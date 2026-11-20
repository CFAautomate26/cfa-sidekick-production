"""Storage for hiring (the People section): job postings and the exact
copies kept of them, applications, quick-question answers, scores,
interviews, decision notices, notes, and the event log every hiring number
is worked out from.

Everything here lives in the People database (people_db.connect()), never
in the shift database or its backups. people_db.init_db() calls
init_schema(conn) at every boot.

Three things this file is strict about, because the law is:

* A posting is a legal document. It cannot go live while the publish check
  (compliance_problems) finds anything, and an exact copy of what was
  shown, with the blank application form, is kept every time it is
  published, changed or closed (hr_snapshots). Nothing here deletes a
  posting or a snapshot.
* Every interview that was held starts a 45-day clock for telling the
  applicant where things stand (notice_obligations).
* The purge takes an applicant's personal details out and keeps the bare
  row, its history and the decision notices the law says to keep for
  three years.

Nothing in this file moves, rejects or ranks an applicant on its own.
"""

import json
import re
from contextlib import closing
from datetime import date, datetime, timedelta
from statistics import median

import hub_text
import people_ai
import people_db
import shift_db

SCHEMA = """
-- A job posting. Wording about the job only: no personal content beyond the
-- sign-in names of the leaders who wrote, published and closed it.
CREATE TABLE IF NOT EXISTS hr_postings (
  id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  employment_type TEXT NOT NULL DEFAULT 'part-time',
  description TEXT NOT NULL DEFAULT '',
  pay_min REAL, pay_max REAL, student_rate INTEGER NOT NULL DEFAULT 0,
  vacancy TEXT NOT NULL DEFAULT '',
  ai_assist INTEGER NOT NULL DEFAULT 0, wording_reviewed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, published_at TEXT, published_by TEXT,
  closed_at TEXT, closed_by TEXT);

-- What an application is scored on. dimension_key links to "Our standards"
-- when the criterion was picked from it. No personal content.
CREATE TABLE IF NOT EXISTS hr_criteria (
  id INTEGER PRIMARY KEY, posting_id INTEGER NOT NULL REFERENCES hr_postings(id) ON DELETE CASCADE,
  sort INTEGER NOT NULL, name TEXT NOT NULL, dimension_key TEXT, definition TEXT NOT NULL DEFAULT '',
  anchors_json TEXT NOT NULL DEFAULT '[]', question TEXT NOT NULL DEFAULT '',
  interview_question TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
  -- The exact wording of a question the Operator confirmed is about a guest
  -- or the work, not about the applicant (see question_hits). A question
  -- that is changed afterwards no longer matches and is checked again.
  question_ok TEXT NOT NULL DEFAULT '', interview_ok TEXT NOT NULL DEFAULT '');

-- Exact copies kept for the three-year record: the posting as shown, plus
-- the blank application form. Personal content: only the hiring contact's
-- name and work contact details, as they were printed on the posting.
-- Never deleted by any routine.
CREATE TABLE IF NOT EXISTS hr_snapshots (
  id INTEGER PRIMARY KEY, posting_id INTEGER NOT NULL REFERENCES hr_postings(id) ON DELETE CASCADE,
  taken_at TEXT NOT NULL, taken_by TEXT NOT NULL, reason TEXT NOT NULL,
  ai_statement INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL);

-- One applicant's application. Personal content: name, email, phone, the
-- two yes/no legal answers, availability, how they heard of us, a
-- referrer's name and their own account of their experience. The purge
-- blanks all of it; the row and its events stay. token_hash is a keyed
-- hash of the applicant's private link, never the link itself.
-- keep_months is the number of months the applicant was told it is kept.
-- duplicate_of is the id of an earlier application to the same posting
-- that gave the same email or phone number (a flag for a leader, no more).
CREATE TABLE IF NOT EXISTS hr_applications (
  id INTEGER PRIMARY KEY, posting_id INTEGER NOT NULL REFERENCES hr_postings(id) ON DELETE CASCADE,
  first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
  contact_key TEXT NOT NULL DEFAULT '',
  work_entitled TEXT NOT NULL DEFAULT '', adult TEXT NOT NULL DEFAULT '',
  availability TEXT NOT NULL DEFAULT '{}', heard_from TEXT NOT NULL DEFAULT '',
  referrer TEXT NOT NULL DEFAULT '', experience TEXT NOT NULL DEFAULT '',
  ai_disclosed INTEGER NOT NULL DEFAULT 0, consent_at TEXT NOT NULL,
  token_hash TEXT NOT NULL, answers_until TEXT NOT NULL, answered_at TEXT,
  stage TEXT NOT NULL DEFAULT 'new', stage_at TEXT NOT NULL, hold INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, purged_at TEXT, purged_by TEXT,
  keep_months INTEGER NOT NULL DEFAULT 24, duplicate_of INTEGER);

-- An applicant's written answers to the quick questions. Personal content:
-- whatever they wrote. Deleted by the purge.
CREATE TABLE IF NOT EXISTS hr_answers (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  criterion_id INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE (application_id, criterion_id));

-- The event log every metric is computed from. Personal content: the
-- sign-in name of the leader who acted. `detail` holds fixed labels only.
CREATE TABLE IF NOT EXISTS hr_events (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  posting_id INTEGER NOT NULL, at TEXT NOT NULL, by TEXT NOT NULL, kind TEXT NOT NULL,
  from_stage TEXT, to_stage TEXT, detail TEXT NOT NULL DEFAULT '');

-- A leader's level for one criterion, from the written answers or from an
-- interview. Personal content: the scorer's sign-in name and their note
-- about what the applicant said. The purge blanks the note.
CREATE TABLE IF NOT EXISTS hr_scores (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  criterion_id INTEGER NOT NULL, source TEXT NOT NULL,
  interview_id INTEGER, scorer_name TEXT NOT NULL, scorer_leader_id INTEGER,
  level INTEGER, note TEXT NOT NULL DEFAULT '', submitted INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL);

-- An interview: when, how, who led it and whether it happened. Personal
-- content: the interviewer's name.
CREATE TABLE IF NOT EXISTS hr_interviews (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  on_date TEXT NOT NULL, at_time TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'in person',
  interviewer TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT 'scheduled',
  outcome_at TEXT, outcome_by TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

-- What an interviewed applicant was told and when. Personal content: the
-- applicant's name (given_to) and the words they were given. Kept at least
-- three years from the day it was given; never purged early.
CREATE TABLE IF NOT EXISTS hr_notices (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, method TEXT NOT NULL, said TEXT NOT NULL, given_on TEXT NOT NULL,
  logged_at TEXT NOT NULL, logged_by TEXT NOT NULL, given_to TEXT NOT NULL DEFAULT '');

-- A leader's note on an application. Personal content: whatever the leader
-- wrote about the applicant. Deleted by the purge.
CREATE TABLE IF NOT EXISTS hr_notes (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES hr_applications(id) ON DELETE CASCADE,
  body TEXT NOT NULL, by TEXT NOT NULL, at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_hr_criteria_posting ON hr_criteria (posting_id, sort, id);
CREATE INDEX IF NOT EXISTS idx_hr_snapshots_posting ON hr_snapshots (posting_id, id);
CREATE INDEX IF NOT EXISTS idx_hr_applications_posting ON hr_applications (posting_id, stage);
CREATE INDEX IF NOT EXISTS idx_hr_applications_token ON hr_applications (token_hash);
CREATE INDEX IF NOT EXISTS idx_hr_events_application ON hr_events (application_id, id);
CREATE INDEX IF NOT EXISTS idx_hr_scores_application ON hr_scores (application_id, criterion_id);
CREATE INDEX IF NOT EXISTS idx_hr_interviews_application ON hr_interviews (application_id, on_date);
CREATE INDEX IF NOT EXISTS idx_hr_notices_application ON hr_notices (application_id, given_on);
CREATE INDEX IF NOT EXISTS idx_hr_notes_application ON hr_notes (application_id, id);
"""


# Columns added after a store may already have been created.
_ADDED_COLUMNS = (("hr_applications", "duplicate_of", "INTEGER"),
                  ("hr_criteria", "question_ok", "TEXT NOT NULL DEFAULT ''"),
                  ("hr_criteria", "interview_ok", "TEXT NOT NULL DEFAULT ''"))


def init_schema(conn) -> None:
    conn.executescript(SCHEMA)
    for table, column, declared in _ADDED_COLUMNS:               # our own names, never input
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declared}")


# ---------------------------------------------------------------------------
# Fixed lists
# ---------------------------------------------------------------------------

EMPLOYMENT_TYPES = {"part-time": "Part-time", "full-time": "Full-time",
                    "either": "Part-time or full-time"}
VACANCY_STATEMENTS = {
    "existing": "This posting is for an existing vacancy.",
    "future": "This posting is not for an existing vacancy. We are collecting applications "
              "for future openings.",
}
VACANCY_CHOICES = {"existing": "An existing vacancy",
                   "future": "Collecting applications for future openings"}
POSTING_STATUSES = ("draft", "open", "closed")
SNAPSHOT_REASONS = ("published", "changed", "closed")

MAIN_LINE = ("new", "screening", "interview", "offer", "hired")
ACTIVE_STAGES = MAIN_LINE[:4]
DECIDED_STAGES = ("not-selected", "withdrawn")
STAGES = MAIN_LINE + DECIDED_STAGES
STAGE_LABELS = {"new": "New", "screening": "Screening", "interview": "Interview", "offer": "Offer",
                "hired": "Hired", "not-selected": "Not selected", "withdrawn": "Withdrew"}
# The only reasons a leader can record for "not selected": each is about the
# job. The words are stored as the event's detail.
NOT_SELECTED_REASONS = (
    "Availability doesn't fit the shifts we need",
    "Answers showed less fit with our standards than other applicants",
    "Position filled",
    "Did not attend interview",
    "Could not reach the applicant",
    "Applicant is not legally able to work in Canada",
)
OFFER_CHECKS_LABEL = "offer checks confirmed"

