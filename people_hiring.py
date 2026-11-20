"""Hiring (/shift/hiring...), part of the People section: job postings and
their publish check, the pipeline, the candidate page with its scoring,
interviews and scorecards, decision notices and the 45-day clock, the
dashboard and the Operator's retention page.

people.py imports this module, calls register(bp) with the People
blueprint before the app registers it, and calls nav_counts() when it
builds the menu. Every route here sits behind the People gate (a signed-in
leader who has entered their PIN again) and needs the "hiring" capability:
the Operator, or a leader the Operator has granted it. Publishing, closing,
the AI switch on a posting, purging, taking a hold off and "add to the
roster" are the Operator's alone.

A person decides every time. Nothing in this file rejects, advances or
ranks an applicant, and the AI assist only ever suggests a level, after
the leader has saved their own.

The candidate page is a scoring screen, so it is never given an
applicant's email, phone number, 18-or-over or work-entitlement answer, or
how they heard of us. Those live on the separate contact page with the
messages and the offer checks.

Nothing here prints a name, a contact detail, an answer or a note.
"""

import re
from urllib.parse import quote

from flask import Response, current_app, flash, redirect, render_template, request, url_for

import hub_text
import people
import people_ai
import people_db
import people_hiring_db as hdb
import shift_db

AI_KIND = "hiring_score"


# ---------------------------------------------------------------------------
# For the menu
# ---------------------------------------------------------------------------

def nav_counts(kind, leader_id, name) -> dict:
    """Counts for the menu badge, for the Operator and hiring leaders only:
    new (applications waiting), notices_due (a decision notice is due
    within a week), notices_overdue."""
    if kind != "operator" and "hiring" not in people_db.capabilities(leader_id, name):
        return {}
    return hdb.counts()


def extra_counts(kind, leader_id, name) -> dict:
    """More for the badge and the People home page, for the same people:
    attention (live postings that no longer pass the publish check, for
    example after a wage floor went up in Settings) and notices_follow_up
    (told "no decision yet", and a decision has since been made)."""
    if kind != "operator" and "hiring" not in people_db.capabilities(leader_id, name):
        return {}
    return {"attention": len(hdb.needs_attention()), "notices_follow_up": hdb.notices_to_follow_up()}


# ---------------------------------------------------------------------------
# AI assist: a suggested level for a written answer, for the leader to check
# ---------------------------------------------------------------------------

AI_TASK = """Task: hiring_score.

"data" holds "criteria" (each with a criterion_id, a name, a definition and level descriptions, lowest level first) and "answers" (each with a criterion_id and what the applicant wrote in answer to that criterion's question). For each answer, compare what the applicant wrote with that criterion's definition and level descriptions, and with nothing else.

Reply with one JSON object in exactly this shape:
{"criteria": [{"criterion_id": 0, "evidence_summary": "one or two sentences on what the answer shows about this criterion", "quote": "a short phrase copied word for word from that answer", "suggested_level": 1, "note": "which level description the answer matches, quoting that description, or what is missing"}], "human_checks": ["only the Human check needed lines from the rules above that apply, copied exactly"]}

Rules for this task, on top of the rules above:
- The answer is the only evidence, and its id is the criterion_id. Include every criterion_id that has an answer, once.
- "quote" must be a phrase of at least three words copied exactly from that answer. If you cannot quote the answer, set "suggested_level" to null.
- "suggested_level" is 1, 2, 3 or 4 (the position of the level description that fits best, lowest first), or null. If the answer does not address the criterion, or there is too little to tell, use null and write "Not enough in this answer to suggest a level."
- Never use how the answer is written: spelling, grammar, length, fluency or dialect. Never guess at the applicant's age, schooling, background or circumstances.
- "human_checks" holds only the "Human check needed: ..." lines from the rules above that apply. Anything else written there is discarded. Use an empty list when none apply.
- One criterion at a time. No total, no average and no overall view. Never say whether the applicant should be interviewed, hired, moved on or turned down."""

# Words in an applicant's own answer that may mean health or accommodation:
# such an answer is never sent to the AI service.
_NEVER_SEND = set(people_ai.LANGUAGE_TERMS["health"]) | {
    "accommodation", "accommodations", "accommodate", "wsib", "workers comp", "sick day",
    "sick days", "called in sick", "bereavement", "human rights"}
_DECISION_WORDS = re.compile(
    r"(?<![\w])(hire|hired|hiring|reject|rejected|rejection|turn(?:ed)? down|shortlist|"
    r"recommend|recommended|recommendation|should be interviewed|move forward|advance)(?![\w])", re.I)
WITHHELD_CHECK = ("Human check needed: an answer was not sent to the AI because it may mention "
                  "health or an accommodation. Read it yourself, and tell the Operator if the "
                  "applicant has asked for an accommodation.")


def _clip(value, cap: int) -> str:
    return " ".join(hub_text.clean(value if isinstance(value, str) else "").split())[:cap]


def _squash(text: str) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.casefold().split())


def _ai_build(payload: dict) -> tuple[str, dict]:
    return AI_TASK, {"criteria": payload["criteria"], "answers": payload["answers"]}


QUOTE_MIN_WORDS = 3
QUOTE_MIN_LETTERS = 12


def _really_quoted(quoted: str, answer: str) -> bool:
    """Is this a real quotation from the answer: at least three words (or
    twelve characters), found in the answer as whole words? A single letter
    or a common word is "in" every answer and proves nothing."""
    quote_text, text = _squash(quoted), _squash(answer)
    if len(quote_text.split()) < QUOTE_MIN_WORDS and len(quote_text) < QUOTE_MIN_LETTERS:
        return False
    return re.search(r"(?<![\w])" + re.escape(quote_text) + r"(?![\w])", text) is not None


