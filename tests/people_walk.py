"""The People section walked end to end, the way the Operator meets it on
day one (no tests of its own: test_people_privacy.py and
test_people_roles.py build on it).

first_run() starts from a store where nobody has been given access and no
standards are loaded, and goes through every step by the same pages, links
and forms a person would use: unlock, who has access, our standards (the
example file), leaders, a review cycle, a reflection, an observation, the
ratings, the "before you finish" questions, sharing, the leader's own
sign-in and their acknowledgement; then settings, a posting, the publish
check, the public careers pages, the quick questions, the pipeline, an
interview, a scorecard, the decision notice, the roster hand-off and the
numbers. At each step it checks that the page really offers the next one,
so a dead end fails here.

Every response is kept in Walk.seen and every private string that went in
is kept in Walk.private, for the privacy checks. Everyone is invented.
"""

import io
import json
import re
from datetime import timedelta

import app as cfa_app
import people_db
import people_hiring_db as hdb
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit

APPLICANT = {"first": "Jordan", "last": "Quillfeather", "email": "jordan.quill@example.com",
             "phone": "519-555-0142"}
CONTACT = {"name": "Morgan Example", "email": "hiring@example.com"}
OBSERVATION = "Opened the dining room ten minutes early and walked the zinnia checklist with the team."
COMMENT = "Steady through the kumquat rush on Saturday."
STRENGTHS = "Keeps the marigold standard on every open."
GROWTH = "Hand the larkspur close to a second leader."
REFLECTION = "I think my handovers in the juniper week were rushed."
ACK_COMMENT = "Thank you. I would like to talk about the tamarind schedule."
ANSWERS = ("I stayed late to finish the persimmon inventory when a teammate was away.",
           "A guest could not find the quince sauce, so I walked them to it and checked back.")
EXPERIENCE = "Two summers at the Fernhollow community pool."
INTERVIEW_NOTE = "Gave a clear example about the saffron order mix-up."
POSTING_TEXT = ("Serve guests at the counter and in the dining room, keep your station clean and "
                "stocked, and help your teammates through the rush. You will be trained on every "
                "station you work. Shifts are mornings, afternoons and evenings, six days a week.")


def day(offset: int = 0) -> str:
    return (shift_db.now_local().date() + timedelta(days=offset)).isoformat()


def ai_reply(system: str, user: str) -> dict:
    """A well-behaved AI service: it answers each kind of request in the
    shape that kind asks for, from what it was sent and nothing else."""
    asked = json.loads(user)
    data = asked["data"]
    if asked["kind"] == "review_draft":
        by_key: dict = {}
        for item in data["evidence"]:
            by_key.setdefault(item["dimension_key"], []).append(item["id"])
        every = [item["id"] for item in data["evidence"]]
        return {"dimensions": {s["key"]: {"evidence_summary": "The notes show steady work on this standard.",
                                          "cited": by_key.get(s["key"], []), "suggested_level": 3,
                                          "anchor_used": s["anchors"][2], "note": ""}
                               for s in data["standards"]},
                "strengths": [{"text": "Opens on time and briefs the team.", "cited": every[:1]}],
                "growth_areas": [], "coaching_questions": ["What would make the open easier?"],
                "human_checks": []}
    return {"criteria": [{"criterion_id": a["criterion_id"], "evidence_summary": "A clear example.",
                          "quote": " ".join(a["text"].split()[:4]), "suggested_level": 3,
                          "note": "Matches the third level description."}
                         for a in data["answers"]],
            "human_checks": []}