DAYS = (("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"),
        ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday"))
DAYPARTS = (("morning", "Morning"), ("afternoon", "Afternoon"), ("evening", "Evening"))
HEARD_FROM = (
    ("walk-in", "Walked in or scanned a code in the restaurant"),
    ("friend", "A friend or family member who works here"),
    ("job-site", "Indeed or another job site"),
    ("social", "Social media"),
    ("school", "School"),
    ("other", "Other"),
)
HEARD_LABELS = dict(HEARD_FROM)

INTERVIEW_MODES = ("in person", "phone", "video")
INTERVIEW_OUTCOMES = ("scheduled", "attended", "no-show", "cancelled", "rescheduled")
OUTCOME_LABELS = {"scheduled": "Scheduled", "attended": "Attended", "no-show": "Did not show",
                  "cancelled": "Cancelled", "rescheduled": "Rescheduled"}
NOTICE_KINDS = {"offer": "Offer", "not-moving-forward": "Not moving forward",
                "no-decision-yet": "No decision yet"}
NOTICE_METHODS = ("email", "text", "phone", "in person")
CONTACT_METHODS = ("phone", "email", "text", "in person")
OFFER_REPLIES = ("accepted", "declined")
EVENT_KINDS = ("applied", "answered", "stage", "contacted", "interview", "outcome", "notice",
               "offer", "offer_reply", "purged", "note", "score", "scorecard", "hold", "roster", "ai",
               "corrected", "more_time")
ENTERED_FOR_APPLICANT = "entered for the applicant"

NOTICE_DAYS = 45                 # days after the last interview to tell the applicant
NOTICE_SOON_DAYS = 7
NOTICE_AIM_DAYS = 30             # our own aim, well inside the law's 45
NOTICE_KEEP_MONTHS = 36          # a decision notice is kept three years from the day given
DUPLICATE_DAYS = 30
SMALL_NUMBER = 5                 # below this many cases a percentage or median is not shown

TITLE_MAX = 80
DESCRIPTION_MIN = 200
DESCRIPTION_MAX = 6000
CRITERION_NAME_MAX = 80
DEFINITION_MAX = 1200
ANCHOR_MAX = 700
QUESTION_MAX = 400
MIN_QUESTIONS = 2
MAX_QUESTIONS = 6
MAX_CRITERIA = 12
NAME_MAX = 60
EXPERIENCE_MAX = 1000
ANSWER_MAX = 1500
NOTE_MAX = 1500
SCORE_NOTE_MAX = 600
SAID_MAX = 2000
LEVELS = (1, 2, 3, 4)
HOURS_A_YEAR = 2080
RANGE_CAP_A_YEAR = 50_000

_STAMP = "%Y-%m-%d %H:%M"
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PHONE_CHARS = re.compile(r"[0-9+().\s-]+")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_MONEY = re.compile(r"\$?\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _line(value, cap: int) -> str:
    """One clean line of text, capped."""
    return " ".join(hub_text.clean(str(value or "")).split())[:cap]


def _block(value, cap: int) -> str:
    """Clean text that may run over several lines, capped."""
    return hub_text.clean(str(value or "")).strip()[:cap].rstrip()


def _stamp() -> str:
    return shift_db.now_stamp()


def _today() -> date:
    """The calendar date in the store's time zone (not the 4 a.m. business
    day): legal deadlines count calendar days."""
    return shift_db.now_local().date()


def _as_date(text) -> date | None:
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def _as_time(text) -> datetime | None:
    try:
        return datetime.strptime(str(text or "")[:16], _STAMP)
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def _cents(amount) -> int | None:
    try:
        return int(round(float(amount) * 100)) if amount is not None else None
    except (TypeError, ValueError):
        return None


def money(amount) -> str:
    """17.9 -> '$17.90'."""
    cents = _cents(amount)
    return "" if cents is None else f"${cents // 100:,}.{cents % 100:02d}"


def _settings(conn) -> dict:
    """The People settings hiring needs, read on this connection."""
    saved = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {key: saved.get(key, default) for key, default in people_db.SETTINGS.items()}


def settings() -> dict:
    with closing(people_db.connect()) as conn:
        return _settings(conn)


def _event(conn, application_id: int, posting_id: int, by: str, kind: str, detail: str = "",
           from_stage: str | None = None, to_stage: str | None = None, at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO hr_events (application_id, posting_id, at, by, kind, from_stage, to_stage, detail) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (application_id, posting_id, at or _stamp(), _line(by, 80), kind, from_stage, to_stage,
         _line(detail, 120)))


class _Refused(Exception):
    """Raised inside a write to undo it: the change would leave a live
    posting failing the publish check."""

    def __init__(self, problems: list[str]):
        super().__init__("refused")
        self.problems = problems


# ---------------------------------------------------------------------------
# What a posting says: one source for the public page and the kept copy
# ---------------------------------------------------------------------------

AI_STATEMENT = (
    "We use artificial intelligence (AI) to help assess applications for this position. "
    "The AI tool compares your written answers with the job-related criteria in this posting "
    "and suggests a score. A member of our leadership team makes every decision.")
AGE_LINE = ("Ontario law sets a minimum age of 14 to work in a restaurant's serving areas "
            "and 15 in its kitchen.")
STUDENT_MINIMUM_LINE = (
    "The lower rate is Ontario's student minimum wage, which applies to students under 18 who "
    "work 28 hours a week or less during the school year or work during a school break.")
STUDENT_RATE_LINE = (
    "The lower rate is a student rate. It applies to students under 18 who work 28 hours a "
    "week or less during the school year or work during a school break.")
ADULT_REASON = ("We ask only because some rules differ for people under 18. It is not used to "
                "assess your application.")
MIC_HINT = "Tap the microphone on your keyboard to talk instead of type."
# The blank application form, in the order it is shown. The same words are
# on the page and in the kept copy.
FORM_STEP_ONE = (
    "First name (required)",
    "Last name (required)",
    "Phone (give a phone number or an email)",
    "Email (give a phone number or an email)",
    "Are you legally entitled to work in Canada? (yes or no, required)",
    "Are you 18 or older? (yes or no, required) " + ADULT_REASON,
    "When could you work? (tick boxes: Monday to Sunday, each with Morning, Afternoon, Evening)",
    "How did you hear about us? (" + "; ".join(label for _, label in HEARD_FROM) + ")",
    "If a friend or family member who works here told you about us, their name (optional)",
    "Any work or volunteer experience you would like us to know about? (optional)",
)
CONSENT_LABEL = "I have read this notice and I agree."


def contact_line(conf: dict) -> str:
    """'Morgan Example at hiring@example.com or 519-555-0100'."""
    name = conf.get("hiring_contact_name", "")
    ways = [w for w in (conf.get("hiring_contact_email", ""), conf.get("hiring_contact_phone", "")) if w]
    if not name and not ways:
        return "the restaurant"
    return (name or "the restaurant") + (" at " + " or ".join(ways) if ways else "")


def pay_line(posting: dict) -> str:
    """'$17.95 to $19.50 an hour'."""
    low, high = _cents(posting.get("pay_min")), _cents(posting.get("pay_max"))
    if low is None or high is None:
        return ""
    if low == high:
        return f"{money(posting['pay_min'])} an hour"
    return f"{money(posting['pay_min'])} to {money(posting['pay_max'])} an hour"


def pay_label(posting: dict, conf: dict) -> str:
    """The pay range for a list or a heading, where the full student-rate
    sentence is not printed beside it: '$16.90 (student rate) to $19.50 an
    hour'. A figure below the general minimum wage is never shown bare."""
    if not student_line(posting, conf) or _cents(posting.get("pay_min")) == _cents(posting.get("pay_max")):
        return pay_line(posting)
    return f"{money(posting['pay_min'])} (student rate) to {money(posting['pay_max'])} an hour"


def student_line(posting: dict, conf: dict) -> str:
    """Printed only while the lower figure really is a student rate: below
    the general minimum wage, with the student box ticked."""
    low, general, student = (_cents(posting.get("pay_min")), _cents(conf["min_wage_general"]),
                             _cents(conf["min_wage_student"]))
    if not posting.get("student_rate") or low is None or general is None or low >= general:
        return ""
    return STUDENT_MINIMUM_LINE if low == student else STUDENT_RATE_LINE


def accommodation_notice(conf: dict) -> str:
    return (f"{conf['employer_name']} welcomes applications from people with disabilities. "
            "Accommodation is available on request at every stage of our hiring process, "
            "including this application and the interview. To ask for an accommodation, or to "
            f"get this form another way, contact {contact_line(conf)}. You do not have to tell "
            "us about a disability to apply, and asking for an accommodation will not affect "
            "how your application is assessed.")


def privacy_notice(conf: dict, ai: bool) -> list[str]:
    """What an applicant is told before they send the form. Every sentence
    is something this tool really does (see purge() and apply())."""
    months = conf["retention_months"]
    lines = [
        "We collect the information on this form, and your answers to the quick questions "
        f"that follow, to assess your application for work at {conf['employer_name']}, to "
        "contact you about it, and to meet our legal record-keeping duties.",
        "It is seen only by the Operator and the leaders involved in hiring.",
    ]
    if ai:
        lines.append(
            "Your written answers to the quick questions may be sent to an AI service run by "
            "an outside company, which may process them outside Canada. Your name and contact "
            "details are not sent with them.")
    lines += [
        "We do not sell your information or use it for marketing.",
        f"If you are not hired, we keep your application for {months} months after the hiring "
        "decision. After that, the Operator removes your name, contact details, availability, "
        "experience and answers from our records. We keep them longer only if a complaint or "
        "legal claim means we must.",
        "Ontario law requires us to keep a copy of this posting, the blank application form "
        "and any notice we give you after an interview, with your name, for 3 years.",
        f"You can ask to see, correct or withdraw your application by contacting {contact_line(conf)}.",
        "By sending this form you confirm the information is true and agree to this use.",
        "If you are under 18, you may wish to read this with a parent or guardian.",
    ]
    return lines


def says_ai(posting: dict, conf: dict) -> bool:
    """Does this posting tell applicants that AI is used? Only while that
    is true: AI assist is on for the posting, the Operator's switch in
    Settings is on, and the server has a key for the AI service. Take any
    of the three away and the posting stops saying it, and nothing is
    sent. And never again once the posting has been public without the
    statement ("ai_locked", see _went_out_without_ai): AI may be used for
    a position only if its posting said so from the day it went live."""
    return (bool(posting.get("ai_assist")) and not posting.get("ai_locked")
            and conf.get("ai_hiring") == "1" and people_ai.available())


AI_LOCKED = ("This posting has been live without the AI statement, so AI assist can't be used "
             "for it. To use AI for this job, close this posting and copy it to a new draft.")
AI_DRAFT_ONLY = ("AI assist can only be switched on while a posting is a draft, because the posting "
                 "has to say AI is used from the day it goes live. To use AI for this job, close "
                 "this posting and copy it to a new draft.")
AI_NOT_READY = ("AI assist is switched on for this posting, but AI can't run yet (the switch in "
                "People settings is off, or no AI key is set up on the server), so the posting "
                "would go live without the AI statement. Finish setting AI up, or switch AI assist "
                "off for this posting, then publish.")


def _went_out_without_ai(conn, posting_id: int) -> bool:
    """Has any copy of this posting been public without the AI statement?
    The kept copies are the record of every version the public was shown;
    the copy taken when a posting closes is of a page nobody can open."""
    return conn.execute("SELECT 1 FROM hr_snapshots WHERE posting_id=? AND ai_statement=0 "
                        "AND reason != 'closed' LIMIT 1", (posting_id,)).fetchone() is not None


def public_view(posting: dict, conf: dict | None = None) -> dict:
    """Everything a published posting and its application form show, in one
    place, so the page an applicant sees and the copy that is kept can never
    say different things."""
    conf = conf or settings()
    criteria = [c for c in posting.get("criteria", []) if c.get("active", 1)]
    ai = says_ai(posting, conf)
    return {
        "id": posting["id"], "slug": posting["slug"], "title": posting["title"],
        "status": posting["status"],
        "employer": conf["employer_name"],
        "type_label": EMPLOYMENT_TYPES.get(posting["employment_type"], ""),
        "pay": pay_line(posting), "pay_short": pay_label(posting, conf),
        "student_line": student_line(posting, conf),
        "description": posting["description"],
        "criteria": [{"name": c["name"], "definition": c["definition"]} for c in criteria],
        "questions": [{"id": c["id"], "name": c["name"], "question": c["question"]}
                      for c in criteria if c["question"]],
        "vacancy_statement": VACANCY_STATEMENTS.get(posting["vacancy"], ""),
        "ai": ai, "ai_statement": AI_STATEMENT if ai else "",
        "accommodation": accommodation_notice(conf), "age_line": AGE_LINE,
        "contact": contact_line(conf),
        "contact_name": conf["hiring_contact_name"], "contact_email": conf["hiring_contact_email"],
        "contact_phone": conf["hiring_contact_phone"],
        "privacy": privacy_notice(conf, ai), "consent_label": CONSENT_LABEL,
        "form_step_one": FORM_STEP_ONE, "published_at": posting.get("published_at"),
    }


def document_text(view: dict) -> str:
    """The posting as shown, then the blank application form, as plain
    text: the body of a kept copy."""
    out = ["JOB POSTING", "", view["title"], view["employer"], view["type_label"],
           "Pay: " + view["pay"]]
    if view["student_line"]:
        out.append(view["student_line"])
    out += ["", view["description"], "", "What we look for"]
    out += [f"- {c['name']}: {c['definition']}" if c["definition"] else f"- {c['name']}"
            for c in view["criteria"]]
    out += ["", view["vacancy_statement"]]
    if view["ai_statement"]:
        out += ["", view["ai_statement"]]
    out += ["", view["accommodation"], "", view["age_line"], "",
            "APPLICATION FORM (blank)", "", "Step one: about you", "", view["accommodation"], ""]
    if view["ai_statement"]:
        out += [view["ai_statement"], ""]
    out += [f"- {label}" for label in view["form_step_one"]]
    out += ["", "How we look after your information"] + list(view["privacy"])
    out += [f"[ ] {view['consent_label']} (required)", "", "Step two: a few quick questions", MIC_HINT]
    out += [f"{n}. {q['question']}" for n, q in enumerate(view["questions"], 1)]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Postings
# ---------------------------------------------------------------------------

def _slug_base(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50].strip("-")
    return slug if len(slug) >= 2 else "job"


def _free_slug(conn, title: str) -> str:
    base = _slug_base(title)
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM hr_postings WHERE slug=?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _slug_follows_title(conn, posting_id: int) -> None:
    """A posting's public address is made from its title. Until the posting
    has been public the address follows the title, so a title that was
    corrected before publishing ("Young energetic counter girl" to "Team
    member") never goes live under its first wording. Call it when a draft
    is saved and again just before it is published. Once a posting has
    been public its address is frozen: links to it are already out."""
    row = conn.execute("SELECT slug, title, status FROM hr_postings WHERE id=?", (posting_id,)).fetchone()
    if not row or row["status"] != "draft" or conn.execute(
            "SELECT 1 FROM hr_snapshots WHERE posting_id=? LIMIT 1", (posting_id,)).fetchone():
        return
    base = _slug_base(row["title"])
    if row["slug"] == base or re.fullmatch(re.escape(base) + r"-[0-9]+", row["slug"]):
        return
    conn.execute("UPDATE hr_postings SET slug=? WHERE id=?", (_free_slug(conn, row["title"]), posting_id))


def _read_money(raw, label: str, problems: list[str]) -> float | None:
    text = _line(raw, 20)
    if not text:
        return None
    match = _MONEY.fullmatch(text)
    if not match or not 0 < float(match.group(1)) < 1000:
        problems.append(f"{label} needs a dollar amount like 17.95.")
        return None
    return round(float(match.group(1)), 2)


def _posting_fields(raw: dict) -> tuple[dict, list[str]]:
    """The posting as it will be stored, and what was typed that can't be."""
    problems: list[str] = []
    fields = {
        "title": _line(raw.get("title"), TITLE_MAX),
        "employment_type": raw.get("employment_type") if raw.get("employment_type") in EMPLOYMENT_TYPES
        else "part-time",
        "description": _block(raw.get("description"), DESCRIPTION_MAX),
        "pay_min": _read_money(raw.get("pay_min"), "The lowest pay", problems),
        "pay_max": _read_money(raw.get("pay_max"), "The highest pay", problems),
        "student_rate": 1 if str(raw.get("student_rate") or "") in ("1", "on", "True", "true") else 0,
        "vacancy": raw.get("vacancy") if raw.get("vacancy") in VACANCY_STATEMENTS else "",
    }
    if not fields["title"]:
        problems.append("Give the posting a title.")
    return fields, problems


def _criterion_row(row) -> dict:
    out = dict(row)
    try:
        anchors = json.loads(out.pop("anchors_json") or "[]")
    except ValueError:
        anchors = []
    out["anchors"] = [str(a) for a in anchors] if isinstance(anchors, list) else []
    return out


def _criteria(conn, posting_id: int, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM hr_criteria WHERE posting_id=? "
    if active_only:
        sql += "AND active=1 "
    return [_criterion_row(r) for r in conn.execute(sql + "ORDER BY sort, id", (posting_id,))]


def _posting(conn, posting_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM hr_postings WHERE id=?", (posting_id,)).fetchone()
    if not row:
        return None
    posting = dict(row)
    posting["criteria"] = _criteria(conn, posting_id)
    posting["ai_locked"] = _went_out_without_ai(conn, posting_id)
    return posting


def get_posting(posting_id: int) -> dict | None:
    """One posting with its active criteria (each with `anchors` as a list)
    and "ai_locked" (it has been public without the AI statement)."""
    with closing(people_db.connect()) as conn:
        return _posting(conn, posting_id)


def posting_by_slug(slug: str) -> dict | None:
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT id FROM hr_postings WHERE slug=?", (str(slug or "")[:80],)).fetchone()
        return _posting(conn, row["id"]) if row else None


def postings() -> list[dict]:
    """Every posting, open ones first, each with how many applications it
    has had."""
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM hr_applications a WHERE a.posting_id=p.id) AS applications "
            "FROM hr_postings p ORDER BY CASE p.status WHEN 'open' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, "
            "p.id DESC")]


def open_postings() -> list[dict]:
    """What the public careers page lists."""
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hr_postings WHERE status='open' ORDER BY published_at DESC, id DESC")]


def _problems(posting: dict, criteria: list[dict], conf: dict) -> list[str]:
    problems: list[str] = []
    if not posting.get("title"):
        problems.append("Give the posting a title.")
    length = len(posting.get("description") or "")
    if length < DESCRIPTION_MIN:
        problems.append(f"Write a description of at least {DESCRIPTION_MIN} characters "
                        f"(it has {length}).")
    low, high = _cents(posting.get("pay_min")), _cents(posting.get("pay_max"))
    general, student = _cents(conf["min_wage_general"]), _cents(conf["min_wage_student"])
    if low is None or high is None:
        problems.append("Enter both pay figures: the lowest and the highest hourly wage.")
    else:
        if low > high:
            problems.append("The lowest pay can't be more than the highest.")
        if posting.get("student_rate"):
            if low < student:
                problems.append(f"The lowest pay is below Ontario's student minimum wage "
                                f"({money(conf['min_wage_student'])} an hour).")
            elif low < general and high < general:
                problems.append(
                    "With the student wage box ticked, the highest pay must still be at least "
                    f"the general minimum wage ({money(conf['min_wage_general'])} an hour): "
                    "that is the least anyone who is not on the student rate can be paid.")
        elif low < general:
            problems.append(
                f"The lowest pay is below Ontario's general minimum wage "
                f"({money(conf['min_wage_general'])} an hour). If the lower figure is the "
                "student minimum wage, tick the student wage box.")
        if (high - low) * HOURS_A_YEAR > RANGE_CAP_A_YEAR * 100:
            problems.append("The pay range is too wide. Ontario allows a posted range of "
                            "$50,000 a year at most, which is about $24.03 an hour.")
    if posting.get("vacancy") not in VACANCY_STATEMENTS:
        problems.append("Say whether this posting is for an existing vacancy or for future openings.")
    if not conf["hiring_contact_name"] or not (conf["hiring_contact_email"] or conf["hiring_contact_phone"]):
        problems.append("Set the hiring contact's name and an email or phone number in People "
                        "settings. The accommodation notice needs a named person.")
    questions = [c for c in criteria if c.get("active", 1) and c.get("question")]
    if len(questions) < MIN_QUESTIONS:
        problems.append(f"Add at least {MIN_QUESTIONS} criteria that each have a question for "
                        "the applicant.")
    if len(questions) > MAX_QUESTIONS:
        problems.append(f"Applicants can be asked {MAX_QUESTIONS} questions at most. "
                        "Take a question off a criterion (it can stay as an interview criterion).")
    problems += [block["problem"] for block in question_blocks({"criteria": criteria})]
    return problems


def compliance_problems(posting: dict) -> list[str]:
    """The publish check, as plain sentences. A posting cannot go live, or
    stay live through a change, while this returns anything. `posting` is
    get_posting()'s shape (with its criteria)."""
    return _problems(posting, posting.get("criteria", []), settings())


# What an application or an interview may not ask (asking can itself
# breach the Human Rights Code). Two nets, because a word list cannot read
# a question for meaning:
#
# * NEVER_ASK: phrases that ask about a ground. They are looked for
#   everywhere in a posting (they flag a description, for the "I have read
#   these" tick).
# * Inside a question for applicants or an interview question the net is
#   much wider (question_hits): the same phrases with their plurals, the
#   words in QUESTION_ONLY, and every word on the ground lists the review
#   notes are checked with. Any hit holds the posting back. It cannot go
#   live until the question is reworded, or the Operator confirms that
#   this exact question is about a guest or the work and not about the
#   applicant ("Describe a time you helped a guest with a disability").
#   That confirmation is the Operator's alone, per question, and logged.
#
# Even so it catches listed words only: a question that asks about a
# ground in words that are on no list gets through, so the Operator still
# reads every question before publishing.
NEVER_ASK = (
    ("age", ("how old", "your age", "date of birth", "birth date", "birthdate", "birthday",
             "year of birth", "when were you born", "what year did you graduate",
             "when did you graduate", "year you graduated", "year did you finish", "dob",
             "what grade", "which grade")),
    ("pregnancy or family", ("pregnant", "pregnancy", "marital", "married", "have children",
                             "have kids", "have any children", "have any kids", "your children",
                             "your kids", "your child", "childcare", "child care", "your husband",
                             "your wife", "your spouse", "your boyfriend", "your girlfriend",
                             "maiden name", "plan to have", "are you single", "divorced",
                             "children do you have", "kids do you have", "start a family",
                             "family commitments", "live with your parents", "emergency contact",
                             "next of kin")),
    ("religion", ("religion", "religious", "church", "mosque", "temple", "synagogue", "your faith",
                  "what faith", "do you pray")),
    ("citizenship or where someone is from", (
        "citizen", "citizenship", "nationality", "born", "birthplace", "place of birth",
        "country of origin", "where are you from", "where you are from", "ethnicity", "your race",
        "mother tongue", "native language", "first language", "immigration status",
        "what country", "country are you from", "language at home", "speak at home",
        "native tongue", "ethnic background")),
    ("health or disability", ("disability", "disabled", "medical", "medication", "diagnosis",
                              "diagnosed", "illness", "health condition", "mental health", "wsib",
                              "workers comp", "workers' compensation", "workers compensation",
                              "physical limitation", "handicap", "handicapped", "sick leave",
                              "use drugs")),
    ("a record of offences", ("criminal", "arrested", "convicted", "conviction", "charged with",
                              "record of offences", "police record", "police check",
                              "background check", "trouble with the police",
                              "trouble with the law", "pardon", "record suspension")),
    ("gender or orientation", ("gender", "sexual orientation", "your sex", "male or female")),
    ("a SIN, a photo or a licence", ("social insurance", "sin number", "your sin", "photo",
                                     "photograph", "driver's licence", "drivers licence",
                                     "driver's license", "drivers license", "your height",
                                     "your weight", "how tall", "how much do you weigh")),
    ("income or why someone cannot work", ("government assistance", "social assistance",
                                           "why can't you work", "why can you not work",
                                           "why cannot you work")),
)
# Words that are fine in a description but hold a question back.
QUESTION_ONLY = (
    ("age", ("grade", "years old", "student", "students")),
    ("pregnancy or family", ("single", "parents")),
    ("religion", ("christmas", "easter")),
    ("citizenship or where someone is from", ("country", "canadian", "language")),
    ("health or disability", ("limitation", "physically able", "drugs", "do you drink")),
    ("a record of offences", ("police", "offence", "offense", "crime")),
    ("a SIN, a photo or a licence", ("tall", "weigh", "weight", "height", "postal code")),
)
_AGE_NUMBER = re.compile(r"\b(?:over|under|at\s+least|older\s+than|younger\s+than)\s+\d{1,2}\b", re.I)
QUESTION_OK_LABEL = "This question is about a guest or the work, not about the applicant."
QUESTION_WHICH = {"question": ("question for applicants", "question_ok"),
                  "interview": ("interview question", "interview_ok")}
NEVER_ASK_ADVICE = ("An application may not ask about this, and a posting may not screen on it. "
                    "Take it out, or say what the work needs instead.")
GROUND_ADVICE = ("This can point at a protected ground ({category}). A posting may not screen on "
                 "it and an application may not ask about it. Keep the wording about the work.")
# The word-list categories that are protected grounds (personality labels
# and consequence words are about reviews, not postings).
_GROUNDS = ("health", "family", "religion", "age", "origin", "gender", "leave")


def _phrase(term: str, loose: bool = False):
    """Whole words, any case, spaces or hyphens between them. loose=True
    also takes a plain plural or possessive of the last word ("health
    conditions", "limitation's")."""
    return re.compile(r"(?<![\w])" + r"[\s-]+".join(
        re.escape(part) for part in re.split(r"[\s-]+", term))
        + (r"(?:s|es|'s)?" if loose else "") + r"(?![\w])", re.I)


_NEVER_ASK_PATTERNS = [(about, [(term, _phrase(term)) for term in terms]) for about, terms in NEVER_ASK]
_QUESTION_PATTERNS = [(about, [(term, _phrase(term, loose=True)) for term in terms])
                      for about, terms in NEVER_ASK + QUESTION_ONLY]


def _found(text, groups) -> list[tuple[str, str]]:
    text = str(text or "").replace("’", "'")
    found = []
    for about, patterns in groups:
        for term, pattern in patterns:
            match = pattern.search(text)
            if match:
                found.append((match.start(), -len(term), term, about))
    return [(term, about) for _, _, term, about in sorted(found)]


def never_asked(text) -> list[tuple[str, str]]:
    """[(the phrase found, what it asks about)] for anything in `text` an
    application or an interview may not ask, in the order found."""
    return _found(text, _NEVER_ASK_PATTERNS)


def question_hits(text) -> list[tuple[str, str, bool]]:
    """Everything in a question (for applicants, or for the interview) that
    may ask about a protected ground: [(the words found, what they can ask
    about, True when it is one of the NEVER_ASK phrases)]. The phrases
    first, then the question-only words, a number that reads as an age,
    and every word on the ground lists review notes are checked with.
    Listed words only: it cannot read a question for meaning."""
    text = str(text or "").replace("’", "'")
    plain = {term for term, _ in never_asked(text)}
    hits = [(term, about, term in plain) for term, about in _found(text, _QUESTION_PATTERNS)]
    number = _AGE_NUMBER.search(text)
    if number:
        hits.append((" ".join(number.group(0).split()).lower(), "age", False))
    hits += [(f["term"], f["category"].lower(), False) for f in people_ai.language_check(text)
             if f["key"] in _GROUNDS]
    out, seen = [], set()
    for term, about, phrase in sorted(hits, key=lambda hit: not hit[2]):     # the phrases first
        if term.casefold() not in seen:
            seen.add(term.casefold())
            out.append((term, about, phrase))
    return out


def question_blocks(posting: dict) -> list[dict]:
    """The questions on this posting that hold it back: each uses words
    that may ask about a protected ground, and the Operator has not
    confirmed that exact wording as about a guest or the work. Each is
    {"criterion_id", "which" ("question" or "interview"), "name", "term",
    "about", "text", "problem" (the publish-check sentence)}."""
    blocks = []
    for c in posting.get("criteria", []):
        if not c.get("active", 1):
            continue
        for which, (label, ok_column) in QUESTION_WHICH.items():
            text = c.get("question" if which == "question" else "interview_question") or ""
            hits = question_hits(text)
            if not hits or (c.get(ok_column) or "") == text:
                continue
            term, about, phrase = hits[0]
            name = c.get("name") or "a criterion"
            said = (f"asks about {about} (“{term}”)" if phrase
                    else f"uses “{term}”, which can ask about {about}")
            blocks.append({
                "criterion_id": c.get("id"), "which": which, "name": name, "term": term,
                "about": about, "text": text,
                "problem": (f"The {label} under “{name}” {said}. An application or an interview "
                            "may not ask that. Ask about the work instead. If the question is "
                            "about a guest or the work and not about the applicant, the "
                            "Operator can confirm that here, under the publish check.")})
    return blocks


def confirm_question(posting_id: int, criterion_id: int, which: str, by: str) -> bool:
    """The Operator confirms that one flagged question, exactly as it is
    worded now, is about a guest or the work and not about the applicant.
    Kept with the question; changing the question afterwards voids it.
    Logged. True when something was recorded."""
    if which not in QUESTION_WHICH:
        return False
    column = QUESTION_WHICH[which][1]                     # one of our two column names
    source = "question" if which == "question" else "interview_question"
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT c.{source} AS text, c.{column} AS ok, p.status FROM hr_criteria c "
            "JOIN hr_postings p ON p.id=c.posting_id WHERE c.id=? AND c.posting_id=? AND c.active=1",
            (criterion_id, posting_id)).fetchone()
        if not row or row["status"] == "closed" or not row["text"] or row["ok"] == row["text"] \
                or not question_hits(row["text"]):
            return False
        conn.execute(f"UPDATE hr_criteria SET {column}=? WHERE id=?", (row["text"], criterion_id))
        people_db._audit(conn, by, "question confirmed as about the work", "posting", posting_id,
                         f"criterion {criterion_id}, {QUESTION_WHICH[which][0]}")
    return True