def _ai_validate(raw: dict, payload: dict) -> dict:
    """Keep only what can be stood behind: known criteria, a quote that
    really is in the answer (or no level), nothing that reads like a
    decision, every length capped. What our own checks found about one
    answer is kept with that answer ("checks"), so it is shown only where
    the suggestion is. Of the AI's own "human checks" only the fixed lines
    are kept (people_ai.fixed_checks), never its own words."""
    sent = {a["criterion_id"]: a["text"] for a in payload["answers"]}
    items = raw.get("criteria")
    if not isinstance(items, list):
        raise ValueError("no criteria")
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        criterion_id = item.get("criterion_id")
        if isinstance(criterion_id, bool) or not isinstance(criterion_id, int) \
                or criterion_id not in sent or any(o["criterion_id"] == criterion_id for o in out):
            continue
        checks = []
        summary, quoted, note = (_clip(item.get("evidence_summary"), 600), _clip(item.get("quote"), 240),
                                 _clip(item.get("note"), 500))
        level = item.get("suggested_level")
        if isinstance(level, bool) or level not in hdb.LEVELS:
            level = None
        if not quoted or not _really_quoted(quoted, sent[criterion_id]):
            if level is not None or quoted:
                checks.append("Human check needed: the AI did not point to a real phrase from this "
                              "answer, so no level is suggested for it.")
            level, quoted = None, ""
        if _DECISION_WORDS.search(summary) or _DECISION_WORDS.search(note):
            summary = "" if _DECISION_WORDS.search(summary) else summary
            note = "" if _DECISION_WORDS.search(note) else note
            checks.append("Human check needed: part of the AI's wording here read like a hiring "
                          "decision and was left out. The decision is yours.")
        found = sorted({f["category"] for f in people_ai.language_check(f"{summary} {note}")
                        if f["key"] != "consequence"})
        if found:
            checks.append(f"Human check needed: the AI's wording here touches on "
                          f"{', '.join(found).lower()}. None of that may count for or against an applicant.")
        out.append({"criterion_id": criterion_id, "evidence_summary": summary, "quote": quoted,
                    "suggested_level": level, "note": note, "flagged": found, "checks": checks})
    overall = [WITHHELD_CHECK] if payload.get("withheld") else []
    overall += [line for line in people_ai.fixed_checks(raw.get("human_checks"), "answers")
                if line not in overall]
    return {"criteria": out, "human_checks": overall}


def _overall_checks(output: dict) -> list[str]:
    """The check lines that are about the whole request. Only sentences
    our own code wrote: anything else in a stored reply is not shown."""
    ours = {WITHHELD_CHECK, people_ai.CONSEQUENCE_LEFT_OUT, people_ai.OWN_WORDS_LEFT_OUT,
            *(sentence.format(what="answers") for _, sentence in people_ai.FIXED_CHECKS)}
    lines = output.get("human_checks")
    return [line for line in lines if line in ours] if isinstance(lines, list) else []


people_ai.register_kind(AI_KIND, _ai_build, _ai_validate)


def ai_blocked(record: dict) -> str | None:
    """Why AI assist cannot run on this application, or None when it may.
    It needs: the switch in Settings (and a key on the server), AI assist
    on for the posting, a posting that has never been public without its
    AI statement, and an applicant who applied while it was showing."""
    if not people_ai.enabled("hiring"):
        return "AI assist for hiring is switched off in People settings."
    if not record["posting_ai"]:
        return "AI assist is not switched on for this posting."
    if record.get("posting_ai_locked"):
        return hdb.AI_LOCKED
    if not record["ai_disclosed"]:
        return ("This applicant applied while the posting did not say AI is used, so AI assist "
                "can't be used on their application.")
    if record["purged"] or not record["answers"]:
        return "There are no written answers to read."
    return None


def ai_payload(record: dict) -> dict:
    """Exactly what the AI is given: the criteria (name, definition, level
    descriptions) and the written answers with the applicant's own name,
    roster names, email addresses, phone numbers and he/she words taken
    out. Never the name, contact details, availability, how they heard of
    us, their experience, the two yes/no legal answers or anything from an
    interview. An answer that may mention health or an accommodation is
    left out altogether."""
    others = [m["name"] for m in shift_db.roster(include_inactive=True)]
    others += [l["name"] for l in shift_db.leaders(include_inactive=True)]
    subject = [record["name"]]
    criteria, answers, withheld = [], [], []
    for c in record["criteria"]:
        body = record["answers"].get(c["id"])
        if not body:
            continue
        if any(f["term"] in _NEVER_SEND for f in people_ai.language_check(body)):
            withheld.append(c["id"])
            continue
        criteria.append({"criterion_id": c["id"], "name": c["name"], "definition": c["definition"],
                         "anchors": c["anchors"]})
        answers.append({"criterion_id": c["id"],
                        "text": people_ai.deidentify(body, subject, others, "the applicant")})
    return {"criteria": criteria, "answers": answers, "withheld": withheld}


# ---------------------------------------------------------------------------
# Messages: copy-ready text and links that open the leader's own mail or
# text app. Nothing is ever sent from here.
# ---------------------------------------------------------------------------

def _one_line(value) -> str:
    """A value that goes into a message or a link: no line breaks, no
    control characters."""
    return " ".join(hub_text.clean(str(value or "")).replace("\n", " ").split())


def _long_day(stamp) -> str:
    day = hdb._as_date(stamp)
    return f"{day:%B} {day.day}, {day.year}" if day else ""