class Walk:
    """Clients for each person, every response they got, and what was
    typed in along the way."""

    def __init__(self, world: dict):
        self.world = world
        self.seen: list[tuple] = []          # (who, method, path, response)
        self.private: list[str] = []         # must never reach stdout, Slack or the shift backup
        self.ids: dict = {}
        self._clients: dict = {}

    def client(self, who: str):
        if who not in self._clients:
            self._clients[who] = cfa_app.app.test_client()
            if who != "public":
                kit.sign_in(self._clients[who], who)
        return self._clients[who]

    def get(self, who: str, path: str, expect: int | None = 200):
        resp = self.client(who).get(path)
        self.seen.append((who, "GET", path, resp))
        if expect is not None:
            assert resp.status_code == expect, (who, path, resp.status_code,
                                                resp.headers.get("Location"))
        return resp

    def page(self, who: str, path: str) -> str:
        return self.get(who, path).get_data(as_text=True)

    def post(self, who: str, path: str, data: dict | None = None, expect: int | None = 302, **more):
        resp = self.client(who).post(path, data=data or {}, **more)
        self.seen.append((who, "POST", path, resp))
        if expect is not None:
            assert resp.status_code == expect, (who, path, resp.status_code,
                                                resp.get_data(as_text=True)[:400])
        return resp

    def flashes(self, who: str) -> list[str]:
        with self.client(who).session_transaction() as sess:
            return [message for _, message in sess.get("_flashes", [])]

    def typed(self, *values: str) -> None:
        self.private.extend(v for v in values if v)

    def html(self) -> list[tuple]:
        """(who, method, path, response) for every HTML page that came back."""
        return [item for item in self.seen if item[3].mimetype == "text/html"]


def _unlock(walk: Walk, who: str) -> None:
    """The lock as a person meets it: a People page sends them to the PIN
    box, and the PIN brings them back to the page they asked for."""
    resp = walk.get(who, "/shift/people", expect=302)
    assert resp.headers["Location"].startswith("/shift/people/unlock"), resp.headers["Location"]
    assert "Enter your PIN" in walk.page(who, resp.headers["Location"])
    resp = walk.post(who, "/shift/people/unlock", {"pin": kit.PINS[who], "next": "/shift/people"})
    assert resp.headers["Location"] == "/shift/people"


def set_up(walk: Walk, ai: bool = False) -> None:
    """Unlock, who has access, our standards from the example file."""
    ids = walk.world["ids"]
    _unlock(walk, "operator")
    home = walk.page("operator", "/shift/people")
    assert "Start with Our standards" in home
    for link in ("/shift/people/access", "/shift/people/standards", "/shift/people/settings",
                 "/shift/reviews", "/shift/hiring"):
        assert f'href="{link}' in home, link

    assert "Give access" in walk.page("operator", "/shift/people/access")
    walk.post("operator", "/shift/people/access/grant", {"leader_id": ids["rae"], "capability": "reviewer"})
    walk.post("operator", "/shift/people/access/grant", {"leader_id": ids["hari"], "capability": "hiring"})
    assert people_db.capabilities(ids["rae"], "Rae") == {"reviewer"}
    assert people_db.capabilities(ids["hari"], "Hari") == {"hiring"}

    empty = walk.page("operator", "/shift/people/standards")
    assert "No standards are loaded yet." in empty and "/shift/people/standards/example" in empty
    example = walk.post("operator", "/shift/people/standards/example", expect=200)
    assert example.mimetype == "application/json"
    walk.post("operator", "/shift/people/standards/import",
              {"file": (io.BytesIO(example.get_data()), "example-standards.json")},
              content_type="multipart/form-data")
    assert "version 1" in walk.page("operator", "/shift/people/standards")
    assert "Start with Our standards" not in walk.page("operator", "/shift/people")
    if ai:
        for switch in ("ai_reviews", "ai_hiring"):
            walk.post("operator", "/shift/people/settings", {switch: "1"})
            assert people_db.setting_on(switch)