# Words that should make a leader stop and read the posting again. They
# never block by themselves: publishing asks for the "I have read these"
# tick while any are found.
WORDING_RULES = (
    ("canadian-experience", ("canadian experience",),
     "Ontario law does not let a posting ask for Canadian experience. Take it out, unless "
     "the sentence says none is needed."),
    ("age", ("young", "mature", "recent grad", "recent grads", "recent graduate",
             "recent graduates", "energetic", "digital native", "digital natives",
             "student only", "students only"),
     "This points to an age group. Describe the work instead."),
    ("language", ("native english speaker", "native english speakers", "native speaker",
                  "native speakers", "no accent"),
     "Ask for the language skill the job needs, such as taking guest orders in English, "
     "not how someone learned it or how they sound."),
    ("gendered", ("waitress", "hostess", "busboy", "busgirl", "salesman", "saleswoman"),
     "Use a title that fits anyone, such as server, host or team member."),
    ("physical", ("able-bodied", "strong", "fit", "be healthy", "good health", "physically fit"),
     "Name the duty instead, such as lifting boxes of up to 20 kg, so it is about the job."),
    ("pay", ("tips", "tip", "bonus", "bonuses"),
     "Posted pay means wages. Tips and bonuses can't be used to state it."),
)
_WORDING_PATTERNS = [(key, advice, [(term, _phrase(term)) for term in terms])
                     for key, terms, advice in WORDING_RULES]