def message_links(email: str, phone: str, subject: str, body: str) -> dict:
    """mailto: and sms: links with the subject and body URL-encoded, so no
    value typed by an applicant or a leader can add a header or a second
    recipient."""
    email, digits = _one_line(email), re.sub(r"[^0-9+]", "", _one_line(phone))
    return {
        "mailto": (f"mailto:{quote(email, safe='@')}?subject={quote(_one_line(subject), safe='')}"
                   f"&body={quote(body, safe='')}") if email else "",
        "sms": f"sms:{quote(digits, safe='+')}?&body={quote(body, safe='')}" if digits else "",
    }


def messages(record: dict, contact: dict, conf: dict, talks: list[dict]) -> list[dict]:
    """The four messages a leader may need, worded from the legal brief:
    an interview invitation (with the accommodation line), an offer (with
    the accommodation-policies line), not moving forward, and no decision
    yet. "A hiring decision has been made for this posting" is only said to
    someone who was interviewed, because that is who the sentence is owed
    to and the only time this page knows it to be true. Each is {"key",
    "label", "kind" (the notice kind it would be logged as, or None),
    "subject", "body", "mailto", "sms"}."""
    first = _one_line(record["first_name"]) or "there"
    title = _one_line(record["posting_title"])
    employer = _one_line(conf["employer_name"])
    who = _one_line(conf["hiring_contact_name"]) or "A leader"
    reach = _one_line(hdb.contact_line(conf))
    posted = _long_day(record["posting_published"])
    posting = f"{title} (posted {posted})" if posted else title
    met = any(t["outcome"] == "attended" for t in talks)
    thanks = (f"Hi {first}, thank you for interviewing for {posting}." if met
              else f"Hi {first}, thank you for applying for {posting}.")
    decided = ("A hiring decision has been made for this posting, and we will not be moving "
               "forward with your application." if met
               else "We will not be moving forward with your application.")
    planned = next((t for t in talks if t["outcome"] == "scheduled"), None)
    if planned:
        when = _long_day(planned["on_date"]) + (f" at {planned['at_time']}" if planned["at_time"] else "")
        how = {"in person": "in person at the restaurant", "phone": "by phone",
               "video": "by video call"}[planned["mode"]]
        ask = (f"We would like to invite you to an interview on {when}, {how}. Please reply to "
               "confirm, or to ask for another time.")
    else:
        ask = ("We would like to invite you to an interview. Please reply with a few times that "
               "work for you.")
    texts = (
        ("invite", "Interview invitation", None, f"Interview for {title} at {employer}",
         f"Hi {first}, thank you for applying for {title} at {employer}. {ask}\n\n"
         "Accommodations are available on request for the interview and for anything used in "
         f"it. If you need one, please tell {reach}.\n\n{employer}"),
        ("offer", "Offer", "offer", f"Your application for {title} at {employer}",
         f"{thanks} A hiring decision has been made for this posting, and we are pleased to "
         f"offer you the position. {who} will contact you with the details.\n\n"
         f"We have policies for accommodating employees with disabilities. {who} will share "
         f"them with you, and you can ask for an accommodation at any time.\n\n{employer}"),
        ("no", "Not moving forward", "not-moving-forward", f"Your application for {title} at {employer}",
         f"{thanks} {decided} We appreciate the time you gave us and wish you well.\n\n{employer}"),
        ("waiting", "No decision yet", "no-decision-yet", f"Your application for {title} at {employer}",
         f"{thanks} A hiring decision has not yet been made for this posting. We will contact "
         f"you when it has.\n\n{employer}"),
    )
    return [{"key": key, "label": label, "kind": kind, "subject": subject, "body": body,
             **message_links(contact["email"], contact["phone"], subject, body)}
            for key, label, kind, subject, body in texts]


OFFER_REMINDERS = (
    "Ask for proof of age only after a conditional offer.",
    "Minimum age: 14 for serving areas, 15 for the kitchen.",
    "A trial shift is paid work.",
    "Give the new hire their written job information before their first day: the employer's "
    "legal name and operating name; our address, phone number and a contact name; where they "
    "will first work; their starting wage; the pay period and pay day; and the hours to expect "
    "at first.",
    "When you make the offer, tell them about our policies for accommodating employees with "
    "disabilities. The offer message says so.",
    "Ask for a social insurance number only after the offer is accepted.",
    "A police or background check is never part of applying. If one is ever needed, it comes "
    "after a conditional offer and with written consent first: talk to the Operator.",
)


# ---------------------------------------------------------------------------
# Small helpers for the routes
# ---------------------------------------------------------------------------

def _ticked(key: str) -> bool:
    return request.form.get(key) == "1"


def _scorer() -> tuple[str, int | None]:
    who = people.viewer()
    return who.name, who.leader_id


def _log(action: str, object_type: str = "", object_id: int | None = None, detail: str = "") -> None:
    people_db.audit(people.actor(), action, object_type, object_id, detail)


def _say(problems: list[str]) -> None:
    for problem in problems[:8]:
        flash(problem)


def _posting_form(raw) -> dict:
    return {key: raw.get(key, "") for key in ("title", "employment_type", "description", "pay_min",
                                              "pay_max", "student_rate", "vacancy")}


def _criterion_form(raw) -> dict:
    return {"name": raw.get("name", ""), "definition": raw.get("definition", ""),
            "question": raw.get("question", ""), "interview_question": raw.get("interview_question", ""),
            "anchors": [raw.get(f"anchor{n}", "") for n in hdb.LEVELS]}


def _score_entries(criteria: list[dict]) -> dict:
    return {c["id"]: (request.form.get(f"level_{c['id']}", ""), request.form.get(f"note_{c['id']}", ""))
            for c in criteria}