def reviews(walk: Walk, ai: bool = False) -> None:
    """Leaders, a cycle, a reflection, an observation, the review, the
    checks, sharing, and the leader's own side."""
    front = walk.page("operator", "/shift/reviews")
    assert 'href="/shift/reviews/leaders"' in front              # "Set up leaders"
    assert "Start with every Team Hub leader" in walk.page("operator", "/shift/reviews/leaders")
    walk.post("operator", "/shift/reviews/leaders/add", {"everyone": "1"})
    people = {s["display_name"]: s["id"] for s in rdb.subjects()}
    assert set(people) == {"Lena", "Ada", "Hari", "Rae"}
    subject = walk.ids["subject"] = people["Lena"]

    front = walk.page("operator", "/shift/reviews")
    assert 'action="/shift/reviews/cycles/new"' in front
    resp = walk.post("operator", "/shift/reviews/cycles/new",
                     {"name": "First check-in", "period_start": day(-60), "period_end": day(0),
                      "due_date": day(14), "ask_reflection": "1"})
    cycle = walk.ids["cycle"] = rdb.cycles()[0]["id"]
    assert resp.headers["Location"] == f"/shift/reviews/cycles/{cycle}"
    review = walk.ids["review"] = next(r["id"] for r in rdb.cycle_reviews(cycle)
                                       if r["subject_id"] == subject)
    page = walk.page("operator", f"/shift/reviews/cycles/{cycle}")
    assert f'href="/shift/reviews/review/{review}"' in page

    # the leader is asked for a reflection first: the menu takes them to it
    menu = walk.page("lena", "/shift/more")
    assert "My review" in menu and 'href="/shift/reviews/mine"' in menu
    resp = walk.get("lena", "/shift/reviews/mine", expect=302)   # locked: the PIN first
    resp = walk.post("lena", "/shift/people/unlock", {"pin": kit.PINS["lena"],
                                                      "next": "/shift/reviews/mine"})
    assert resp.headers["Location"] == "/shift/reviews/mine"
    mine = walk.page("lena", "/shift/reviews/mine")
    assert f'href="/shift/reviews/mine/{review}"' in mine
    assert "Your reflection" in walk.page("lena", f"/shift/reviews/mine/{review}")
    walk.typed(REFLECTION)
    walk.post("lena", f"/shift/reviews/mine/{review}/reflection",
              {"r_reliable": REFLECTION, "send": "1"})

    # an observation, from the leader's log
    log = walk.page("operator", f"/shift/reviews/leaders/{subject}")
    assert f'action="/shift/reviews/leaders/{subject}/observe"' in log
    walk.typed(OBSERVATION, COMMENT, STRENGTHS, GROWTH)
    walk.post("operator", f"/shift/reviews/leaders/{subject}/observe",
              {"obs_date": day(-3), "dimension_key": "reliable", "kind": "strength", "body": OBSERVATION})
    assert f'href="/shift/reviews/review/{review}"' in walk.page("operator", f"/shift/reviews/leaders/{subject}")

    form = f"/shift/reviews/review/{review}"
    page = walk.page("operator", form)
    assert OBSERVATION in page and REFLECTION in page and f'action="{form}/rate"' in page
    if ai:
        walk.post("operator", f"{form}/ai")
        assert "Draft prepared with AI assistance" not in walk.page("operator", form)   # own rating first
    for key in ("reliable", "guest-care"):
        walk.post("operator", f"{form}/rate", {"dimension_key": key, "level": "3", "comment": COMMENT})
    walk.post("operator", f"{form}/rate", {"dimension_key": "teamwork", "level": "not_enough"})
    if ai:
        assert "Draft prepared with AI assistance. Not a decision." in walk.page("operator", form)
    walk.post("operator", f"{form}/summary", {"strengths": STRENGTHS, "growth": GROWTH})
    walk.post("operator", f"{form}/actions/add",
              {"action": "Hand over one close a week", "measure": "Closes handed over",
               "target": "4 by month end", "check_date": day(30)})
    assert f'href="{form}/finalise"' in walk.page("operator", form)
    checks = walk.page("operator", f"{form}/finalise")
    assert "Before you finish" in checks
    walk.post("operator", f"{form}/finalise", {**{f"gate_{key}": "no" for key in rdb.GATE_KEYS},
                                               "wording_read": "1"})
    assert rdb.get_review(review)["status"] == "final", walk.flashes("operator")
    assert f'action="{form}/share"' in walk.page("operator", form)
    walk.post("operator", f"{form}/share")
    assert rdb.get_review(review)["status"] == "shared"
    walk.get("operator", f"{form}/print")
    walk.get("operator", f"/shift/reviews/calibration/{cycle}")
    walk.get("operator", "/shift/reviews/health")

    # the leader reads it, confirms it, and can still find it afterwards
    menu = walk.page("lena", "/shift/more")
    assert "1 waiting" in menu
    home = walk.page("lena", "/shift/people")
    assert 'href="/shift/reviews/mine"' in home and "Something is waiting for you." in home
    assert f'href="/shift/reviews/mine/{review}"' in walk.page("lena", "/shift/reviews/mine")
    shared = walk.page("lena", f"/shift/reviews/mine/{review}")
    assert OBSERVATION in shared and STRENGTHS in shared
    assert f'action="/shift/reviews/mine/{review}/acknowledge"' in shared
    walk.typed(ACK_COMMENT)
    walk.post("lena", f"/shift/reviews/mine/{review}/acknowledge",
              {"confirm": "1", "discussed_on": day(0), "comment": ACK_COMMENT})
    assert rdb.get_review(review)["status"] == "acknowledged", walk.flashes("lena")
    walk.get("lena", f"/shift/reviews/review/{review}/print")
    assert 'href="/shift/reviews/mine"' in walk.page("lena", "/shift/people")
    menu = walk.page("lena", "/shift/more")
    assert 'href="/shift/reviews/mine"' in menu and "waiting" not in menu