def wording_flags(posting: dict) -> list[dict]:
    """[{"term", "key", "advice"}] for words in the posting's title,
    description and criteria that may point at a protected group, ask for
    Canadian experience or pad the pay, or that touch on anything an
    application may not ask about (the same word lists the review notes
    are checked with, plus NEVER_ASK). Plain word lists, no AI."""
    parts = [posting.get("title") or "", posting.get("description") or ""]
    if posting.get("status") == "draft":         # the public address is read too, until it is frozen
        parts.append(str(posting.get("slug") or "").replace("-", " "))
    for c in posting.get("criteria", []):
        if c.get("active", 1):
            parts += [c.get("name") or "", c.get("definition") or "", c.get("question") or "",
                      c.get("interview_question") or ""]
    text = "\n".join(parts)
    found = []
    for key, advice, patterns in _WORDING_PATTERNS:
        for term, pattern in patterns:
            match = pattern.search(text)
            if match:
                found.append((match.start(), -len(term), term, key, advice))
    found.sort()
    flags, taken = [], []
    for start, neg_length, term, key, advice in found:
        if any(s <= start < e for s, e in taken):          # "tips" inside a longer hit
            continue
        taken.append((start, start - neg_length))
        flags.append({"term": term, "key": key, "advice": advice})
    seen = {flag["term"].casefold() for flag in flags}
    for term, _ in never_asked(text):
        if term.casefold() not in seen:
            seen.add(term.casefold())
            flags.append({"term": term, "key": "never-ask", "advice": NEVER_ASK_ADVICE})
    for finding in people_ai.language_check(text):
        if finding["key"] in _GROUNDS and finding["term"].casefold() not in seen:
            seen.add(finding["term"].casefold())
            flags.append({"term": finding["term"], "key": finding["key"],
                          "advice": GROUND_ADVICE.format(category=finding["category"].lower())})
    return flags


def _take_snapshot(conn, posting: dict, conf: dict, reason: str, by: str) -> None:
    view = public_view(posting, conf)
    conn.execute(
        "INSERT INTO hr_snapshots (posting_id, taken_at, taken_by, reason, ai_statement, body) "
        "VALUES (?,?,?,?,?,?)",
        (posting["id"], _stamp(), _line(by, 80), reason, 1 if view["ai"] else 0, document_text(view)))


def _flag_terms(conn, posting_id: int) -> set:
    posting = _posting(conn, posting_id)
    return {flag["term"] for flag in wording_flags(posting)} if posting else set()


def _changed_while_open(conn, posting_id: int, by: str, reviewed: bool, before: set) -> None:
    """Call after any change to a posting, inside the write (`before` is
    _flag_terms() from just before the change). A live posting must still
    pass the publish check and any newly flagged word must have been read,
    or the whole change is undone; then an exact copy of the new version is
    kept."""
    posting = _posting(conn, posting_id)
    if not posting or posting["status"] != "open":
        return
    conf = _settings(conn)
    problems = _problems(posting, posting["criteria"], conf)
    if problems:
        raise _Refused(["This posting is live, so it has to keep passing the publish check."]
                       + problems)
    fresh = sorted({flag["term"] for flag in wording_flags(posting)} - before)
    if fresh and not reviewed:
        raise _Refused(["This posting is live, and the change adds words to check ("
                        + ", ".join(fresh) + "). Read the advice on flagged words, then tick "
                        "the box to say the wording is about the job."])
    _take_snapshot(conn, posting, conf, "changed", by)


def _write(work) -> list[str]:
    """Run `work(conn)` in one write; a _Refused undoes it and its problems
    are returned."""
    try:
        with closing(people_db.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            work(conn)
    except _Refused as refused:
        return refused.problems
    return []


def create_posting(raw: dict, by: str) -> tuple[int | None, list[str]]:
    """A new draft. Only a title is needed to save one; the publish check
    covers the rest."""
    fields, problems = _posting_fields(raw)
    if problems:
        return None, problems
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        posting_id = conn.execute(
            "INSERT INTO hr_postings (slug, title, employment_type, description, pay_min, pay_max, "
            "student_rate, vacancy, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_free_slug(conn, fields["title"]), fields["title"], fields["employment_type"],
             fields["description"], fields["pay_min"], fields["pay_max"], fields["student_rate"],
             fields["vacancy"], _stamp(), _line(by, 80))).lastrowid
    return posting_id, []


def save_posting(posting_id: int, raw: dict, by: str, reviewed: bool = False) -> list[str]:
    """Change a draft or a live posting (never a closed one). Returns the
    problems; nothing is saved while there are any. A change to a live
    posting keeps a new exact copy."""
    fields, problems = _posting_fields(raw)
    if problems:
        return problems

    def work(conn):
        before = _flag_terms(conn, posting_id)
        cur = conn.execute(
            "UPDATE hr_postings SET title=?, employment_type=?, description=?, pay_min=?, pay_max=?, "
            "student_rate=?, vacancy=?, wording_reviewed=? WHERE id=? AND status IN ('draft','open')",
            (fields["title"], fields["employment_type"], fields["description"], fields["pay_min"],
             fields["pay_max"], fields["student_rate"], fields["vacancy"], 1 if reviewed else 0,
             posting_id))
        if not cur.rowcount:
            raise _Refused(["That posting is closed, so it can't be changed. Copy it to a new "
                            "draft instead."])
        _slug_follows_title(conn, posting_id)
        _changed_while_open(conn, posting_id, by, reviewed, before)

    return _write(work)


def _criterion_fields(raw: dict) -> tuple[dict, list[str]]:
    problems = []
    anchors = [_block(a, ANCHOR_MAX) for a in (raw.get("anchors") or [])][:len(LEVELS)]
    anchors += [""] * (len(LEVELS) - len(anchors))
    fields = {
        "name": _line(raw.get("name"), CRITERION_NAME_MAX),
        "definition": _block(raw.get("definition"), DEFINITION_MAX),
        "anchors": anchors if any(anchors) else [],
        "question": _block(raw.get("question"), QUESTION_MAX),
        "interview_question": _block(raw.get("interview_question"), QUESTION_MAX),
        "dimension_key": (_line(raw.get("dimension_key"), 40) or None),
    }
    if not fields["name"]:
        problems.append("Give the criterion a name.")
    if not fields["definition"]:
        problems.append("Say what the criterion means, in a sentence about the job.")
    return fields, problems


def _confirmed(fields: dict, questions_ok: bool) -> tuple[str, str]:
    """(question_ok, interview_ok) for a criterion being saved: the exact
    wording, for a question that has hits, when the Operator ticked the
    confirmation on the same form; otherwise empty."""
    if not questions_ok:
        return "", ""
    return tuple(text if question_hits(text) else ""
                 for text in (fields["question"], fields["interview_question"]))


def add_criterion(posting_id: int, raw: dict, by: str, reviewed: bool = False,
                  questions_ok: bool = False) -> list[str]:
    """Add what applications are scored on. raw: name, definition, anchors
    (four level descriptions, lowest first, or none), question (blank =
    interview only), interview_question, dimension_key. questions_ok is the
    Operator's confirmation, given on the same form, that a flagged
    question is about a guest or the work (see question_blocks)."""
    fields, problems = _criterion_fields(raw)
    if problems:
        return problems
    question_ok, interview_ok = _confirmed(fields, questions_ok)

    def work(conn):
        before = _flag_terms(conn, posting_id)
        row = conn.execute("SELECT status FROM hr_postings WHERE id=?", (posting_id,)).fetchone()
        if not row or row["status"] == "closed":
            raise _Refused(["That posting is closed, so it can't be changed."])
        count, last = conn.execute("SELECT COUNT(*), COALESCE(MAX(sort), 0) FROM hr_criteria "
                                   "WHERE posting_id=? AND active=1", (posting_id,)).fetchone()
        if count >= MAX_CRITERIA:
            raise _Refused([f"A posting can have {MAX_CRITERIA} criteria at most."])
        if fields["dimension_key"] and conn.execute(
                "SELECT 1 FROM hr_criteria WHERE posting_id=? AND active=1 AND dimension_key=?",
                (posting_id, fields["dimension_key"])).fetchone():
            raise _Refused(["That standard is already one of this posting's criteria."])
        conn.execute(
            "INSERT INTO hr_criteria (posting_id, sort, name, dimension_key, definition, anchors_json, "
            "question, interview_question, question_ok, interview_ok) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (posting_id, last + 1, fields["name"], fields["dimension_key"], fields["definition"],
             json.dumps(fields["anchors"], ensure_ascii=False), fields["question"],
             fields["interview_question"], question_ok, interview_ok))
        _changed_while_open(conn, posting_id, by, reviewed, before)

    return _write(work)


def save_criterion(criterion_id: int, posting_id: int, raw: dict, by: str,
                   reviewed: bool = False, questions_ok: bool = False) -> list[str]:
    fields, problems = _criterion_fields(raw)
    if problems:
        return problems
    question_ok, interview_ok = _confirmed(fields, questions_ok)

    def work(conn):
        before = _flag_terms(conn, posting_id)
        row = conn.execute("SELECT status FROM hr_postings WHERE id=?", (posting_id,)).fetchone()
        if not row or row["status"] == "closed":
            raise _Refused(["That posting is closed, so it can't be changed."])
        # A confirmation already given stays only while its question is word for word the same.
        cur = conn.execute(
            "UPDATE hr_criteria SET name=?, definition=?, anchors_json=?, question=?, "
            "interview_question=?, "
            "question_ok=CASE WHEN ?!='' THEN ? WHEN question_ok=? THEN question_ok ELSE '' END, "
            "interview_ok=CASE WHEN ?!='' THEN ? WHEN interview_ok=? THEN interview_ok ELSE '' END "
            "WHERE id=? AND posting_id=? AND active=1",
            (fields["name"], fields["definition"], json.dumps(fields["anchors"], ensure_ascii=False),
             fields["question"], fields["interview_question"],
             question_ok, question_ok, fields["question"],
             interview_ok, interview_ok, fields["interview_question"], criterion_id, posting_id))
        if not cur.rowcount:
            raise _Refused(["That criterion isn't on this posting any more."])
        _changed_while_open(conn, posting_id, by, reviewed, before)

    return _write(work)


def remove_criterion(criterion_id: int, posting_id: int, by: str) -> list[str]:
    """Take a criterion off a posting. One that already has answers or
    scores is kept in the record and only hidden."""

    def work(conn):
        row = conn.execute("SELECT status FROM hr_postings WHERE id=?", (posting_id,)).fetchone()
        if not row or row["status"] == "closed":
            raise _Refused(["That posting is closed, so it can't be changed."])
        used = conn.execute(
            "SELECT (SELECT COUNT(*) FROM hr_answers WHERE criterion_id=?) + "
            "(SELECT COUNT(*) FROM hr_scores WHERE criterion_id=?)", (criterion_id, criterion_id)).fetchone()[0]
        if used:
            cur = conn.execute("UPDATE hr_criteria SET active=0 WHERE id=? AND posting_id=? AND active=1",
                               (criterion_id, posting_id))
        else:
            cur = conn.execute("DELETE FROM hr_criteria WHERE id=? AND posting_id=?",
                               (criterion_id, posting_id))
        if not cur.rowcount:
            raise _Refused(["That criterion was already removed."])
        _changed_while_open(conn, posting_id, by, True, set())

    return _write(work)


def publish(posting_id: int, by: str, reviewed: bool = False) -> list[str]:
    """Put a draft on the public careers page. Refused while the publish
    check finds anything, or while flagged words have not been read. Keeps
    an exact copy of what went live."""

    def work(conn):
        _slug_follows_title(conn, posting_id)     # the address it goes live at is its title today
        posting = _posting(conn, posting_id)
        if not posting or posting["status"] != "draft":
            raise _Refused(["Only a draft can be published."])
        conf = _settings(conn)
        problems = _problems(posting, posting["criteria"], conf)
        if problems:
            raise _Refused(problems)
        if wording_flags(posting) and not reviewed:
            raise _Refused(["Read the flagged words, then tick the box to say the wording is "
                            "about the job."])
        if posting["ai_assist"] and not says_ai(posting, conf):
            raise _Refused([AI_NOT_READY])
        now = _stamp()
        conn.execute(
            "UPDATE hr_postings SET status='open', published_at=?, published_by=?, wording_reviewed=? "
            "WHERE id=? AND status='draft'", (now, _line(by, 80), 1 if reviewed else 0, posting_id))
        posting.update(status="open", published_at=now)
        _take_snapshot(conn, posting, conf, "published", by)

    return _write(work)


def close_posting(posting_id: int, by: str) -> bool:
    """Take a live posting off the careers page for good. The date is the
    day public access ended; the three-year record runs from it."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        posting = _posting(conn, posting_id)
        if not posting or posting["status"] != "open":
            return False
        conn.execute("UPDATE hr_postings SET status='closed', closed_at=?, closed_by=? "
                     "WHERE id=? AND status='open'", (_stamp(), _line(by, 80), posting_id))
        _take_snapshot(conn, posting, _settings(conn), "closed", by)
    return True


def set_ai(posting_id: int, on: bool, by: str) -> bool:
    """Switch AI assist on or off for one posting. On only while it is a
    draft: a posting has to say AI is used from the day it goes live. Off
    on a draft or a live posting; on a live one the AI statement comes off
    with it, a new exact copy is kept, and AI assist then stays off for
    that posting. True when something changed."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("UPDATE hr_postings SET ai_assist=? WHERE id=? AND ai_assist=? AND "
                           + ("status='draft'" if on else "status IN ('draft','open')"),
                           (1 if on else 0, posting_id, 0 if on else 1))
        if not cur.rowcount:
            return False
        posting = _posting(conn, posting_id)
        if posting["status"] == "open":
            _take_snapshot(conn, posting, _settings(conn), "changed", by)
    return True


SETTINGS_CHANGE = "A change in People settings"


def keep_current_copy(posting_id: int) -> bool:
    """A live posting also changes when a setting it prints changes: the
    hiring contact, the employer name, a wage floor, the months
    applications are kept, or whether AI is in use at all. The careers
    pages call this before they show a
    posting: if what is about to be shown no longer matches the last kept
    copy, a new copy is kept first, so every version the public saw is on
    record. True when a copy was taken."""
    with closing(people_db.connect()) as conn:
        def current():
            posting = _posting(conn, posting_id)
            if not posting or posting["status"] != "open":
                return None, None
            conf = _settings(conn)
            last = conn.execute("SELECT body FROM hr_snapshots WHERE posting_id=? ORDER BY id DESC LIMIT 1",
                                (posting_id,)).fetchone()
            same = bool(last) and last["body"] == document_text(public_view(posting, conf))
            return (None, None) if same else (posting, conf)

        if current()[0] is None:                 # the usual case: one read, nothing to do
            return False
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            posting, conf = current()            # again, now that nobody else can write
            if posting is None:
                return False
            _take_snapshot(conn, posting, conf, "changed", SETTINGS_CHANGE)
    return True