def _note_nudge(*texts: str) -> None:
    """A plain word-list check on what a leader just wrote. It never blocks
    or changes anything: it points back at the guidance."""
    found = sorted({f["category"] for text in texts for f in people_ai.language_check(text)
                    if f["key"] != "consequence"})
    if found:
        flash("Check what you wrote: it uses words about " + ", ".join(found).lower()
              + ". None of that may count for or against an applicant. "
              "See “What to write, and what never to write”.")


def _questions_ok() -> bool:
    """The tick on a criterion form that confirms a flagged question is
    about a guest or the work. It counts only from the Operator."""
    return _ticked("questions_ok") and people.can("operator")


def _questions_ok_logged(posting_id: int, raw: dict, criterion_id: int | None = None) -> None:
    """The activity-log line for a confirmation given on a criterion form."""
    if not _questions_ok():
        return
    which = [label for label, text in (("question for applicants", raw.get("question")),
                                       ("interview question", raw.get("interview_question")))
             if hdb.question_hits(hub_text.clean(text or "").strip())]
    if which:
        _log("question confirmed as about the work", "posting", posting_id,
             (f"criterion {criterion_id}, " if criterion_id else "new criterion, ") + " and ".join(which))


def _tabs(current: str) -> list[dict]:
    items = [("pipeline", "Pipeline", "people.hiring_home"), ("postings", "Postings", "people.hiring_postings"),
             ("notices", "Notices", "people.hiring_notices"), ("metrics", "Numbers", "people.hiring_metrics")]
    if people.can("operator"):
        items.append(("retention", "Retention", "people.hiring_retention"))
    return [{"label": label, "url": url_for(endpoint), "on": key == current} for key, label, endpoint in items]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(bp) -> None:  # noqa: C901 (one flat list of small views)
    hiring = people.requires("hiring")
    operator = people.requires("operator")

    def missing(endpoint: str = "people.hiring_home"):
        return people.deny(endpoint)

    def to_posting(posting_id: int, anchor: str = ""):
        return redirect(url_for("people.hiring_posting", posting_id=posting_id) + anchor)

    def to_candidate(application_id: int, anchor: str = ""):
        return redirect(url_for("people.hiring_candidate", application_id=application_id) + anchor)

    def to_contact(application_id: int, anchor: str = ""):
        return redirect(url_for("people.hiring_contact", application_id=application_id) + anchor)

    # --- the pipeline ------------------------------------------------------

    @bp.route("/hiring/", strict_slashes=False)
    @hiring
    def hiring_home():
        return render_template("people/hiring/pipeline.html", tabs=_tabs("pipeline"),
                               postings=hdb.pipeline(), counts=hdb.counts(),
                               follow_up=hdb.notices_to_follow_up(),
                               stage_labels=hdb.STAGE_LABELS, active_stages=hdb.ACTIVE_STAGES,
                               stages=hdb.STAGES, attention=hdb.needs_attention(),
                               drafts=sum(1 for p in hdb.postings() if p["status"] == "draft"))

    # --- postings ----------------------------------------------------------

    @bp.route("/hiring/postings")
    @hiring
    def hiring_postings():
        rows = hdb.postings()
        for row in rows:
            row["pay"] = hdb.pay_line(row)
        return render_template("people/hiring/postings.html", tabs=_tabs("postings"), postings=rows,
                               types=hdb.EMPLOYMENT_TYPES)

    def posting_form(posting, fields, problems, status=200):
        return render_template("people/hiring/posting_form.html", tabs=_tabs("postings"),
                               posting=posting, f=fields, problems=problems,
                               types=hdb.EMPLOYMENT_TYPES, vacancies=hdb.VACANCY_CHOICES,
                               conf=hdb.settings(), limits=hdb), status

    @bp.route("/hiring/postings/new", methods=["GET", "POST"])
    @hiring
    def hiring_posting_new():
        if request.method == "GET":
            return posting_form(None, {"employment_type": "part-time"}, [])
        fields = _posting_form(request.form)
        posting_id, problems = hdb.create_posting(fields, people.actor())
        if problems:
            return posting_form(None, fields, problems, 400)
        _log("posting created", "posting", posting_id)
        flash("Draft saved. Add what you will score applications on next.")
        return to_posting(posting_id, "#criteria")

    @bp.route("/hiring/postings/<int:posting_id>")
    @hiring
    def hiring_posting(posting_id):
        posting = hdb.get_posting(posting_id)
        if not posting:
            return missing("people.hiring_postings")
        conf = hdb.settings()
        view = hdb.public_view(posting, conf)
        model = people_db.active_model()
        used = {c["dimension_key"] for c in posting["criteria"] if c["dimension_key"]}
        return render_template(
            "people/hiring/posting.html", tabs=_tabs("postings"), posting=posting, view=view,
            body=hub_text.render(view["description"]),
            problems=hdb.compliance_problems(posting) if posting["status"] != "closed" else [],
            question_blocks=hdb.question_blocks(posting) if posting["status"] != "closed" else [],
            question_ok_label=hdb.QUESTION_OK_LABEL,
            flags=hdb.wording_flags(posting), copies=hdb.snapshots(posting_id),
            standards=[d for d in (model["dimensions"] if model else []) if d["key"] not in used],
            has_model=bool(model), ai_setting=people_db.setting_on("ai_hiring"), limits=hdb,
            wages_to_check=people_db.wages_to_check() if posting["status"] != "closed" else [],
            public_url=url_for("careers.posting", slug=posting["slug"], _external=True)
            if "careers" in current_app.blueprints else "")

    @bp.route("/hiring/postings/<int:posting_id>/edit", methods=["GET", "POST"])
    @hiring
    def hiring_posting_edit(posting_id):
        posting = hdb.get_posting(posting_id)
        if not posting:
            return missing("people.hiring_postings")
        if posting["status"] == "closed":
            flash("That posting is closed, so it can't be changed. Copy it to a new draft instead.")
            return to_posting(posting_id)
        if request.method == "GET":
            fields = dict(posting)
            for key in ("pay_min", "pay_max"):
                fields[key] = hdb.money(posting[key]).lstrip("$")
            return posting_form(posting, fields, [])
        fields = _posting_form(request.form)
        problems = hdb.save_posting(posting_id, fields, people.actor(), reviewed=_ticked("wording_reviewed"))
        if problems:
            return posting_form(posting, fields, problems, 400)
        _log("posting changed", "posting", posting_id)
        flash("Saved, and a copy of the new version was kept." if posting["status"] == "open" else "Saved.")
        return to_posting(posting_id)

    @bp.route("/hiring/postings/<int:posting_id>/criteria", methods=["POST"])
    @hiring
    def hiring_criterion_add(posting_id):
        key = request.form.get("dimension_key", "")
        if key:
            model = people_db.active_model()
            dim = next((d for d in (model["dimensions"] if model else []) if d["key"] == key), None)
            if not dim:
                flash("That standard isn't in Our standards any more.")
                return to_posting(posting_id, "#criteria")
            raw = {"name": dim["name"], "definition": dim["definition"], "dimension_key": dim["key"],
                   "question": dim["hiring"]["question"] if dim["hiring"] else "",
                   "anchors": dim["hiring"]["anchors"] if dim["hiring"] else []}
        else:
            raw = _criterion_form(request.form)
        problems = hdb.add_criterion(posting_id, raw, people.actor(), reviewed=_ticked("wording_reviewed"),
                                     questions_ok=_questions_ok())
        if problems:
            _say(problems)
        else:
            _log("criterion added", "posting", posting_id)
            flash("Criterion added.")
            _questions_ok_logged(posting_id, raw)
        return to_posting(posting_id, "#criteria")

    @bp.route("/hiring/postings/<int:posting_id>/criteria/<int:criterion_id>", methods=["POST"])
    @hiring
    def hiring_criterion_save(posting_id, criterion_id):
        raw = _criterion_form(request.form)
        problems = hdb.save_criterion(criterion_id, posting_id, raw, people.actor(),
                                      reviewed=_ticked("wording_reviewed"), questions_ok=_questions_ok())
        if problems:
            _say(problems)
        else:
            _log("criterion changed", "posting", posting_id)
            flash("Criterion saved.")
            _questions_ok_logged(posting_id, raw, criterion_id)
        return to_posting(posting_id, f"#c{criterion_id}")

    @bp.route("/hiring/postings/<int:posting_id>/criteria/<int:criterion_id>/question-ok", methods=["POST"])
    @operator
    def hiring_question_ok(posting_id, criterion_id):
        """The Operator's own call, one question at a time, and logged: this
        flagged question is about a guest or the work, not the applicant."""
        if not _ticked("confirm"):
            flash("Tick the box to confirm the question is about a guest or the work, not about "
                  "the applicant. Or reword the question.")
        elif hdb.confirm_question(posting_id, criterion_id, request.form.get("which", ""), people.actor()):
            flash("Confirmed. If the question is changed, it is checked again.")
        else:
            flash("There is nothing to confirm on that question any more.")
        return to_posting(posting_id, "#check")

    @bp.route("/hiring/postings/<int:posting_id>/criteria/<int:criterion_id>/remove", methods=["POST"])
    @hiring
    def hiring_criterion_remove(posting_id, criterion_id):
        problems = hdb.remove_criterion(criterion_id, posting_id, people.actor())
        if problems:
            _say(problems)
        else:
            _log("criterion removed", "posting", posting_id)
            flash("Criterion removed.")
        return to_posting(posting_id, "#criteria")

    @bp.route("/hiring/postings/<int:posting_id>/publish", methods=["POST"])
    @operator
    def hiring_publish(posting_id):
        problems = hdb.publish(posting_id, people.actor(), reviewed=_ticked("wording_reviewed"))
        if problems:
            _say(problems)
            return to_posting(posting_id, "#check")
        _log("posting published", "posting", posting_id)
        flash("Published. It is on the careers page now, and an exact copy was kept.")
        return to_posting(posting_id)

    @bp.route("/hiring/postings/<int:posting_id>/close", methods=["POST"])
    @operator
    def hiring_close(posting_id):
        if not _ticked("confirm"):
            flash("Tick the box to close the posting. A closed posting can't be reopened.")
        elif hdb.close_posting(posting_id, people.actor()):
            _log("posting closed", "posting", posting_id)
            flash("Closed. It is off the careers page, and a copy was kept with today's date.")
        else:
            flash("That posting isn't open.")
        return to_posting(posting_id)

    @bp.route("/hiring/postings/<int:posting_id>/ai", methods=["POST"])
    @operator
    def hiring_posting_ai(posting_id):
        on = request.form.get("on") == "1"
        posting = hdb.get_posting(posting_id)
        if on and not people_db.setting_on("ai_hiring"):
            flash("Switch on AI assist for hiring in People settings first.")
        elif on and posting and posting["status"] == "open":
            flash(hdb.AI_DRAFT_ONLY)
        elif hdb.set_ai(posting_id, on, people.actor()):
            _log("posting AI switched " + ("on" if on else "off"), "posting", posting_id)
            flash("AI assist is off for this posting." if not on
                  else "AI assist is on for this posting. It will say so from the day it goes live."
                  if people_ai.enabled("hiring")
                  else "Switched on for this posting. It can be published once an AI key is set up "
                       "on the server.")
        else:
            flash("Nothing changed.")
        return to_posting(posting_id, "#ai")

    @bp.route("/hiring/postings/<int:posting_id>/copy", methods=["POST"])
    @hiring
    def hiring_posting_copy(posting_id):
        new_id = hdb.copy_posting(posting_id, people.actor())
        if not new_id:
            return missing("people.hiring_postings")
        _log("posting copied", "posting", new_id, f"from {posting_id}")
        flash("Copied to a new draft.")
        return to_posting(new_id)

    @bp.route("/hiring/postings/<int:posting_id>/copies/<int:snapshot_id>")
    @hiring
    def hiring_snapshot(posting_id, snapshot_id):
        posting, copy = hdb.get_posting(posting_id), hdb.get_snapshot(snapshot_id)
        if not posting or not copy or copy["posting_id"] != posting_id:
            return missing("people.hiring_postings")
        return render_template("people/hiring/snapshot.html", tabs=_tabs("postings"), posting=posting,
                               copy=copy)

    @bp.route("/hiring/records", methods=["POST"])
    @operator
    def hiring_records():
        _log("hiring records downloaded")
        resp = Response(hdb.records_export(), mimetype="application/json")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="hiring-records-{shift_db.now_local():%Y-%m-%d}.json"')
        return resp

    # --- one candidate -----------------------------------------------------

    def ai_view(record: dict, scores: dict) -> dict:
        """What the candidate page shows of the AI assist. A suggestion for
        a criterion, and anything our checks found about it, is included
        only once this leader has saved their own level for that answer."""
        blocked = ai_blocked(record)
        job = None if blocked else people_ai.latest_job(AI_KIND, record["id"])
        out = {"blocked": blocked, "status": job["status"] if job else None,
               "error": job["error"] if job else None, "by": job["requested_by"] if job else "",
               "at": job["finished_at"] if job else "", "suggestions": {}, "checks": [], "waiting": 0}
        if job and job["status"] == "done" and job["output"]:
            for item in job["output"].get("criteria", []):
                if item["criterion_id"] in scores["mine"]:
                    out["suggestions"][item["criterion_id"]] = item
                else:
                    out["waiting"] += 1
            # Fixed sentences only, none of them about one answer or a
            # level, so they can show at once (one says an answer was not
            # sent and needs reading by a person).
            out["checks"] = _overall_checks(job["output"])
        return out

    @bp.route("/hiring/candidates/<int:application_id>")
    @hiring
    def hiring_candidate(application_id):
        record = hdb.candidate(application_id)
        if not record:
            return missing()
        who = people.viewer()
        _log("candidate opened", "application", application_id)
        scores = hdb.scores_for(application_id, who.name, who.leader_id, who.is_operator)
        talks = hdb.interviews(application_id)
        return render_template(
            "people/hiring/candidate.html", tabs=_tabs("pipeline"), c=record, scores=scores, talks=talks,
            has_notes=any(note for card in scores["cards"] for note in card["notes"].values()),
            notes=hdb.notes(application_id), history=hdb.events(application_id),
            moves=hdb.allowed_moves(record["stage"], who.is_operator) if not record["purged"] else [],
            reasons=hdb.NOT_SELECTED_REASONS, stage_labels=hdb.STAGE_LABELS,
            outcome_labels=hdb.OUTCOME_LABELS, modes=hdb.INTERVIEW_MODES, days=hdb.DAYS,
            dayparts=hdb.DAYPARTS, levels=hdb.LEVELS, clocks=hdb.clocks_for(application_id),
            ai=ai_view(record, scores), today=shift_db.now_local().date().isoformat(), limits=hdb)

    def acting_on(application_id):
        """The application a form post is about, or None (already answered
        with the one message for "not there")."""
        return hdb.candidate(application_id)

    @bp.route("/hiring/candidates/<int:application_id>/stage", methods=["POST"])
    @hiring
    def hiring_stage(application_id):
        record = acting_on(application_id)
        if not record:
            return missing()
        to = request.form.get("to", "")
        problem = hdb.move_stage(application_id, request.form.get("from", ""), to, people.actor(),
                                 reason=request.form.get("reason", ""),
                                 checks_done=_ticked("checks_done"), operator=people.can("operator"))
        if problem:
            flash(problem)
            return to_candidate(application_id, "#stage")
        _log("stage changed", "application", application_id, to)
        flash(f"Moved to {hdb.STAGE_LABELS[to].lower()}.")
        if to == "offer":                        # the offer message and the offer checks are there
            return to_contact(application_id, "#offer-checks")
        return to_candidate(application_id)

    @bp.route("/hiring/candidates/<int:application_id>/note", methods=["POST"])
    @hiring
    def hiring_note(application_id):
        body = request.form.get("body", "")
        if hdb.add_note(application_id, body, people.actor()):
            _log("note added", "application", application_id)
            flash("Note saved.")
            _note_nudge(body)
        else:
            flash("Write the note first.")
        return to_candidate(application_id, "#notes")

    @bp.route("/hiring/candidates/<int:application_id>/contacted", methods=["POST"])
    @hiring
    def hiring_contacted(application_id):
        if hdb.mark_contacted(application_id, request.form.get("method", ""), people.actor()):
            _log("contact recorded", "application", application_id)
            flash("Recorded that you reached out.")
        else:
            flash("Choose how you reached out.")
        return to_contact(application_id, "#messages")

    @bp.route("/hiring/candidates/<int:application_id>/score", methods=["POST"])
    @hiring
    def hiring_score(application_id):
        record = acting_on(application_id)
        if not record:
            return missing()
        name, leader_id = _scorer()
        entries = _score_entries(record["criteria"])
        saved = hdb.save_answer_scores(application_id, name, leader_id, entries)
        _log("answers scored", "application", application_id)
        flash(f"Saved your level for {saved} answer{'' if saved == 1 else 's'}." if saved
              else "No levels saved. Choose a level for an answer first.")
        _note_nudge(*(note for _, note in entries.values()))
        return to_candidate(application_id, "#answers")

    @bp.route("/hiring/candidates/<int:application_id>/interview", methods=["POST"])
    @hiring
    def hiring_interview(application_id):
        problem = hdb.add_interview(application_id, request.form.get("on_date", ""),
                                    request.form.get("at_time", ""), request.form.get("mode", ""),
                                    request.form.get("interviewer", ""), people.actor())
        if problem:
            flash(problem)
        else:
            _log("interview added", "application", application_id)
            flash("Interview added. Record what happened once it has been held.")
        return to_candidate(application_id, "#interviews")

    @bp.route("/hiring/candidates/<int:application_id>/interviews/<int:interview_id>/outcome",
              methods=["POST"])
    @hiring
    def hiring_outcome(application_id, interview_id):
        outcome = request.form.get("outcome", "")
        problem = hdb.set_interview_outcome(interview_id, application_id, outcome, people.actor(),
                                            confirmed=_ticked("not_held"), operator=people.can("operator"))
        if problem:
            flash(problem)
        else:
            _log("interview outcome", "application", application_id, outcome)
            flash("Recorded. The applicant must be told where things stand within 45 days."
                  if outcome == "attended" else "Recorded.")
        return to_candidate(application_id, f"#i{interview_id}")

    @bp.route("/hiring/candidates/<int:application_id>/interviews/<int:interview_id>/scorecard",
              methods=["POST"])
    @hiring
    def hiring_scorecard(application_id, interview_id):
        record = acting_on(application_id)
        if not record:
            return missing()
        name, leader_id = _scorer()
        submit = request.form.get("action") == "submit"
        entries = _score_entries(record["criteria"])
        problem = hdb.save_scorecard(interview_id, application_id, name, leader_id, entries, submit)
        if problem:
            flash(problem)
        else:
            _log("scorecard submitted" if submit else "scorecard saved", "application", application_id)
            flash("Scorecard submitted. It can't be changed now." if submit
                  else "Draft saved. Submit it when you are done.")
            _note_nudge(*(note for _, note in entries.values()))
        return to_candidate(application_id, f"#i{interview_id}")

    @bp.route("/hiring/candidates/<int:application_id>/hold", methods=["POST"])
    @hiring
    def hiring_hold(application_id):
        on = request.form.get("on") == "1"
        if not on and not people.can("operator"):
            flash("Only the Operator can take a hold off.")
        elif hdb.set_hold(application_id, on, people.actor()):
            _log("hold " + ("on" if on else "off"), "application", application_id)
            flash("On hold: it will not be deleted." if on else "Hold taken off.")
        else:
            flash("Nothing changed.")
        return to_contact(application_id, "#records")

    @bp.route("/hiring/candidates/<int:application_id>/ai", methods=["POST"])
    @hiring
    def hiring_ai(application_id):
        record = acting_on(application_id)
        if not record:
            return missing()
        blocked = ai_blocked(record)
        if blocked:
            flash(blocked)
            return to_candidate(application_id, "#answers")
        payload = ai_payload(record)
        if not payload["answers"]:
            flash("None of the answers could be sent to the AI. Score them yourself.")
            return to_candidate(application_id, "#answers")
        people_ai.start_job(AI_KIND, application_id, payload, people.actor(),
                            wait=bool(current_app.config.get("TESTING")))
        hdb.log_event(application_id, "ai", people.actor(), "suggestion asked")
        _log("AI suggestion asked", "application", application_id)
        flash("Asked. A suggestion shows beside each answer once you have saved your own level for it.")
        return to_candidate(application_id, "#answers")

    # --- contact, messages, notices and the offer checks ------------------

    @bp.route("/hiring/candidates/<int:application_id>/contact")
    @hiring
    def hiring_contact(application_id):
        record = hdb.candidate(application_id)
        contact = hdb.candidate_contact(application_id)
        if not record or not contact:
            return missing()
        _log("contact details opened", "application", application_id)
        conf = hdb.settings()
        talks = hdb.interviews(application_id)
        return render_template(
            "people/hiring/contact.html", tabs=_tabs("pipeline"), c=record, contact=contact,
            heard=hdb.HEARD_LABELS.get(contact["heard_from"], ""),
            messages=[] if record["purged"] else messages(record, contact, conf, talks),
            notices=hdb.notices(application_id), clocks=hdb.clocks_for(application_id),
            notice_kinds=hdb.NOTICE_KINDS, methods=hdb.NOTICE_METHODS,
            contact_methods=hdb.CONTACT_METHODS, offer=hdb.offer_state(application_id),
            reminders=OFFER_REMINDERS, stage_labels=hdb.STAGE_LABELS,
            active_stages=hdb.ACTIVE_STAGES, same_contact=record["same_contact"],
            on_roster=any(m["name"].casefold() == record["name"].casefold()
                          for m in shift_db.roster(include_inactive=True)),
            today=shift_db.now_local().date().isoformat(), limits=hdb)

    @bp.route("/hiring/candidates/<int:application_id>/more-time", methods=["POST"])
    @hiring
    def hiring_more_time(application_id):
        until = hdb.give_more_time(application_id, people.actor())
        if until:
            _log("more time given", "application", application_id)
            flash("Their link is open again. Let them know they can use it.")
        else:
            flash("There is nothing to reopen: the questions are answered, or the application is "
                  "no longer open.")
        return to_contact(application_id, "#questions")

    @bp.route("/hiring/candidates/<int:application_id>/answers", methods=["POST"])
    @hiring
    def hiring_enter_answers(application_id):
        record = acting_on(application_id)
        if not record:
            return missing()
        typed = {c["id"]: request.form.get(f"q_{c['id']}", "") for c in record["criteria"] if c["question"]}
        result = hdb.save_answers(application_id, typed, entered_by=people.actor())
        if result == "saved":
            _log("answers entered for the applicant", "application", application_id)
            flash("Answers saved. They are scored like any others.")
            return to_candidate(application_id, "#answers")
        flash("Write the answer to at least one question." if result == "empty"
              else "The questions are already answered, or the application is no longer open.")
        return to_contact(application_id, "#questions")

    @bp.route("/hiring/candidates/<int:application_id>/correct", methods=["POST"])
    @hiring
    def hiring_correct(application_id):
        problems = hdb.correct_details(application_id, {key: request.form.get(key, "") for key in
                                                        ("first_name", "last_name", "email", "phone")},
                                       people.actor())
        if problems:
            _say(problems)
        else:
            _log("details corrected", "application", application_id)
            flash("Corrected.")
        return to_contact(application_id, "#reach")

    @bp.route("/hiring/candidates/<int:application_id>/notice", methods=["POST"])
    @hiring
    def hiring_notice(application_id):
        kind = request.form.get("kind", "")
        problem = hdb.log_notice(application_id, kind, request.form.get("method", ""),
                                 request.form.get("said", ""), request.form.get("given_on", ""),
                                 people.actor())
        if problem:
            flash(problem)
        else:
            _log("notice logged", "application", application_id, kind)
            flash("Notice logged. It is kept for three years.")
        return to_contact(application_id, "#notices")

    @bp.route("/hiring/candidates/<int:application_id>/offer", methods=["POST"])
    @hiring
    def hiring_offer(application_id):
        problem = hdb.log_offer(application_id, people.actor())
        if problem:
            flash(problem)
        else:
            _log("offer recorded", "application", application_id)
            flash("Offer recorded. If they were interviewed, log the offer as a decision notice too.")
        return to_contact(application_id, "#offer")

    @bp.route("/hiring/candidates/<int:application_id>/offer-reply", methods=["POST"])
    @hiring
    def hiring_offer_reply(application_id):
        reply = request.form.get("reply", "")
        problem = hdb.log_offer_reply(application_id, reply, people.actor())
        if problem:
            flash(problem)
        else:
            _log("offer answered", "application", application_id, reply)
            flash("Recorded. Move them to hired yourself once the offer checks are done."
                  if reply == "accepted" else "Recorded.")
        return to_contact(application_id, "#offer")

    @bp.route("/hiring/candidates/<int:application_id>/roster", methods=["POST"])
    @operator
    def hiring_roster(application_id):
        record = hdb.candidate(application_id)
        if not record:
            return missing()
        if record["stage"] != "hired" or record["purged"]:
            flash("Only someone who has been hired can be added to the roster.")
            return to_contact(application_id, "#roster")
        name = " ".join(record["name"].split())
        if any(m["name"].casefold() == name.casefold() for m in shift_db.roster(include_inactive=True)):
            flash("That name is already on the Team Hub roster.")
        elif shift_db.add_member(name, touch_existing=False):
            # Only the name crosses into Team Hub. Nothing else about the
            # application ever leaves the People database.
            hdb.log_event(application_id, "roster", people.actor(), "added")
            _log("added to roster", "application", application_id)
            flash("Added to the Team Hub roster. Only their name was copied.")
        else:
            flash("That name couldn't be added.")
        return to_contact(application_id, "#roster")

    # --- notices, numbers, retention --------------------------------------

    @bp.route("/hiring/notices")
    @hiring
    def hiring_notices():
        board = hdb.notice_board()
        return render_template("people/hiring/notices.html", tabs=_tabs("notices"), board=board,
                               notice_kinds=hdb.NOTICE_KINDS, days=hdb.NOTICE_DAYS,
                               soon=hdb.NOTICE_SOON_DAYS, aim=hdb.NOTICE_AIM_DAYS,
                               stage_labels=hdb.STAGE_LABELS)

    @bp.route("/hiring/metrics")
    @hiring
    def hiring_metrics():
        raw_days, raw_posting = request.args.get("days", ""), request.args.get("posting", "")
        days = int(raw_days) if raw_days in ("30", "90", "365") else 90
        rows = [p for p in hdb.postings() if p["status"] != "draft"]
        posting_id = int(raw_posting) if raw_posting.isascii() and raw_posting.isdigit() \
            and len(raw_posting) <= 9 and int(raw_posting) in {p["id"] for p in rows} else None
        return render_template("people/hiring/metrics.html", tabs=_tabs("metrics"),
                               m=hdb.metrics(posting_id, days), postings=rows, days=days,
                               posting_id=posting_id, periods=hdb.METRIC_PERIODS, small=hdb.SMALL_NUMBER)

    @bp.route("/hiring/retention")
    @operator
    def hiring_retention():
        return render_template("people/hiring/retention.html", tabs=_tabs("retention"),
                               r=hdb.retention(), months=people_db.get_setting("retention_months"),
                               stage_labels=hdb.STAGE_LABELS)

    @bp.route("/hiring/retention/purge", methods=["POST"])
    @operator
    def hiring_purge():
        if not _ticked("confirm"):
            flash("Tick the box to remove these applicants' details. It can't be undone.")
            return redirect(url_for("people.hiring_retention"))
        count, notices = hdb.purge_all(people.actor())
        _log("applications purged", "", None, f"{count} applications" + (f", {notices} old notices" if notices else ""))
        said = []
        if count:
            said.append(f"Removed the personal details of {count} application{'' if count == 1 else 's'}.")
        if notices:
            said.append(f"Removed the wording of {notices} decision notice{'' if notices == 1 else 's'} "
                        "more than three years old.")
        flash(" ".join(said) or "Nothing was due to be removed.")
        return redirect(url_for("people.hiring_retention"))