def hiring(walk: Walk, ai: bool = False) -> None:
    """Settings, a posting, the careers pages, the pipeline, an interview,
    the decision notice, the roster and the numbers."""
    assert 'name="hiring_contact_name"' in walk.page("operator", "/shift/people/settings")
    walk.post("operator", "/shift/people/settings",
              {"hiring_contact_name": CONTACT["name"], "hiring_contact_email": CONTACT["email"]})

    pipeline = walk.page("operator", "/shift/hiring")
    assert 'href="/shift/hiring/postings' in pipeline
    assert 'href="/shift/hiring/postings/new"' in walk.page("operator", "/shift/hiring/postings")
    walk.get("operator", "/shift/hiring/postings/new")
    resp = walk.post("operator", "/shift/hiring/postings/new",
                     {"title": "Team member", "employment_type": "part-time",
                      "description": POSTING_TEXT, "pay_min": "17.95", "pay_max": "19.50",
                      "vacancy": "existing"})
    posting = walk.ids["posting"] = hdb.postings()[0]["id"]
    at = f"/shift/hiring/postings/{posting}"
    assert resp.headers["Location"].startswith(at)
    page = walk.page("operator", at)
    assert f'action="{at}/criteria"' in page and 'value="reliable"' in page
    for key in ("reliable", "guest-care"):                    # picked from Our standards
        walk.post("operator", f"{at}/criteria", {"dimension_key": key})
    assert hdb.compliance_problems(hdb.get_posting(posting)) == []
    if ai:
        walk.post("operator", f"{at}/ai", {"on": "1"})
    walk.get("operator", f"{at}/edit")
    assert f'action="{at}/publish"' in walk.page("operator", at)
    walk.post("operator", f"{at}/publish")
    found = hdb.get_posting(posting)
    assert found["status"] == "open", walk.flashes("operator")
    slug = walk.ids["slug"] = found["slug"]
    walk.ids["snapshot"] = hdb.snapshots(posting)[0]["id"]
    assert f"/careers/{slug}" in walk.page("operator", at)
    walk.get("operator", f"{at}/copies/{walk.ids['snapshot']}")

    # the public side: someone who has never signed in
    listing = walk.page("public", "/careers/")
    assert f'href="/careers/{slug}"' in listing and "$17.95 to $19.50 an hour" in listing
    advert = walk.page("public", f"/careers/{slug}")
    assert f'href="/careers/{slug}/apply"' in advert and CONTACT["name"] in advert
    assert ("We use artificial intelligence (AI)" in advert) == ai
    form = walk.page("public", f"/careers/{slug}/apply")
    assert f'action="/careers/{slug}/apply"' in form
    walk.typed(APPLICANT["last"], APPLICANT["email"], APPLICANT["phone"], EXPERIENCE, *ANSWERS)
    sent = {"first_name": APPLICANT["first"], "last_name": APPLICANT["last"],
            "email": APPLICANT["email"], "phone": APPLICANT["phone"], "work_entitled": "yes",
            "adult": "no", "av_mon_morning": "1", "av_sat_evening": "1", "heard_from": "school",
            "experience": EXPERIENCE, "consent": "1"}
    if ai:
        sent["ai"] = re.search(r'name="ai" value="([^"]*)"', form).group(1)
    resp = walk.post("public", f"/careers/{slug}/apply", sent)
    private = resp.headers["Location"]
    assert re.fullmatch(r"/careers/a/[0-9a-f]{32}", private)
    walk.typed(private.rsplit("/", 1)[1])                     # the token itself
    walk.ids["private"] = private
    questions = walk.page("public", private)
    fields = re.findall(r'name="(q_\d+)"', questions)
    assert len(fields) == 2
    walk.post("public", private, dict(zip(fields, ANSWERS)))
    status = walk.page("public", private)
    assert "We have your application for Team member" in status and "name=\"q_" not in status

    # the pipeline, by the links on each page
    pipeline = walk.page("operator", "/shift/hiring")
    candidate = walk.ids["candidate"] = hdb.pipeline()[0]["stages"]["new"][0]["id"]
    at = f"/shift/hiring/candidates/{candidate}"
    assert f'href="{at}"' in pipeline and "<b>1</b> new" in pipeline
    page = walk.page("operator", at)
    assert ANSWERS[0] in page and f'action="{at}/stage"' in page and f'href="{at}/contact"' in page
    criteria = [c["id"] for c in hdb.get_posting(posting)["criteria"]]
    if ai:
        walk.post("operator", f"{at}/ai")
        assert "Suggested, for reviewer decision" not in walk.page("operator", at)   # own level first
    walk.post("operator", f"{at}/score", {f"level_{c}": "3" for c in criteria})
    if ai:
        assert "Suggested, for reviewer decision. Not a decision." in walk.page("operator", at)
    walk.post("operator", f"{at}/stage", {"from": "new", "to": "screening"})
    walk.post("operator", f"{at}/contacted", {"method": "email"})
    walk.post("operator", f"{at}/stage", {"from": "screening", "to": "interview"})
    walk.post("operator", f"{at}/interview", {"on_date": day(0), "at_time": "15:30",
                                              "mode": "in person", "interviewer": "Hari"})
    talk = hdb.interviews(candidate)[0]["id"]
    assert f'action="{at}/interviews/{talk}/outcome"' in walk.page("operator", at)
    walk.post("operator", f"{at}/interviews/{talk}/outcome", {"outcome": "attended"})
    walk.typed(INTERVIEW_NOTE)
    walk.post("operator", f"{at}/interviews/{talk}/scorecard",
              {**{f"level_{c}": "3" for c in criteria}, f"note_{criteria[0]}": INTERVIEW_NOTE,
               "action": "submit"})
    assert "<b>1</b> owed" in walk.page("operator", "/shift/hiring/notices")

    walk.post("operator", f"{at}/stage", {"from": "interview", "to": "offer"})
    contact = walk.page("operator", f"{at}/contact")
    assert APPLICANT["email"] in contact and "mailto:" in contact
    assert f'action="{at}/notice"' in contact and "Ask for proof of age only after a conditional offer." in contact
    walk.post("operator", f"{at}/offer")
    said = "A hiring decision has been made for this posting, and we are pleased to offer you the position."
    walk.post("operator", f"{at}/notice", {"kind": "offer", "method": "email", "said": said,
                                           "given_on": day(0)})
    assert len(hdb.notices(candidate)) == 1, walk.flashes("operator")
    assert "<b>0</b> owed" in walk.page("operator", "/shift/hiring/notices")
    assert "A hiring decision has been made for this posting." in walk.page("public", private)
    walk.post("operator", f"{at}/offer-reply", {"reply": "accepted"})
    walk.post("operator", f"{at}/stage", {"from": "offer", "to": "hired", "checks_done": "1"})
    assert hdb.candidate(candidate)["stage"] == "hired", walk.flashes("operator")
    walk.post("operator", f"{at}/roster")
    full_name = f"{APPLICANT['first']} {APPLICANT['last']}"
    assert any(m["name"] == full_name for m in shift_db.roster())

    numbers = walk.page("operator", "/shift/hiring/metrics")
    assert "<b>1</b> application" in numbers
    walk.get("operator", "/shift/hiring/retention")
    walk.post("operator", "/shift/hiring/records", expect=200)
    walk.get("operator", "/shift/people/audit")
    walk.get("operator", "/shift/people/backup")
    assert walk.post("operator", "/shift/people/backup", expect=200).mimetype == "application/json"


def first_run(world: dict, monkeypatch=None, ai: bool = False) -> Walk:
    """The whole path on a day-one store (see kit.make_world(bare=True)).
    ai=True also switches both AI switches on, with a stand-in for the AI
    service (needs monkeypatch)."""
    import careers
    careers.reset_throttles()
    walk = Walk(world)
    if ai:
        walk.ai_calls = kit.fake_ai(monkeypatch, ai_reply)
    set_up(walk, ai)
    reviews(walk, ai)
    hiring(walk, ai)
    return walk