def needs_attention() -> list[dict]:
    """Live postings that no longer pass the publish check (a wage floor
    went up, or the hiring contact was removed), each with its problems."""
    out = []
    with closing(people_db.connect()) as conn:
        conf = _settings(conn)
        for row in conn.execute("SELECT id FROM hr_postings WHERE status='open' ORDER BY id").fetchall():
            posting = _posting(conn, row["id"])
            problems = _problems(posting, posting["criteria"], conf)
            if problems:
                out.append({"id": posting["id"], "title": posting["title"], "problems": problems})
    return out


def copy_posting(posting_id: int, by: str) -> int | None:
    """A new draft with the same wording and criteria (for the next time
    the same job is posted). Never copies applications."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        posting = _posting(conn, posting_id)
        if not posting:
            return None
        new_id = conn.execute(
            "INSERT INTO hr_postings (slug, title, employment_type, description, pay_min, pay_max, "
            "student_rate, vacancy, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_free_slug(conn, posting["title"]), posting["title"], posting["employment_type"],
             posting["description"], posting["pay_min"], posting["pay_max"], posting["student_rate"],
             posting["vacancy"], _stamp(), _line(by, 80))).lastrowid
        for n, c in enumerate(posting["criteria"], 1):
            conn.execute(
                "INSERT INTO hr_criteria (posting_id, sort, name, dimension_key, definition, "
                "anchors_json, question, interview_question) VALUES (?,?,?,?,?,?,?,?)",
                (new_id, n, c["name"], c["dimension_key"], c["definition"],
                 json.dumps(c["anchors"], ensure_ascii=False), c["question"], c["interview_question"]))
    return new_id


def snapshots(posting_id: int) -> list[dict]:
    """The kept copies of one posting, oldest first (no bodies)."""
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, posting_id, taken_at, taken_by, reason, ai_statement FROM hr_snapshots "
            "WHERE posting_id=? ORDER BY id", (posting_id,))]


def get_snapshot(snapshot_id: int) -> dict | None:
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT * FROM hr_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Applying (the public form)
# ---------------------------------------------------------------------------

def _contact_keys(email: str, phone: str) -> list[str]:
    """Keyed hashes of the email and the phone number, used only to notice
    the same person applying to the same posting twice."""
    keys = []
    if email:
        keys.append(people_db.token_hash("contact:e:" + email.casefold())[:32])
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if digits:
        keys.append(people_db.token_hash("contact:p:" + digits)[:32])
    return keys


def clean_application(raw: dict) -> tuple[dict, list[str]]:
    """Step one of the form, tidied and checked. Returns (fields, problems
    in plain words); nothing but what is listed here is ever kept."""
    problems: list[str] = []
    fields = {
        "first_name": _line(raw.get("first_name"), NAME_MAX),
        "last_name": _line(raw.get("last_name"), NAME_MAX),
        "email": _line(raw.get("email"), 120),
        "phone": _line(raw.get("phone"), 30),
        "work_entitled": raw.get("work_entitled") if raw.get("work_entitled") in ("yes", "no") else "",
        "adult": raw.get("adult") if raw.get("adult") in ("yes", "no") else "",
        "heard_from": raw.get("heard_from") if raw.get("heard_from") in HEARD_LABELS else "",
        "referrer": _line(raw.get("referrer"), 80),
        "experience": _block(raw.get("experience"), EXPERIENCE_MAX),
        "consent": str(raw.get("consent") or "") == "1",
    }
    availability = {}
    for day, _ in DAYS:
        parts = [part for part, _ in DAYPARTS if str(raw.get(f"av_{day}_{part}") or "") == "1"]
        if parts:
            availability[day] = parts
    fields["availability"] = availability
    if fields["heard_from"] != "friend":
        fields["referrer"] = ""
    if not fields["first_name"]:
        problems.append("Enter your first name.")
    if not fields["last_name"]:
        problems.append("Enter your last name.")
    if not fields["email"] and not fields["phone"]:
        problems.append("Give us a phone number or an email so we can reach you.")
    if fields["email"] and not _EMAIL.fullmatch(fields["email"]):
        problems.append("That email doesn't look right. Check it and try again.")
    if fields["phone"]:
        digits = re.sub(r"\D", "", fields["phone"])
        if not 10 <= len(digits) <= 15 or not _PHONE_CHARS.fullmatch(fields["phone"]):
            problems.append("That phone number needs 10 to 15 digits.")
    if not fields["work_entitled"]:
        problems.append("Answer yes or no: are you legally entitled to work in Canada?")
    if not fields["adult"]:
        problems.append("Answer yes or no: are you 18 or older?")
    if not fields["consent"]:
        problems.append("Tick the box to say you have read how we look after your information.")
    return fields, problems


def apply(posting_id: int, raw: dict, ai_shown: bool = False) -> dict:
    """Take step one of an application. Returns {"status": ...}:
    "invalid" (with "problems" and the tidied "fields"), "closed" (the
    posting is no longer open) or "created" (with the "token" for the
    applicant's private link, which is not stored, and "id"). An
    application that gives the same email or phone as an earlier one to
    this posting in the last 30 days is created like any other, with
    duplicate_of set for a leader to look at."""
    fields, problems = clean_application(raw)
    if problems:
        return {"status": "invalid", "problems": problems, "fields": fields}
    keys = _contact_keys(fields["email"], fields["phone"])
    token = people_db.new_token()
    hashed = people_db.token_hash(token)
    now = shift_db.now_local()
    stamp = now.strftime(_STAMP)
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        posting = _posting(conn, posting_id)
        if not posting or posting["status"] != "open":
            return {"status": "closed"}
        conf = _settings(conn)
        # AI may only ever read an application whose applicant was shown the
        # AI statement, on a posting that was really saying it.
        told_ai = bool(ai_shown) and says_ai(posting, conf)
        # The same email or phone on this posting in the last 30 days is
        # kept like any other application and flagged for a leader. The
        # person sending the form is never told: a different answer would
        # tell anyone who typed someone else's email whether they applied.
        since = (now - timedelta(days=DUPLICATE_DAYS)).strftime(_STAMP)
        earlier = [row["id"] for key in keys for row in conn.execute(
            "SELECT id FROM hr_applications WHERE posting_id=? AND created_at>=? "
            "AND purged_at IS NULL AND instr(contact_key, ?) > 0", (posting_id, since, key))]
        until = (now + timedelta(days=int(conf["quick_link_days"]))).strftime(_STAMP)
        application_id = conn.execute(
            "INSERT INTO hr_applications (posting_id, first_name, last_name, email, phone, contact_key, "
            "work_entitled, adult, availability, heard_from, referrer, experience, ai_disclosed, "
            "consent_at, token_hash, answers_until, stage, stage_at, created_at, keep_months, "
            "duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)",
            (posting_id, fields["first_name"], fields["last_name"], fields["email"], fields["phone"],
             " ".join(keys), fields["work_entitled"], fields["adult"],
             json.dumps(fields["availability"]), fields["heard_from"], fields["referrer"],
             fields["experience"], 1 if told_ai else 0, stamp, hashed,
             until, stamp, stamp, int(conf["retention_months"]),
             min(earlier) if earlier else None)).lastrowid
        _event(conn, application_id, posting_id, "applicant", "applied",
               "possible duplicate" if earlier else "", to_stage="new", at=stamp)
    return {"status": "created", "token": token, "id": application_id}


def application_by_token(token: str) -> dict | None:
    """What an applicant's private page needs, and nothing a leader wrote:
    the posting's title, when the application was received, the quick
    questions while they can still be answered, and the plain status once a
    decision notice has been logged ("decision": None, "pending", "made",
    or "closed" for someone never interviewed who was told they are not
    moving forward). None for an unknown link."""
    token = str(token or "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    hashed = people_db.token_hash(token)
    with closing(people_db.connect()) as conn:
        row = conn.execute(
            "SELECT a.id, a.posting_id, a.created_at, a.answers_until, a.answered_at, "
            "a.stage, a.purged_at, p.title FROM hr_applications a JOIN hr_postings p ON p.id=a.posting_id "
            "WHERE a.token_hash=? AND a.token_hash != ''", (hashed,)).fetchone()
        if not row or row["purged_at"]:
            return None
        out = dict(row)
        questions = [{"id": c["id"], "question": c["question"]}
                     for c in _criteria(conn, row["posting_id"]) if c["question"]]
        notice = conn.execute("SELECT kind FROM hr_notices WHERE application_id=? "
                              "ORDER BY given_on DESC, id DESC LIMIT 1", (row["id"],)).fetchone()
        met = conn.execute("SELECT 1 FROM hr_interviews WHERE application_id=? AND outcome='attended' "
                           "LIMIT 1", (row["id"],)).fetchone() is not None
        conf = _settings(conn)
    out["questions"] = questions
    out["can_answer"] = bool(questions and not row["answered_at"] and row["stage"] in ACTIVE_STAGES
                             and row["answers_until"] >= _stamp())
    # "A hiring decision has been made for this posting" is said only to
    # someone who was interviewed or offered the job, as in the message a
    # leader sends: for anyone else all this page knows is that their own
    # application is not moving forward.
    out["decision"] = (None if not notice else
                       "pending" if notice["kind"] == "no-decision-yet" else
                       "made" if notice["kind"] == "offer" or met else "closed")
    out["contact"] = contact_line(conf)
    out["employer"] = conf["employer_name"]
    del out["stage"], out["purged_at"]
    return out


def _open_questions(conn, application_id: int, posting_id: int) -> list[dict]:
    """The posting's questions this application has no answer to yet."""
    answered = {r["criterion_id"] for r in conn.execute(
        "SELECT criterion_id FROM hr_answers WHERE application_id=?", (application_id,))}
    return [c for c in _criteria(conn, posting_id) if c["question"] and c["id"] not in answered]


def unanswered_questions(application_id: int) -> list[dict]:
    """The questions a leader can still enter an answer to for this
    applicant ([{"id", "question"}]): asked by the posting, not answered,
    on an application that is still open."""
    with closing(people_db.connect()) as conn:
        row = _open_application(conn, application_id)
        if not row or row["stage"] not in ACTIVE_STAGES:
            return []
        return [{"id": c["id"], "question": c["question"]}
                for c in _open_questions(conn, application_id, row["posting_id"])]


def save_answers(application_id: int, answers: dict, entered_by: str | None = None) -> str:
    """Step two: "saved", "empty" (nothing was written), or "closed"
    (already answered, or the window has ended). answers: {criterion id:
    text}; only this posting's own questions are kept. The applicant
    answers once, on their own link. `entered_by` is the leader recording
    answers the applicant gave another way (by phone, on paper, with
    someone's help): that works after the link has closed too, and again
    later for any question that still has no answer (one that has an
    answer is never replaced), and the history says who entered them,
    never why."""
    now = _stamp()
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT posting_id, stage FROM hr_applications WHERE id=? "
                           "AND purged_at IS NULL AND ((answered_at IS NULL AND answers_until>=?) OR ?)",
                           (application_id, now, 1 if entered_by else 0)).fetchone()
        if not row or row["stage"] not in ACTIVE_STAGES:
            return "closed"
        asked = {c["id"] for c in _open_questions(conn, application_id, row["posting_id"])}
        if not asked:
            return "closed"
        kept = {}
        for criterion_id, body in (answers or {}).items():
            text = _block(body, ANSWER_MAX)
            if criterion_id in asked and text:
                kept[criterion_id] = text
        if not kept:
            return "empty"
        for criterion_id, text in kept.items():
            conn.execute("INSERT INTO hr_answers (application_id, criterion_id, body) "
                         "VALUES (?,?,?)", (application_id, criterion_id, text))
        conn.execute("UPDATE hr_applications SET answered_at=? WHERE id=? AND answered_at IS NULL",
                     (now, application_id))
        _event(conn, application_id, row["posting_id"], entered_by or "applicant", "answered",
               ENTERED_FOR_APPLICANT if entered_by else "", at=now)
    return "saved"


def give_more_time(application_id: int, by: str) -> str | None:
    """Open the applicant's own link for the quick questions again, for
    the usual number of days from now. For someone who needs more time
    (accommodation is offered at every stage, and nobody has to say why:
    no reason is asked for or kept). Returns the new closing time, or None
    when there is nothing to reopen (already answered, decided or removed)."""
    now = shift_db.now_local()
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row or row["answered_at"] or row["stage"] not in ACTIVE_STAGES \
                or not any(c["question"] for c in _criteria(conn, row["posting_id"])):
            return None
        until = (now + timedelta(days=int(_settings(conn)["quick_link_days"]))).strftime(_STAMP)
        conn.execute("UPDATE hr_applications SET answers_until=? WHERE id=? AND answered_at IS NULL",
                     (until, application_id))
        _event(conn, application_id, row["posting_id"], by, "more_time")
    return until


# ---------------------------------------------------------------------------
# The pipeline and one candidate
# ---------------------------------------------------------------------------

def _days_since(stamp, today: date | None = None) -> int:
    day = _as_date(stamp)
    return max(((today or _today()) - day).days, 0) if day else 0


def _name(row) -> str:
    return " ".join(part for part in (row["first_name"], row["last_name"]) if part) or "Removed"


def pipeline() -> list[dict]:
    """Open postings (and closed ones that still have someone in an active
    stage), each with counts per stage and the applicants in each active
    stage: name, days in stage and flags. Nothing is ordered by score."""
    today, now = _today(), _stamp()
    with closing(people_db.connect()) as conn:
        out = []
        for p in conn.execute("SELECT id, slug, title, status, ai_assist FROM hr_postings "
                              "WHERE status != 'draft' ORDER BY status='closed', id DESC").fetchall():
            rows = conn.execute(
                "SELECT id, first_name, last_name, stage, stage_at, answered_at, answers_until, "
                "work_entitled, hold, purged_at, duplicate_of FROM hr_applications WHERE posting_id=? "
                "ORDER BY stage_at, id", (p["id"],)).fetchall()
            counts = {stage: 0 for stage in STAGES}
            stages = {stage: [] for stage in ACTIVE_STAGES}
            for r in rows:
                counts[r["stage"]] = counts.get(r["stage"], 0) + 1
                if r["stage"] in stages:
                    stages[r["stage"]].append({
                        "id": r["id"], "name": _name(r), "days": _days_since(r["stage_at"], today),
                        "unanswered": not r["answered_at"],
                        "link_expired": not r["answered_at"] and r["answers_until"] < now,
                        "check_work": r["work_entitled"] == "no", "hold": bool(r["hold"]),
                        "duplicate": bool(r["duplicate_of"])})
            active = sum(len(v) for v in stages.values())
            if p["status"] == "open" or active:
                out.append({**dict(p), "counts": counts, "stages": stages, "total": len(rows),
                            "active": active})
    return out


_SCORING_COLUMNS = ("id", "posting_id", "first_name", "last_name", "availability", "experience",
                    "ai_disclosed", "answers_until", "answered_at", "stage", "stage_at", "hold",
                    "created_at", "purged_at", "purged_by", "keep_months", "duplicate_of")


def candidate(application_id: int) -> dict | None:
    """One application as the candidate page (a scoring screen) may see it.
    On purpose this never carries the email, the phone number, the
    18-or-over or work-entitlement answers, or how the applicant heard of
    us: those are for candidate_contact()."""
    with closing(people_db.connect()) as conn:
        row = conn.execute(
            "SELECT " + ", ".join("a." + c for c in _SCORING_COLUMNS) + ", p.title AS posting_title, "
            "p.status AS posting_status, p.ai_assist AS posting_ai, p.published_at AS posting_published "
            "FROM hr_applications a JOIN hr_postings p ON p.id=a.posting_id WHERE a.id=?",
            (application_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["posting_ai_locked"] = _went_out_without_ai(conn, row["posting_id"])
        # Other applications to this posting that gave the same email or
        # phone, either way round (ids only: a scoring screen shows no contact).
        out["same_contact"] = [] if row["purged_at"] else [r["id"] for r in conn.execute(
            "SELECT id FROM hr_applications WHERE purged_at IS NULL AND id != ? AND "
            "(id = ? OR duplicate_of = ? OR (duplicate_of IS NOT NULL AND duplicate_of = ?)) ORDER BY id",
            (application_id, row["duplicate_of"] or 0, application_id, row["duplicate_of"] or 0))]
        out["criteria"] = _criteria(conn, row["posting_id"])
        answers = {r["criterion_id"]: r["body"] for r in conn.execute(
            "SELECT criterion_id, body FROM hr_answers WHERE application_id=?", (application_id,))}
    try:
        grid = json.loads(out["availability"] or "{}")
    except ValueError:
        grid = {}
    out["availability"] = grid if isinstance(grid, dict) else {}
    out["name"] = _name(row)
    out["answers"] = answers
    out["days_in_stage"] = _days_since(row["stage_at"])
    out["link_expired"] = not row["answered_at"] and row["answers_until"] < _stamp()
    out["purged"] = bool(row["purged_at"])
    return out


def candidate_contact(application_id: int) -> dict | None:
    """The part of an application that is never on a scoring screen: how to
    reach the applicant, how they heard of us, and the two yes/no legal
    answers for the offer checks."""
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT id, email, phone, work_entitled, adult, heard_from, referrer "
                           "FROM hr_applications WHERE id=?", (application_id,)).fetchone()
    return dict(row) if row else None


def correct_details(application_id: int, raw: dict, by: str) -> list[str]:
    """Change an applicant's name, email or phone when they ask for a
    correction (applicants are told they can). The same checks as the form
    they filled in. Returns the problems; nothing is saved while there are
    any."""
    fields, problems = clean_application({**raw, "work_entitled": "yes", "adult": "yes", "consent": "1"})
    if problems:
        return problems
    keys = _contact_keys(fields["email"], fields["phone"])
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return ["That application isn't there any more."]
        conn.execute("UPDATE hr_applications SET first_name=?, last_name=?, email=?, phone=?, contact_key=? "
                     "WHERE id=?", (fields["first_name"], fields["last_name"], fields["email"],
                                    fields["phone"], " ".join(keys), application_id))
        _event(conn, application_id, row["posting_id"], by, "corrected", "name or contact details")
    return []


def events(application_id: int) -> list[dict]:
    """The application's history, oldest first."""
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hr_events WHERE application_id=? ORDER BY id", (application_id,))]


def allowed_moves(stage: str, operator: bool = False) -> list[str]:
    """Where an application in `stage` can go next. Forward along the main
    line (a stage may be skipped, but only an offer leads to hired), or out
    as not selected or withdrawn. Only the Operator reopens."""
    if stage in ACTIVE_STAGES:
        ahead = [s for s in MAIN_LINE[MAIN_LINE.index(stage) + 1:] if s != "hired" or stage == "offer"]
        return ahead + list(DECIDED_STAGES)
    if stage in DECIDED_STAGES and operator:
        return ["screening"]
    return []


def move_stage(application_id: int, expected: str, to: str, by: str, reason: str = "",
               checks_done: bool = False, operator: bool = False) -> str | None:
    """Move one application, by a person's choice. Returns None when it
    moved, or a plain sentence saying why it did not. `expected` is the
    stage the leader was looking at: if someone else moved it meanwhile,
    nothing happens."""
    if to not in allowed_moves(expected, operator):
        return "That move isn't available from this stage."
    detail = ""
    if to == "not-selected":
        if reason not in NOT_SELECTED_REASONS:
            return "Choose the job-related reason from the list."
        detail = reason
    elif to == "hired":
        if not checks_done:
            return ("Tick the box to say the offer checks are done: proof of age seen after the "
                    "offer, and legally able to work in Canada.")
        detail = OFFER_CHECKS_LABEL
    elif expected in DECIDED_STAGES:
        detail = "reopened"
    now = _stamp()
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT posting_id FROM hr_applications WHERE id=? AND stage=? "
                           "AND purged_at IS NULL", (application_id, expected)).fetchone()
        if not row:
            return "Someone else changed this application. Check where it is now."
        conn.execute("UPDATE hr_applications SET stage=?, stage_at=? WHERE id=? AND stage=?",
                     (to, now, application_id, expected))
        _event(conn, application_id, row["posting_id"], by, "stage", detail, expected, to, now)
    return None


def _open_application(conn, application_id: int):
    """The application row, unless it is gone or its details were removed."""
    return conn.execute("SELECT * FROM hr_applications WHERE id=? AND purged_at IS NULL",
                        (application_id,)).fetchone()


def add_note(application_id: int, body: str, by: str) -> bool:
    text = _block(body, NOTE_MAX)
    if not text:
        return False
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return False
        conn.execute("INSERT INTO hr_notes (application_id, body, by, at) VALUES (?,?,?,?)",
                     (application_id, text, _line(by, 80), _stamp()))
        _event(conn, application_id, row["posting_id"], by, "note")
    return True


def notes(application_id: int) -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hr_notes WHERE application_id=? ORDER BY id DESC", (application_id,))]


def mark_contacted(application_id: int, method: str, by: str) -> bool:
    """Record that a leader reached out (the clock for "time to first
    contact" stops at the first one)."""
    if method not in CONTACT_METHODS:
        return False
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return False
        _event(conn, application_id, row["posting_id"], by, "contacted", method)
    return True


def set_hold(application_id: int, on: bool, by: str) -> bool:
    """"Hold: do not delete", for when a complaint or claim is known. An
    application on hold is never purged."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT posting_id FROM hr_applications WHERE id=? AND hold=?",
                           (application_id, 0 if on else 1)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE hr_applications SET hold=? WHERE id=?", (1 if on else 0, application_id))
        _event(conn, application_id, row["posting_id"], by, "hold", "on" if on else "off")
    return True


# ---------------------------------------------------------------------------
# Scores: a level and a note for each criterion, by each leader on their own
# ---------------------------------------------------------------------------

def _mine(row, scorer_name: str, scorer_leader_id: int | None) -> bool:
    """Is this score row the viewer's own? A leader is matched on id AND
    name; the Operator (no leader id) on the name alone."""
    if scorer_leader_id is None:
        return row["scorer_leader_id"] is None and row["scorer_name"] == scorer_name
    return people_db.same_person(scorer_leader_id, scorer_name, row["scorer_leader_id"], row["scorer_name"])


def _level(raw) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text in ("1", "2", "3", "4") else None


def save_answer_scores(application_id: int, scorer_name: str, scorer_leader_id: int | None,
                       entries: dict) -> int:
    """This leader's own level and note for each written answer. entries:
    {criterion id: (level 1-4 or None, note)}; only criteria the applicant
    answered count. An entry with no level clears that score. Returns how
    many were saved."""
    saved = 0
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return 0
        known = {r["criterion_id"] for r in conn.execute(
            "SELECT criterion_id FROM hr_answers WHERE application_id=?", (application_id,))}
        mine = {r["criterion_id"]: r["id"] for r in conn.execute(
            "SELECT * FROM hr_scores WHERE application_id=? AND source='answers'", (application_id,))
            if _mine(r, scorer_name, scorer_leader_id)}
        now = _stamp()
        for criterion_id, (level, note) in (entries or {}).items():
            if criterion_id not in known:
                continue
            level, note = _level(level), _block(note, SCORE_NOTE_MAX)
            if level is None:
                if criterion_id in mine:
                    conn.execute("DELETE FROM hr_scores WHERE id=?", (mine[criterion_id],))
                continue
            if criterion_id in mine:
                conn.execute("UPDATE hr_scores SET level=?, note=?, submitted=1, at=? WHERE id=?",
                             (level, note, now, mine[criterion_id]))
            else:
                conn.execute(
                    "INSERT INTO hr_scores (application_id, criterion_id, source, scorer_name, "
                    "scorer_leader_id, level, note, submitted, at) VALUES (?,?,'answers',?,?,?,?,1,?)",
                    (application_id, criterion_id, _line(scorer_name, 80), scorer_leader_id, level,
                     note, now))
            saved += 1
        if saved:
            _event(conn, application_id, row["posting_id"], scorer_name, "score", "answers")
    return saved


def _score_rows(conn, application_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM hr_scores WHERE application_id=? ORDER BY id", (application_id,))]


def scores_for(application_id: int, scorer_name: str, scorer_leader_id: int | None,
               operator: bool = False) -> dict:
    """Everything the candidate page shows about scores, already limited
    to what this viewer may see:

    "mine": {criterion id: {"level", "note"}} for the written answers;
    "cards": every other submitted set of levels this viewer may see, each
        {"source", "interview_id", "scorer", "levels": {criterion id: level},
        "notes": {criterion id: note}};
    "my_cards": {interview id: {"submitted", "levels", "notes"}};
    "hidden": how many scorecards are held back until this viewer submits
        their own.

    Another leader's level for a written answer shows only once the viewer
    has scored that same answer; another interviewer's scorecard shows only
    once the viewer has submitted a scorecard for this candidate. The
    Operator sees them all."""
    with closing(people_db.connect()) as conn:
        rows = _score_rows(conn, application_id)
    mine, my_cards, cards = {}, {}, {}
    hidden: set = set()
    scored = {r["criterion_id"] for r in rows
              if r["source"] == "answers" and _mine(r, scorer_name, scorer_leader_id)}
    submitted_any = any(r["source"] == "interview" and r["submitted"]
                        and _mine(r, scorer_name, scorer_leader_id) for r in rows)
    for r in rows:
        own = _mine(r, scorer_name, scorer_leader_id)
        if r["source"] == "answers":
            if own:
                mine[r["criterion_id"]] = {"level": r["level"], "note": r["note"]}
                continue
            if not operator and r["criterion_id"] not in scored:
                continue
            key = ("answers", None, r["scorer_name"], r["scorer_leader_id"])
        else:
            if own:
                card = my_cards.setdefault(r["interview_id"],
                                           {"submitted": False, "levels": {}, "notes": {}})
                card["submitted"] = card["submitted"] or bool(r["submitted"])
                card["levels"][r["criterion_id"]] = r["level"]
                card["notes"][r["criterion_id"]] = r["note"]
            if not r["submitted"]:
                continue
            if not own and not operator and not submitted_any:
                hidden.add((r["interview_id"], r["scorer_name"], r["scorer_leader_id"]))
                continue
            key = ("interview", r["interview_id"], r["scorer_name"], r["scorer_leader_id"])
        card = cards.setdefault(key, {"source": key[0], "interview_id": key[1], "scorer": key[2],
                                      "levels": {}, "notes": {}})
        card["levels"][r["criterion_id"]] = r["level"]
        card["notes"][r["criterion_id"]] = r["note"]
    for criterion_id, own in mine.items():                   # the viewer's own answers column
        card = cards.setdefault(("answers", None, scorer_name, scorer_leader_id),
                                {"source": "answers", "interview_id": None, "scorer": scorer_name,
                                 "levels": {}, "notes": {}})
        card["levels"][criterion_id] = own["level"]
        card["notes"][criterion_id] = own["note"]
    ordered = sorted(cards.values(), key=lambda c: (c["source"] != "answers", c["interview_id"] or 0,
                                                    c["scorer"].casefold()))
    return {"mine": mine, "cards": ordered, "my_cards": my_cards, "hidden": len(hidden)}


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------

_CLOCK = re.compile(r"([0-9]{1,2})(?:[:.h]([0-5][0-9]))?\s*(a\.?m\.?|p\.?m\.?)?", re.I)


def clock_time(text) -> str | None:
    """A time of day as people write it ("15:30", "3:30 pm", "3pm", "9 a.m.")
    as HH:MM; "" for nothing typed; None when it cannot be read."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    match = _CLOCK.fullmatch(text)
    if not match or (match.group(2) is None and not match.group(3)):     # a bare "15" could mean anything
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half.startswith("p") else 0)
    return f"{hour:02d}:{minute:02d}" if hour <= 23 else None


def add_interview(application_id: int, on_date: str, at_time: str, mode: str, interviewer: str,
                  by: str) -> str | None:
    """Log an interview (in person, phone or video; planned or already
    held). Returns None, or why it could not be saved."""
    day = _as_date(on_date)
    today = _today()
    if not day or str(on_date).strip() != day.isoformat():
        return "Enter the interview date."
    if not today - timedelta(days=366) <= day <= today + timedelta(days=366):
        return "That interview date is more than a year away."
    at_time = clock_time(at_time)
    if at_time is None:
        return "Enter the time like 3:30 pm or 15:30, or leave it empty."
    if mode not in INTERVIEW_MODES:
        return "Choose how the interview is held."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return "That application isn't there any more."
        if row["stage"] not in ACTIVE_STAGES and day > today:
            # Decided already: an interview that was really held can still
            # be put on record (it starts the 45-day clock), a new one can't.
            return ("This application has been decided, so only an interview that has already "
                    "been held can be added.")
        conn.execute(
            "INSERT INTO hr_interviews (application_id, on_date, at_time, mode, interviewer, "
            "created_at, created_by) VALUES (?,?,?,?,?,?,?)",
            (application_id, day.isoformat(), at_time, mode, _line(interviewer, 80), _stamp(),
             _line(by, 80)))
        _event(conn, application_id, row["posting_id"], by, "interview", mode)
    return None


def interviews(application_id: int) -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hr_interviews WHERE application_id=? ORDER BY on_date, at_time, id",
            (application_id,))]


NOTICE_TAKEN_OFF = ("This interview is marked attended, so the applicant is owed a decision notice by "
                    "{deadline}. Changing it takes that notice off the list. Tick the box to say the "
                    "interview was not held, then record it again.")
NOTICE_OVERDUE_OPERATOR = ("The decision notice for this interview is overdue, so only the Operator "
                           "can change what happened to it.")


def _notice_lost(conn, application_id: int, on_date: str) -> dict | None:
    """The decision notice still owed that would come off the list if the
    attended interview on `on_date` stopped counting, or None."""
    held = [r["on_date"] for r in conn.execute(
        "SELECT on_date FROM hr_interviews WHERE application_id=? AND outcome='attended'",
        (application_id,))]
    given = [r["given_on"] for r in conn.execute(
        "SELECT given_on FROM hr_notices WHERE application_id=?", (application_id,))]
    without = list(held)
    if on_date in without:
        without.remove(on_date)
    still = {(c["interviewed"], c["deadline"]) for c in notice_obligations(without, given)
             if c["status"] in ("due", "overdue")}
    for clock in notice_obligations(held, given):
        if clock["status"] in ("due", "overdue") and (clock["interviewed"], clock["deadline"]) not in still:
            return clock
    return None


def set_interview_outcome(interview_id: int, application_id: int, outcome: str, by: str,
                          confirmed: bool = False, operator: bool = False) -> str | None:
    """Record what happened to an interview. An attended interview starts
    the 45-day clock for a decision notice, so it cannot be dated in the
    future, and it cannot be un-attended once a scorecard is in. Changing
    an attended interview to anything else while its notice is still owed
    needs `confirmed` (a tick that it was not held), and once that notice
    is overdue only the Operator may do it."""
    if outcome not in INTERVIEW_OUTCOMES or outcome == "scheduled":
        return "Choose what happened."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT i.*, a.posting_id FROM hr_interviews i JOIN hr_applications a ON a.id=i.application_id "
            "WHERE i.id=? AND i.application_id=? AND a.purged_at IS NULL",
            (interview_id, application_id)).fetchone()
        if not row:
            return "That interview isn't there any more."
        if row["outcome"] == outcome:
            return "That was already recorded."
        if outcome in ("attended", "no-show") and row["on_date"] > _today().isoformat():
            return "That interview is dated in the future. Change it once it has happened."
        if row["outcome"] == "attended" and conn.execute(
                "SELECT 1 FROM hr_scores WHERE interview_id=? AND submitted=1", (interview_id,)).fetchone():
            return "A scorecard has been submitted for that interview, so it stays as attended."
        if row["outcome"] == "attended":
            lost = _notice_lost(conn, application_id, row["on_date"])
            if lost and lost["status"] == "overdue" and not operator:
                return NOTICE_OVERDUE_OPERATOR
            if lost and not confirmed:
                day = _as_date(lost["deadline"])
                return NOTICE_TAKEN_OFF.format(deadline=f"{day:%B} {day.day}, {day.year}")
        now = _stamp()
        conn.execute("UPDATE hr_interviews SET outcome=?, outcome_at=?, outcome_by=? WHERE id=? AND outcome=?",
                     (outcome, now, _line(by, 80), interview_id, row["outcome"]))
        _event(conn, application_id, row["posting_id"], by, "outcome", outcome, at=now)
    return None


def save_scorecard(interview_id: int, application_id: int, scorer_name: str,
                   scorer_leader_id: int | None, entries: dict, submit: bool) -> str | None:
    """This interviewer's scorecard for one attended interview: a level and
    a note for each criterion, the same criteria in the same order for
    every candidate. Saved as a draft until submitted; a submitted
    scorecard is final. Returns None, or why it could not be saved."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT i.outcome, a.posting_id FROM hr_interviews i JOIN hr_applications a "
            "ON a.id=i.application_id WHERE i.id=? AND i.application_id=? AND a.purged_at IS NULL",
            (interview_id, application_id)).fetchone()
        if not row:
            return "That interview isn't there any more."
        if row["outcome"] != "attended":
            return "A scorecard is for an interview that was attended. Record that first."
        mine = {r["criterion_id"]: r for r in conn.execute(
            "SELECT * FROM hr_scores WHERE application_id=? AND source='interview' AND interview_id=?",
            (application_id, interview_id)) if _mine(r, scorer_name, scorer_leader_id)}
        if any(r["submitted"] for r in mine.values()):
            return "You have already submitted your scorecard for that interview."
        known = [c["id"] for c in _criteria(conn, row["posting_id"])]
        cleaned = {}
        for criterion_id in known:
            level, note = (entries or {}).get(criterion_id, (None, ""))
            cleaned[criterion_id] = (_level(level), _block(note, SCORE_NOTE_MAX))
        if submit and not any(level for level, _ in cleaned.values()):
            return "Choose a level for at least one criterion before you submit."
        now = _stamp()
        for criterion_id, (level, note) in cleaned.items():
            if criterion_id in mine:
                conn.execute("UPDATE hr_scores SET level=?, note=?, submitted=?, at=? WHERE id=?",
                             (level, note, 1 if submit else 0, now, mine[criterion_id]["id"]))
            elif level is not None or note or submit:
                conn.execute(
                    "INSERT INTO hr_scores (application_id, criterion_id, source, interview_id, "
                    "scorer_name, scorer_leader_id, level, note, submitted, at) "
                    "VALUES (?,?,'interview',?,?,?,?,?,?,?)",
                    (application_id, criterion_id, interview_id, _line(scorer_name, 80),
                     scorer_leader_id, level, note, 1 if submit else 0, now))
        if submit:
            _event(conn, application_id, row["posting_id"], scorer_name, "scorecard", "submitted", at=now)
    return None


# ---------------------------------------------------------------------------
# Decision notices and the 45-day clock
# ---------------------------------------------------------------------------

def notice_obligations(interview_dates: list, notice_dates: list, today: date | None = None) -> list[dict]:
    """What is owed to one interviewed applicant, from the dates of the
    interviews they attended and the dates notices were given.

    The applicant must be told whether a hiring decision has been made
    within 45 days after their interview, or after the last one when there
    were several. A later interview moves the deadline only if it was held
    before the deadline had passed; interviews further apart each start
    their own clock. A notice counts for a clock only if it was given on or
    after the last interview of that clock.

    Returns one dict per clock, oldest first: {"interviewed" (the last
    interview date), "deadline", "given_on" (or None), "status": "on-time" |
    "late" | "due" | "overdue", "days_left"}."""
    today = today or _today()
    days = sorted({d for d in (_as_date(x) for x in interview_dates or []) if d})
    given = sorted(d for d in (_as_date(x) for x in notice_dates or []) if d)
    clocks: list[list[date]] = []
    for day in days:
        if clocks and day <= clocks[-1][1] + timedelta(days=NOTICE_DAYS):
            clocks[-1][1] = day
        else:
            clocks.append([day, day])
    out = []
    for _, last in clocks:
        deadline = last + timedelta(days=NOTICE_DAYS)
        told = next((g for g in given if g >= last), None)
        if told:
            status = "on-time" if told <= deadline else "late"
        else:
            status = "overdue" if today > deadline else "due"
        out.append({"interviewed": last.isoformat(), "deadline": deadline.isoformat(),
                    "given_on": told.isoformat() if told else None, "status": status,
                    "days_left": (deadline - today).days})
    return out


def _obligations(conn, where: str = "", params: tuple = ()) -> list[dict]:
    """Every clock for every interviewed applicant (optionally limited by a
    WHERE on hr_applications a), with who it is for."""
    today = _today()
    attended: dict[int, list] = {}
    for r in conn.execute(
            "SELECT i.application_id, i.on_date FROM hr_interviews i JOIN hr_applications a "
            "ON a.id=i.application_id WHERE i.outcome='attended' " + where, params):
        attended.setdefault(r["application_id"], []).append(r["on_date"])
    if not attended:
        return []
    given: dict[int, list] = {}
    for r in conn.execute("SELECT application_id, given_on FROM hr_notices"):
        given.setdefault(r["application_id"], []).append(r["given_on"])
    people = {r["id"]: r for r in conn.execute(
        "SELECT a.id, a.first_name, a.last_name, a.stage, p.title FROM hr_applications a "
        "JOIN hr_postings p ON p.id=a.posting_id")}
    out = []
    for application_id, dates in attended.items():
        who = people.get(application_id)
        if not who:
            continue
        for clock in notice_obligations(dates, given.get(application_id, []), today):
            out.append({**clock, "application_id": application_id, "name": _name(who),
                        "posting_title": who["title"], "stage": who["stage"]})
    return out


def notice_board() -> dict:
    """The notices page: "owed" (every interviewed applicant with no notice
    since their last interview, soonest deadline first, each with
    "overdue", "past_aim" and "days_left"), "follow_up" (told "no decision
    yet" and not told anything since) and "log" (notices given, newest
    first)."""
    with closing(people_db.connect()) as conn:
        owed = [o for o in _obligations(conn) if o["status"] in ("due", "overdue")]
        log = [dict(r) for r in conn.execute(
            "SELECT n.*, p.title AS posting_title FROM hr_notices n "
            "JOIN hr_applications a ON a.id=n.application_id JOIN hr_postings p ON p.id=a.posting_id "
            "ORDER BY n.given_on DESC, n.id DESC LIMIT 300")]
        waiting = _told_not_yet(conn, {o["application_id"] for o in owed})
    owed.sort(key=lambda o: (o["deadline"], o["application_id"]))
    for o in owed:
        o["overdue"] = o["status"] == "overdue"
        # past our own 30-day aim, though still inside the law's 45 days
        o["past_aim"] = not o["overdue"] and o["days_left"] < NOTICE_DAYS - NOTICE_AIM_DAYS
    return {"owed": owed, "log": log, "follow_up": waiting}


FOLLOW_UP_STAGES = ("offer", "hired", "not-selected")     # a decision has been made about them


def _told_not_yet(conn, skip: set = frozenset()) -> list[dict]:
    """Applicants whose latest notice says "no decision yet": they were
    promised word when there is one, so they stay listed until an offer or
    a not-moving-forward notice is logged. "decided" marks the ones whose
    application has since been decided. Anyone in `skip` is already owed a
    notice for a later interview."""
    out = []
    for r in conn.execute(
            "SELECT a.id, a.first_name, a.last_name, a.stage, p.title, n.given_on FROM hr_applications a "
            "JOIN hr_postings p ON p.id=a.posting_id JOIN hr_notices n ON n.id = (SELECT id FROM hr_notices "
            "WHERE application_id=a.id ORDER BY given_on DESC, id DESC LIMIT 1) "
            "WHERE a.purged_at IS NULL AND n.kind='no-decision-yet' ORDER BY n.given_on, a.id"):
        if r["id"] not in skip:
            out.append({"application_id": r["id"], "name": _name(r), "posting_title": r["title"],
                        "told_on": r["given_on"], "stage": r["stage"],
                        "decided": r["stage"] in FOLLOW_UP_STAGES})
    return out


def notices_to_follow_up() -> int:
    """How many people told "no decision yet" now have a decision waiting
    to be passed on."""
    with closing(people_db.connect()) as conn:
        owed = {o["application_id"] for o in _obligations(conn) if o["status"] in ("due", "overdue")}
        return sum(1 for item in _told_not_yet(conn, owed) if item["decided"])


def clocks_for(application_id: int) -> list[dict]:
    """The 45-day clocks for one applicant, oldest first."""
    with closing(people_db.connect()) as conn:
        return _obligations(conn, "AND a.id=?", (application_id,))


def log_notice(application_id: int, kind: str, method: str, said: str, given_on: str,
               by: str) -> str | None:
    """Record what an applicant was told about the hiring decision, how and
    when. For an applicant who was interviewed the record keeps their name
    with it for three years; for one who never was, it goes when their
    details are removed (see purge_all). Returns None, or why it could not
    be saved."""
    if kind not in NOTICE_KINDS:
        return "Choose what the applicant was told."
    if method not in NOTICE_METHODS:
        return "Choose how they were told."
    day = _as_date(given_on)
    if not day or str(given_on).strip() != day.isoformat():
        return "Enter the date they were told."
    if day > _today():
        return "The date they were told can't be in the future."
    text = _block(said, SAID_MAX)
    if not text:
        return "Write what was said or sent."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row:
            return "That application isn't there any more."
        if day < (_as_date(row["created_at"]) or day):
            return "The date they were told can't be before they applied."
        conn.execute(
            "INSERT INTO hr_notices (application_id, kind, method, said, given_on, logged_at, "
            "logged_by, given_to) VALUES (?,?,?,?,?,?,?,?)",
            (application_id, kind, method, text, day.isoformat(), _stamp(), _line(by, 80), _name(row)))
        _event(conn, application_id, row["posting_id"], by, "notice", kind)
    return None


def notices(application_id: int) -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hr_notices WHERE application_id=? ORDER BY given_on DESC, id DESC",
            (application_id,))]


def _offer_open(conn, application_id: int) -> bool:
    """An offer has been made and not yet answered."""
    row = conn.execute("SELECT kind FROM hr_events WHERE application_id=? AND kind IN ('offer','offer_reply') "
                       "ORDER BY id DESC LIMIT 1", (application_id,)).fetchone()
    return bool(row) and row["kind"] == "offer"


def offer_state(application_id: int) -> dict:
    """{"open": an offer is waiting for an answer, "last_reply": "accepted",
    "declined" or None}."""
    with closing(people_db.connect()) as conn:
        reply = conn.execute("SELECT detail FROM hr_events WHERE application_id=? AND kind='offer_reply' "
                             "ORDER BY id DESC LIMIT 1", (application_id,)).fetchone()
        return {"open": _offer_open(conn, application_id),
                "last_reply": reply["detail"] if reply else None}


def log_offer(application_id: int, by: str) -> str | None:
    """Record that an offer was made (the application must be at the offer
    stage, with no earlier offer still unanswered)."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row or row["stage"] != "offer":
            return "Move the application to the offer stage first."
        if _offer_open(conn, application_id):
            return "An offer is already waiting for an answer."
        _event(conn, application_id, row["posting_id"], by, "offer", "made")
    return None


def log_offer_reply(application_id: int, reply: str, by: str) -> str | None:
    """Record the applicant's answer to the offer. It never moves the
    application: a person does that."""
    if reply not in OFFER_REPLIES:
        return "Choose accepted or declined."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _open_application(conn, application_id)
        if not row or not _offer_open(conn, application_id):
            return "There is no offer waiting for an answer."
        _event(conn, application_id, row["posting_id"], by, "offer_reply", reply)
    return None


def log_event(application_id: int, kind: str, by: str, detail: str = "") -> bool:
    """One row in an application's history for something recorded outside
    this file (the roster hand-off, an AI request). Fixed labels only."""
    if kind not in EVENT_KINDS:
        return False
    with closing(people_db.connect()) as conn, conn:
        row = conn.execute("SELECT posting_id FROM hr_applications WHERE id=?", (application_id,)).fetchone()
        if not row:
            return False
        _event(conn, application_id, row["posting_id"], by, kind, detail)
    return True


def counts() -> dict:
    """For the menu badge: new applications, and decision notices due
    within a week or overdue."""
    with closing(people_db.connect()) as conn:
        new = conn.execute("SELECT COUNT(*) FROM hr_applications WHERE stage='new' "
                           "AND purged_at IS NULL").fetchone()[0]
        owed = [o for o in _obligations(conn) if o["status"] in ("due", "overdue")]
    return {"new": new,
            "notices_due": sum(1 for o in owed if o["status"] == "due"
                               and o["days_left"] <= NOTICE_SOON_DAYS),
            "notices_overdue": sum(1 for o in owed if o["status"] == "overdue")}


# ---------------------------------------------------------------------------
# Metrics: every number comes from the event log
# ---------------------------------------------------------------------------

METRIC_PERIODS = (30, 90, 365)


def _share(count: int, of: int) -> dict:
    """A count beside its percentage; no percentage below five cases."""
    return {"count": count, "of": of,
            "pct": round(100 * count / of) if of >= SMALL_NUMBER else None}


def _middle(values: list[float]) -> dict:
    """A median with how many cases it rests on; none below five."""
    return {"n": len(values),
            "median": round(median(values), 1) if len(values) >= SMALL_NUMBER else None}


def metrics(posting_id: int | None = None, days: int = 90) -> dict:
    """The hiring dashboard for applications received in the last `days`
    days (30, 90 or 365), for one posting or all of them."""
    days = days if days in METRIC_PERIODS else 90
    since = (shift_db.now_local() - timedelta(days=days)).strftime(_STAMP)
    where, params = "a.created_at >= ?", [since]
    if posting_id:
        where += " AND a.posting_id = ?"
        params.append(posting_id)
    with closing(people_db.connect()) as conn:
        apps = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT a.id, a.created_at, a.answered_at, a.heard_from, a.stage FROM hr_applications a "
            f"WHERE {where}", params)}
        evs = [dict(r) for r in conn.execute(
            f"SELECT e.* FROM hr_events e JOIN hr_applications a ON a.id=e.application_id "
            f"WHERE {where} ORDER BY e.id", params)]
        talks = [dict(r) for r in conn.execute(
            f"SELECT i.outcome FROM hr_interviews i JOIN hr_applications a ON a.id=i.application_id "
            f"WHERE {where}", params)]
        clocks = _obligations(conn, "AND " + where, tuple(params))
    total = len(apps)
    reached = {stage: set() for stage in MAIN_LINE[1:]}
    hired_at: dict[int, str] = {}
    first_contact: dict[int, str] = {}
    entered: dict[int, dict] = {a: {"new": row["created_at"]} for a, row in apps.items()}
    in_stage: dict[str, list[float]] = {stage: [] for stage in ACTIVE_STAGES}
    replies = {"accepted": 0, "declined": 0}
    for e in evs:
        app_id = e["application_id"]
        if e["kind"] == "stage":
            if e["to_stage"] in MAIN_LINE[1:]:
                for stage in MAIN_LINE[1:MAIN_LINE.index(e["to_stage"]) + 1]:
                    reached[stage].add(app_id)
            if e["to_stage"] == "hired":
                hired_at.setdefault(app_id, e["at"])
            came = entered.setdefault(app_id, {}).pop(e["from_stage"], None)
            start, end = _as_time(came), _as_time(e["at"])
            if e["from_stage"] in in_stage and start and end and end >= start:
                in_stage[e["from_stage"]].append((end - start).total_seconds() / 86400)
            entered[app_id][e["to_stage"]] = e["at"]
        elif e["kind"] in ("contacted", "interview"):
            first_contact.setdefault(app_id, e["at"])
        elif e["kind"] == "offer_reply" and e["detail"] in replies:
            replies[e["detail"]] += 1
    steps = [("Applications", total, None)]
    before = total
    for stage in MAIN_LINE[1:]:
        n = len(reached[stage])
        steps.append((STAGE_LABELS[stage] if stage == "hired" else f"Reached {STAGE_LABELS[stage].lower()}",
                      n, _share(n, before)))
        before = n

    def gap(app_id: int, later: str, unit: float) -> float | None:
        start, end = _as_time(apps[app_id]["created_at"]), _as_time(later)
        return (end - start).total_seconds() / unit if start and end and end >= start else None

    to_hire = [g for g in (gap(a, at, 86400) for a, at in hired_at.items()) if g is not None]
    to_contact = [g for g in (gap(a, at, 3600) for a, at in first_contact.items()) if g is not None]
    heard: dict[str, int] = {}
    hires: dict[str, int] = {}
    for app_id, row in apps.items():
        heard[row["heard_from"]] = heard.get(row["heard_from"], 0) + 1
        if app_id in hired_at:
            hires[row["heard_from"]] = hires.get(row["heard_from"], 0) + 1
    sources = [{"key": key, "label": HEARD_LABELS.get(key, "Not said"), "applied": heard.get(key, 0),
                "hired": hires.get(key, 0)}
               for key in [k for k, _ in HEARD_FROM] + [""] if heard.get(key)]
    shows = sum(1 for t in talks if t["outcome"] == "attended")
    no_shows = sum(1 for t in talks if t["outcome"] == "no-show")
    answered = replies["accepted"] + replies["declined"]
    return {
        "days": days, "posting_id": posting_id, "applications": total,
        "funnel": [{"label": label, "count": n, "share": share} for label, n, share in steps],
        "answered": _share(sum(1 for r in apps.values() if r["answered_at"]), total),
        "days_to_hire": _middle(to_hire),
        "days_in_stage": [{"stage": stage, "label": STAGE_LABELS[stage], **_middle(in_stage[stage])}
                          for stage in ACTIVE_STAGES],
        "hours_to_contact": _middle(to_contact),
        "sources": sources, "hired": len(hired_at),
        "no_show": _share(no_shows, shows + no_shows),
        "offers": _share(replies["accepted"], answered),
        "notices": {status: sum(1 for c in clocks if c["status"] == status)
                    for status in ("on-time", "late", "due", "overdue")},
    }


# ---------------------------------------------------------------------------
# Retention: the Operator's purge
# ---------------------------------------------------------------------------

def _due(conn, today: date) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT a.id, a.first_name, a.last_name, a.stage, a.stage_at, a.keep_months, p.title "
            "FROM hr_applications a JOIN hr_postings p ON p.id=a.posting_id "
            "WHERE a.stage IN ('not-selected','withdrawn') AND a.hold=0 AND a.purged_at IS NULL "
            "ORDER BY a.stage_at, a.id"):
        decided = _as_date(r["stage_at"])
        if decided and _add_months(decided, int(r["keep_months"])) <= today:
            out.append({"id": r["id"], "name": _name(r), "stage": r["stage"],
                        "decided_on": decided.isoformat(), "keep_months": r["keep_months"],
                        "posting_title": r["title"]})
    return out


def retention() -> dict:
    """The retention page: "due" (not hired, decided longer ago than the
    months the applicant was told, not on hold, not yet purged), "held"
    (on hold, so never purged), "undecided" (how many applications on
    closed postings still have no decision, so their clock has not
    started) and "purged" (how many have already had their details
    removed)."""
    with closing(people_db.connect()) as conn:
        held = [{"id": r["id"], "name": _name(r), "posting_title": r["title"]} for r in conn.execute(
            "SELECT a.id, a.first_name, a.last_name, p.title FROM hr_applications a "
            "JOIN hr_postings p ON p.id=a.posting_id WHERE a.hold=1 AND a.purged_at IS NULL")]
        undecided = conn.execute(
            "SELECT COUNT(*) FROM hr_applications a JOIN hr_postings p ON p.id=a.posting_id "
            "WHERE p.status='closed' AND a.stage IN ('new','screening','interview','offer')").fetchone()[0]
        purged = conn.execute("SELECT COUNT(*) FROM hr_applications WHERE purged_at IS NOT NULL").fetchone()[0]
        return {"due": _due(conn, _today()), "held": held, "undecided": undecided, "purged": purged}


def purge(by: str) -> int:
    """Remove the personal details of every application that is due (see
    purge_all). Returns how many applications were purged."""
    return purge_all(by)[0]


def purge_all(by: str) -> tuple[int, int]:
    """Remove the personal details of every application that is due (see
    retention()). For each: the name becomes "Removed"; the email, phone,
    duplicate-check key, private link, yes/no answers, availability,
    referrer, experience, written answers, leaders' notes, score notes and
    any AI copies of the answers are blanked or deleted. The row, its stage
    history and its levels stay. A decision notice given to someone who
    was interviewed stays, with their name, until three full years after
    the day it was given (after that the wording and the name go, in this
    same run, unless the application is on hold): that is the record the
    law asks for. A notice logged for someone who was never interviewed is
    not that record, and applicants are told their name goes with the rest:
    its wording and the name are removed with the application. Hired
    applicants, applications on hold, postings and the kept copies of
    postings are never touched. Returns (applications purged, notices more
    than three years old whose wording was removed)."""
    today = _today()
    now = _stamp()
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        due = _due(conn, today)
        for item in due:
            application_id = item["id"]
            cur = conn.execute(
                "UPDATE hr_applications SET first_name='Removed', last_name='', email='', phone='', "
                "contact_key='', work_entitled='', adult='', availability='{}', referrer='', "
                "experience='', token_hash='', duplicate_of=NULL, purged_at=?, purged_by=? "
                "WHERE id=? AND purged_at IS NULL AND hold=0 AND stage IN ('not-selected','withdrawn')",
                (now, _line(by, 80), application_id))
            if not cur.rowcount:
                continue
            conn.execute("DELETE FROM hr_answers WHERE application_id=?", (application_id,))
            conn.execute("DELETE FROM hr_notes WHERE application_id=?", (application_id,))
            conn.execute("UPDATE hr_scores SET note='' WHERE application_id=?", (application_id,))
            conn.execute("UPDATE ai_jobs SET input_json=NULL, raw_output=NULL, output_json=NULL "
                         "WHERE kind='hiring_score' AND target_id=?", (application_id,))
            posting_id = conn.execute("SELECT posting_id FROM hr_applications WHERE id=?",
                                      (application_id,)).fetchone()[0]
            _event(conn, application_id, posting_id, by, "purged", at=now)
        # A notice is kept for three years after the day it was given: the
        # whole of its third anniversary is still inside that, so only a
        # notice given before this day three years ago goes. A record on
        # hold is never removed, and that covers its notices.
        # A notice to someone who never had an interview is not the
        # three-year record: it goes with the rest of their details.
        conn.execute(
            "UPDATE hr_notices SET said='', given_to='' WHERE (said != '' OR given_to != '') "
            "AND application_id IN (SELECT id FROM hr_applications WHERE purged_at IS NOT NULL AND hold=0) "
            "AND application_id NOT IN (SELECT application_id FROM hr_interviews WHERE outcome='attended')")
        cutoff = _add_months(today, -NOTICE_KEEP_MONTHS).isoformat()
        notices = conn.execute(
            "UPDATE hr_notices SET said='', given_to='' WHERE given_on < ? AND (said != '' OR given_to != '') "
            "AND application_id IN (SELECT id FROM hr_applications WHERE purged_at IS NOT NULL AND hold=0)",
            (cutoff,)).rowcount
    return len(due), notices


def records_export() -> str:
    """The records an employment standards officer may ask to see, as one
    JSON document: every posting with every kept copy (the posting as shown
    and the blank application form) and the date it came down, and every
    decision notice given to an interviewed applicant."""
    with closing(people_db.connect()) as conn:
        conn.execute("BEGIN")
        posted = []
        for p in conn.execute("SELECT id, slug, title, status, published_at, closed_at FROM hr_postings "
                              "WHERE status != 'draft' ORDER BY id").fetchall():
            item = dict(p)
            item["copies"] = [dict(s) for s in conn.execute(
                "SELECT taken_at, taken_by, reason, ai_statement, body FROM hr_snapshots "
                "WHERE posting_id=? ORDER BY id", (p["id"],))]
            posted.append(item)
        given = [dict(r) for r in conn.execute(
            "SELECT n.application_id, p.title AS posting, n.given_to, n.kind, n.method, n.given_on, "
            "n.said, n.logged_at, n.logged_by FROM hr_notices n JOIN hr_applications a "
            "ON a.id=n.application_id JOIN hr_postings p ON p.id=a.posting_id ORDER BY n.given_on, n.id")]
        conn.commit()
    return json.dumps({"format": 1, "kind": "hiring-records", "exported_at": _stamp(),
                       "postings": posted, "decision_notices": given}, ensure_ascii=False, indent=1)
