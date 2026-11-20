"""Hiring's leader pages (/shift/hiring...): who may open and do what,
postings and the publish check as the Operator sees them, the pipeline,
the candidate page as a scoring screen, interviews and scorecards,
messages and decision notices, the AI assist and its gates, the numbers
page, retention, and the hand-off of a new hire's name to the Team Hub
roster. Everyone here is invented."""

import json
import re
from urllib.parse import unquote

import pytest

import app as cfa_app
import people_ai
import people_db
import people_hiring
import people_hiring_db as hdb
import shift_db
from tests import people_testkit as kit
from tests.test_people_hiring_db import DESCRIPTION, ai_in_use, applied, clock, criterion, draft, live, table

EMAIL, PHONE = "jordan1@example.com", "519-555-0142"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    w = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    return w


def page(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.status_code, resp.headers.get("Location"))
    return resp.data.decode()


def shown(html: str) -> str:
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


def flashed(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


def post(client, path: str, **data):
    resp = client.post(path, data=data)
    assert resp.status_code == 302, (path, resp.status_code)
    return resp


def candidate(posting_id: int, n: int = 1, **changes) -> int:
    """An application with both quick questions answered."""
    app_id = applied(posting_id, n, phone=PHONE if n == 1 else "", heard_from="friend",
                     referrer="Alex Doe", **changes)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    assert hdb.save_answers(app_id, {first: "I opened the farm stand every Saturday for two summers.",
                                     second: "A guest was lost, so I walked her to her table."}) == "saved"
    return app_id


def get_pages(posting_id: int, app_id: int, snapshot_id: int) -> list[str]:
    return ["/shift/hiring/", "/shift/hiring/postings", "/shift/hiring/postings/new",
            f"/shift/hiring/postings/{posting_id}", f"/shift/hiring/postings/{posting_id}/edit",
            f"/shift/hiring/postings/{posting_id}/copies/{snapshot_id}",
            f"/shift/hiring/candidates/{app_id}", f"/shift/hiring/candidates/{app_id}/contact",
            "/shift/hiring/notices", "/shift/hiring/metrics"]


# --- who can open what ------------------------------------------------------------------

def test_hiring_pages_are_registered_and_linked_from_people_home(world):
    rules = {r.rule for r in cfa_app.app.url_map.iter_rules() if r.endpoint.startswith("people.hiring_")}
    assert {"/shift/hiring/", "/shift/hiring/postings", "/shift/hiring/notices", "/shift/hiring/metrics",
            "/shift/hiring/retention", "/shift/hiring/candidates/<int:application_id>"} <= rules
    assert 'href="/shift/hiring/"' in page(kit.open_as(world, "hari"), "/shift/people")
    assert "/shift/hiring" not in page(kit.open_as(world, "rae"), "/shift/people")
    assert kit.open_as(world, "hari").get("/shift/hiring").status_code == 200       # with or without the slash


@pytest.mark.parametrize("who", ["lena", "ada", "rae"])
def test_a_leader_without_the_hiring_grant_opens_nothing(world, who):
    posting_id = live()
    app_id = candidate(posting_id)
    client = kit.open_as(world, who)
    paths = get_pages(posting_id, app_id, hdb.snapshots(posting_id)[0]["id"]) + ["/shift/hiring/retention"]
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", (who, path)
        assert b"Jordan1" not in resp.data
    assert "That page isn't available on your sign-in." in flashed(client)
    # and can change nothing
    for path, data in [
        (f"/shift/hiring/candidates/{app_id}/stage", {"from": "new", "to": "screening"}),
        (f"/shift/hiring/candidates/{app_id}/note", {"body": "A note."}),
        (f"/shift/hiring/candidates/{app_id}/hold", {"on": "1"}),
        (f"/shift/hiring/candidates/{app_id}/ai", {}),
        (f"/shift/hiring/postings/{posting_id}/close", {"confirm": "1"}),
        ("/shift/hiring/postings/new", {"title": "Sneaky"}),
        ("/shift/hiring/retention/purge", {"confirm": "1"}),
        ("/shift/hiring/records", {}),
    ]:
        resp = client.post(path, data=data)
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", (who, path)
    assert hdb.candidate(app_id)["stage"] == "new" and hdb.notes(app_id) == []
    assert hdb.get_posting(posting_id)["status"] == "open" and len(hdb.postings()) == 1


def test_a_team_member_and_a_signed_out_visitor_never_reach_hiring(world):
    posting_id = live()
    app_id = candidate(posting_id)
    paths = get_pages(posting_id, app_id, hdb.snapshots(posting_id)[0]["id"]) + ["/shift/hiring/retention"]
    member = kit.open_as(world, "member")
    visitor = cfa_app.app.test_client()
    for path in paths:
        resp = member.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/"), path
        resp = visitor.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path
    hari = cfa_app.app.test_client()
    kit.sign_in(hari, "hari")                                            # signed in, PIN not entered again
    for path in paths:
        resp = hari.get(path)
        assert resp.status_code == 302 and "/shift/people/unlock" in resp.headers["Location"], path
    assert "/shift/hiring" not in page(member, "/hub/menu") and "People" not in shown(page(member, "/hub/menu"))


def test_every_hiring_page_opens_for_a_hiring_leader_and_the_operator(world):
    hub_csp = cfa_app.app.test_client().get("/hub/login").headers["Content-Security-Policy"]
    posting_id = live()
    app_id = candidate(posting_id)
    hdb.add_interview(app_id, shift_db.now_local().date().isoformat(), "15:30", "in person", "Hari", "Hari")
    hdb.set_interview_outcome(hdb.interviews(app_id)[0]["id"], app_id, "attended", "Hari")
    paths = get_pages(posting_id, app_id, hdb.snapshots(posting_id)[0]["id"])
    for who, extra in (("hari", []), ("operator", ["/shift/hiring/retention"])):
        client = kit.open_as(world, who)
        for path in paths + extra:
            resp = client.get(path)
            assert resp.status_code == 200, (who, path)
            assert resp.headers["Cache-Control"] == "private, no-store", path
            assert resp.headers["X-Frame-Options"] == "DENY" and resp.headers["X-Content-Type-Options"] == "nosniff"
            assert resp.headers["Referrer-Policy"] == "same-origin"
            assert resp.headers["Content-Security-Policy"] == hub_csp, path
            html = resp.data.decode()
            assert "<script" not in html.lower() and "javascript:" not in html.lower(), path
            assert not re.search(r"<[^>]+\son[a-z]+\s*=", html), path
            assert not re.search(r'(?:src|href)="https?://', html), path
            assert re.search(r'<section class="hero compact">\s*<p class="eyebrow">[^<]+</p>\s*'
                             r'<h1 class="headline">[^<]+</h1>\s*<p class="triage">', html), path
            assert '<nav class="tabbar"' in html and "Lock now" in html, path
    assert kit.open_as(world, "hari").get("/shift/hiring/retention").status_code == 302


def test_ids_that_are_not_there_get_one_answer(world):
    posting_id = live()
    client = kit.open_as(world, "hari")
    for path in ("/shift/hiring/postings/999", "/shift/hiring/postings/999/edit",
                 f"/shift/hiring/postings/{posting_id}/copies/999", "/shift/hiring/candidates/999",
                 "/shift/hiring/candidates/999/contact", "/shift/hiring/candidates/" + "9" * 40):
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "That isn't here, or it isn't yours to open." in flashed(client), path
    for path in ("/shift/hiring/candidates/999/stage", "/shift/hiring/candidates/999/score",
                 "/shift/hiring/candidates/999/ai", "/shift/hiring/candidates/999/note"):
        assert client.post(path, data={"body": "x"}).status_code == 302, path
    assert table("hr_events") == []


def test_only_the_operator_publishes_closes_switches_ai_purges_and_adds_to_the_roster(world, monkeypatch):
    clock(monkeypatch, "2024-01-10 09:00")
    people_db.set_setting("ai_hiring", "1", "Operator")
    posting_id = draft()
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    other = live(title="Kitchen team")
    hired = candidate(other, 1)
    old = candidate(other, 2)
    hdb.move_stage(hired, "new", "offer", "Hari")
    hdb.move_stage(hired, "offer", "hired", "Hari", checks_done=True)
    hdb.move_stage(old, "new", "withdrawn", "Hari")
    hdb.set_hold(old, True, "Hari")
    clock(monkeypatch, "2026-11-20 09:00")
    hari = kit.open_as(world, "hari")
    roster_before = [m["name"] for m in shift_db.roster(include_inactive=True)]
    for path, data in [
        (f"/shift/hiring/postings/{posting_id}/publish", {}),
        (f"/shift/hiring/postings/{other}/close", {"confirm": "1"}),
        (f"/shift/hiring/postings/{other}/ai", {"on": "1"}),
        ("/shift/hiring/retention/purge", {"confirm": "1"}),
        (f"/shift/hiring/candidates/{hired}/roster", {}),
        ("/shift/hiring/records", {}),
    ]:
        resp = hari.post(path, data=data)
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", path
        assert "That page isn't available on your sign-in." in flashed(hari), path
    assert hdb.get_posting(posting_id)["status"] == "draft" and hdb.get_posting(other)["status"] == "open"
    assert hdb.get_posting(other)["ai_assist"] == 0
    assert [m["name"] for m in shift_db.roster(include_inactive=True)] == roster_before
    # a hiring leader can put a hold on, and cannot take one off
    post(hari, f"/shift/hiring/candidates/{old}/hold", on="0")
    assert "Only the Operator can take a hold off." in flashed(hari) and hdb.candidate(old)["hold"] == 1
    assert hdb.candidate(old)["purged"] is False
    # what a hiring leader is shown says so, with no button for it
    detail = page(hari, f"/shift/hiring/postings/{posting_id}")
    assert "Only the Operator can publish a posting." in detail and "/publish" not in detail
    assert "Only the Operator can close a posting." in page(hari, f"/shift/hiring/postings/{other}")
    assert "Download hiring records" not in page(hari, "/shift/hiring/postings")
    assert "Retention" not in page(hari, "/shift/hiring/")
    # the Operator can do each of them
    operator = kit.open_as(world, "operator")
    post(operator, f"/shift/hiring/postings/{posting_id}/publish")
    assert hdb.get_posting(posting_id)["status"] == "open"
    post(operator, f"/shift/hiring/postings/{other}/ai", on="1")
    assert hdb.get_posting(other)["ai_assist"] == 1
    post(operator, f"/shift/hiring/candidates/{old}/hold", on="0")
    post(operator, "/shift/hiring/retention/purge", confirm="1")
    assert hdb.candidate(old)["purged"] is True
    post(operator, f"/shift/hiring/postings/{other}/close", confirm="1")
    assert hdb.get_posting(other)["status"] == "closed"
    records = operator.post("/shift/hiring/records")
    assert records.status_code == 200 and "attachment" in records.headers["Content-Disposition"]
    assert json.loads(records.data)["kind"] == "hiring-records"
    actions = [r["action"] for r in people_db.audit_feed()]
    for action in ("posting published", "posting AI switched on", "hold off", "applications purged",
                   "posting closed", "hiring records downloaded"):
        assert action in actions, action


# --- postings as the leaders see them -----------------------------------------------------

def test_writing_a_posting_and_working_through_the_publish_check(world):
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    people_db.set_setting("hiring_contact_name", "", "Operator")
    assert "Is this for an existing vacancy?" in page(hari, "/shift/hiring/postings/new")
    bad = hari.post("/shift/hiring/postings/new", data={"title": "", "description": "Hello"})
    assert bad.status_code == 400 and "Give the posting a title." in bad.data.decode() and b"Hello" in bad.data
    resp = post(hari, "/shift/hiring/postings/new", title="Team member", employment_type="either",
                description="Too short.", pay_min="15", pay_max="19.50")
    posting_id = hdb.postings()[0]["id"]
    assert resp.headers["Location"] == f"/shift/hiring/postings/{posting_id}#criteria"
    detail = page(operator, f"/shift/hiring/postings/{posting_id}")
    for problem in ("at least 200 characters", "below Ontario&#39;s general minimum wage ($17.95",
                    "existing vacancy or for future openings", "Set the hiring contact&#39;s name",
                    "Add at least 2 criteria"):
        assert problem in detail, problem
    assert "This posting can&#39;t go live yet." in detail and "Publish this posting</button>" in detail
    assert re.search(r'<button class="btn btn-primary" type="submit" disabled>Publish this posting', detail)
    post(operator, f"/shift/hiring/postings/{posting_id}/publish")              # refused all the same
    assert hdb.get_posting(posting_id)["status"] == "draft"
    assert any("200 characters" in m for m in flashed(operator))
    # fix it: wording, pay, vacancy, the contact, and criteria from Our standards and by hand
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    post(hari, f"/shift/hiring/postings/{posting_id}/edit", title="Team member", employment_type="either",
         description=DESCRIPTION + " We want strong team players.", pay_min="17.95", pay_max="19.50",
         vacancy="future")
    assert "Add from Our standards" in detail and 'value="reliable"' in detail
    post(hari, f"/shift/hiring/postings/{posting_id}/criteria", dimension_key="reliable")
    post(hari, f"/shift/hiring/postings/{posting_id}/criteria", dimension_key="reliable")
    assert "That standard is already one of this posting's criteria." in flashed(hari)
    post(hari, f"/shift/hiring/postings/{posting_id}/criteria", name="Clean as you go",
         definition="Keeps the station clean through the rush.", question="How do you keep a work area tidy?",
         anchor1="No example.", anchor2="A general answer.", anchor3="A clear example.",
         anchor4="A clear example with a habit behind it.")
    saved = hdb.get_posting(posting_id)["criteria"]
    assert [(c["name"], c["dimension_key"]) for c in saved] == [("Reliable", "reliable"), ("Clean as you go", None)]
    assert saved[0]["question"].startswith("Tell us about a time other people were counting on you")
    assert saved[1]["anchors"][3] == "A clear example with a habit behind it."
    post(hari, f"/shift/hiring/postings/{posting_id}/criteria/{saved[1]['id']}", name="Clean as you go",
         definition="Keeps the station clean and stocked through the rush.",
         question="How do you keep a work area tidy?", interview_question="Walk me through closing a station.")
    assert hdb.get_posting(posting_id)["criteria"][1]["interview_question"] == "Walk me through closing a station."
    # flagged words need the tick, from the Operator
    detail = page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "Words to read again." in detail and "“strong”" in detail
    assert "I have read these and the wording is about the job." in detail and "disabled" not in detail
    post(operator, f"/shift/hiring/postings/{posting_id}/publish")
    assert hdb.get_posting(posting_id)["status"] == "draft"
    post(operator, f"/shift/hiring/postings/{posting_id}/publish", wording_reviewed="1")
    posting = hdb.get_posting(posting_id)
    assert posting["status"] == "open" and posting["published_by"] == "Operator"
    detail = page(hari, f"/shift/hiring/postings/{posting_id}")
    assert "/careers/team-member" in detail and "Applicants apply here:" in detail
    assert "This posting is not for an existing vacancy." in detail      # the preview is what applicants see
    assert "Kept copies" in detail and "Published" in detail
    copy = page(hari, f"/shift/hiring/postings/{posting_id}/copies/{hdb.snapshots(posting_id)[0]['id']}")
    assert "APPLICATION FORM (blank)" in copy and "A kept copy is never changed or deleted." in copy
    # a change to the live posting that would break the check is refused, in words
    broken = hari.post(f"/shift/hiring/postings/{posting_id}/edit",
                       data={"title": "Team member", "description": "Short.", "pay_min": "17.95",
                             "pay_max": "19.50", "vacancy": "future"})
    assert broken.status_code == 400 and "This posting is live" in broken.data.decode()
    post(hari, f"/shift/hiring/postings/{posting_id}/criteria/{saved[1]['id']}/remove")
    assert any("This posting is live" in m for m in flashed(hari))
    assert len(hdb.get_posting(posting_id)["criteria"]) == 2
    post(hari, f"/shift/hiring/postings/{posting_id}/copy")
    assert [p["status"] for p in hdb.postings()] == ["open", "draft"]
    listing = page(hari, "/shift/hiring/postings")
    assert "Live" in listing and "Draft" in listing and "Part-time or full-time" in listing


def test_ai_on_a_posting_needs_the_setting_and_is_said_on_the_posting(world, monkeypatch):
    posting_id = live()
    operator = kit.open_as(world, "operator")
    detail = page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "Off for this posting" in detail and "switch on AI assist for hiring in" in detail
    post(operator, f"/shift/hiring/postings/{posting_id}/ai", on="1")
    assert "Switch on AI assist for hiring in People settings first." in flashed(operator)
    assert hdb.get_posting(posting_id)["ai_assist"] == 0
    people_db.set_setting("ai_hiring", "1", "Operator")
    post(operator, f"/shift/hiring/postings/{posting_id}/ai", on="1")
    assert hdb.get_posting(posting_id)["ai_assist"] == 1
    assert any("once an AI key is set up on the server" in m for m in flashed(operator))
    # no key on the server yet: it is switched on but not in use, and the posting says nothing about AI
    detail = page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "Switched on, but not in use" in detail and "no AI key is set up on the server" in detail
    assert "We use artificial intelligence" not in detail
    assert [(c["reason"], c["ai_statement"]) for c in hdb.snapshots(posting_id)] == [("published", 0), ("changed", 0)]
    assert "No AI" not in shown(detail) and "no AI is used" not in shown(detail)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    detail = page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "On for this posting" in detail and "We use artificial intelligence (AI) to help assess" in detail
    assert "artificial intelligence" in cfa_app.app.test_client().get("/careers/team-member").data.decode()
    assert hdb.snapshots(posting_id)[-1]["ai_statement"] == 1          # what the public saw is on record
    post(operator, f"/shift/hiring/postings/{posting_id}/ai", on="0")
    post(operator, f"/shift/hiring/postings/{posting_id}/ai", on="1")
    assert "AI assist is on for this posting, and the posting now says so." in flashed(operator)


# --- the pipeline -------------------------------------------------------------------------

def test_the_pipeline_lists_people_by_stage_with_flags_and_banner_counts(world, monkeypatch):
    clock(monkeypatch, "2026-09-01 10:00")
    posting_id = live()
    waiting = candidate(posting_id, 1)
    flagged = applied(posting_id, 2, work_entitled="no")
    talked = candidate(posting_id, 3)
    hdb.move_stage(talked, "new", "interview", "Hari")
    hdb.add_interview(talked, "2026-09-01", "", "phone", "Hari", "Hari")
    hdb.set_interview_outcome(hdb.interviews(talked)[0]["id"], talked, "attended", "Hari")
    gone = candidate(posting_id, 4)
    hdb.move_stage(gone, "new", "not-selected", "Hari", reason="Position filled")
    hdb.set_hold(waiting, True, "Hari")
    clock(monkeypatch, "2026-10-20 10:00")
    hari = kit.open_as(world, "hari")
    html = page(hari, "/shift/hiring/")
    assert "<b>2</b> new · <b>0</b> notices due this week · <b>1</b> overdue" in html
    assert "1 decision notice is overdue." in html and 'href="/shift/hiring/notices"' in html
    assert "Nothing here moves, ranks or turns down an applicant." in html
    for label in ("New 2", "Screening 0", "Interview 1", "Offer 0", "Hired 0", "Not selected 1", "Withdrew 0"):
        assert label in html, label
    assert f'href="/shift/hiring/candidates/{waiting}"' in html and "49 days in this stage" in html
    assert f'href="/shift/hiring/candidates/{gone}"' not in html           # decided: off the active lists
    row = html[html.index(f'id="a{flagged}"'):html.index("</a>", html.index(f'id="a{flagged}"'))]
    assert "Check before any offer" in row and "Questions not answered" in row
    assert "On hold" in html[html.index(f'id="a{waiting}"'):html.index("</a>", html.index(f'id="a{waiting}"'))]
    # listed by how long they have waited, never by a score
    assert html.index(f'id="a{waiting}"') < html.index(f'id="a{flagged}"')
    for word in ("score", "rank", "average", "total", "best", "top"):
        assert word not in re.sub(r"<[^>]+>", " ", shown(html)).lower().replace("ranks or turns down", ""), word
    assert people_hiring.nav_counts("leader", world["ids"]["hari"], "Hari") == {
        "new": 2, "notices_due": 0, "notices_overdue": 1}
    assert people_hiring.nav_counts("operator", None, "Operator")["new"] == 2
    assert people_hiring.nav_counts("leader", world["ids"]["lena"], "Lena") == {}
    assert people_hiring.nav_counts("leader", world["ids"]["hari"] + 40, "Hari") == {}
    assert "1 notice overdue" in page(hari, "/shift/more") and "2 new" in page(hari, "/shift/people")
    assert "notice overdue" not in page(kit.open_as(world, "lena"), "/shift/more")


# --- the candidate page is a scoring screen ---------------------------------------------------

def test_scoring_pages_never_show_contact_details_or_the_two_legal_answers(world):
    posting_id = live()
    app_id = candidate(posting_id, work_entitled="no", adult="no")
    hari = kit.open_as(world, "hari")
    for stage in ("new", "screening", "interview", "offer"):
        if stage != "new":
            assert hdb.move_stage(app_id, hdb.candidate(app_id)["stage"], stage, "Hari") is None
        html = shown(page(hari, f"/shift/hiring/candidates/{app_id}"))
        for private in (EMAIL, PHONE, "5195550142", "mailto:", "sms:", "Alex Doe", "friend or family",
                        "entitled to work", "legally entitled", "18 or older?", "Heard about us",
                        "Check before any offer"):
            assert private not in html, (stage, private)
        assert "Jordan1 Sample" in html and "Contact, messages and offer checks" in html
    # the contact page is where those live, and it has no level pickers
    contact = shown(page(hari, f"/shift/hiring/candidates/{app_id}/contact"))
    for there in (EMAIL, PHONE, "Alex Doe", "A friend or family member who works here",
                  "Check before any offer: said they are not legally entitled to work in Canada",
                  "Nothing on this page is part of a score."):
        assert there in contact, there
    assert 'name="level_' not in contact and "Save my levels" not in contact
    opened = [r["action"] for r in people_db.audit_feed() if r["object_id"] == app_id]
    assert opened.count("candidate opened") == 4 and opened.count("contact details opened") == 1


def test_the_candidate_page_is_laid_out_in_the_agreed_order(world):
    posting_id = live()
    app_id = candidate(posting_id)
    html = page(kit.open_as(world, "hari"), f"/shift/hiring/candidates/{app_id}")
    order = ['<h1 class="headline">Jordan1 Sample</h1>', 'id="stage"', "What to write, and what never to write",
             'id="answers"', "I opened the farm stand every Saturday", 'id="availability"', 'id="experience"',
             "Volunteered at a food bank.", 'id="interviews"', 'id="summary"', 'id="notes"', 'id="history"']
    at = -1
    for part in order:
        found = html.find(part, at + 1)
        assert found > at, part
        at = found
    assert "Hiring · Team member" in html and "<b>New</b> · 0 days in this stage" in html
    assert "A fact for scheduling. It is not scored" in html
    assert "There is no total and no average on purpose" in html
    # the quick-question answer sits with its criterion's definition and level descriptions
    block = html[html.index('id="answers"'):html.index('id="availability"')]
    for part in ("What reliable looks like on a shift.", "Level one.", "Level four.", 'name="level_',
                 'name="note_', "Save my levels"):
        assert part in block, part


def test_moving_stages_from_the_page(world):
    posting_id = live()
    app_id = candidate(posting_id)
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    for label in ("Move to screening", "Move to interview", "Move to offer", "Mark as not selected",
                  "The applicant withdrew"):
        assert label in html, label
    assert "Move to hired" not in html
    block = html[html.index('id="not-selected"'):html.index('id="guide"')]
    assert "This tool does not decide who is hired or turned down." in block and "a lawyer is called first" in block
    for reason in hdb.NOT_SELECTED_REASONS:
        assert reason.replace("'", "&#39;") in block, reason
    post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "new", "to": "not-selected"})
    assert "Choose the job-related reason from the list." in flashed(hari)
    post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "new", "to": "screening"})
    assert "Moved to screening." in flashed(hari)
    post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "new", "to": "interview"})   # a stale page
    assert "Someone else changed this application. Check where it is now." in flashed(hari)
    resp = post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "screening", "to": "offer"})
    assert resp.headers["Location"].endswith(f"/candidates/{app_id}/contact#offer-checks")
    post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "offer", "to": "hired"})
    assert any("offer checks are done" in m for m in flashed(hari))
    assert "Move to hired" in page(hari, f"/shift/hiring/candidates/{app_id}")
    post(hari, f"/shift/hiring/candidates/{app_id}/stage",
         **{"from": "offer", "to": "not-selected", "reason": "Position filled"})
    assert hdb.candidate(app_id)["stage"] == "not-selected"
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Only the Operator can reopen this application." in html and "Reopen to screening" not in html
    post(hari, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "not-selected", "to": "screening"})
    assert hdb.candidate(app_id)["stage"] == "not-selected"
    assert "Reopen to screening" in page(operator, f"/shift/hiring/candidates/{app_id}")
    post(operator, f"/shift/hiring/candidates/{app_id}/stage", **{"from": "not-selected", "to": "screening"})
    assert hdb.candidate(app_id)["stage"] == "screening"
    history = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Offer to Not selected (Position filled)" in history and "The applicant" in history
    changes = [r["detail"] for r in people_db.audit_feed() if r["action"] == "stage changed"]
    assert changes == ["screening", "not-selected", "offer", "screening"]


def test_each_leader_scores_alone_and_the_summary_adds_nothing_up(world):
    posting_id = live()
    app_id = candidate(posting_id)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    post(operator, f"/shift/hiring/candidates/{app_id}/score",
         **{f"level_{first}": "2", f"note_{first}": "General.", f"level_{second}": "4"})
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    summary = html[html.index('id="summary"'):html.index('id="notes"')]
    assert "No levels to show yet." in summary and "Operator" not in summary     # Hari has not scored yet
    post(hari, f"/shift/hiring/candidates/{app_id}/score", **{f"level_{first}": "3", f"note_{first}": "A real example."})
    assert "Saved your level for 1 answer." in flashed(hari)
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert f'name="level_{first}" value="3" checked' in html and "A real example.</textarea>" in html
    summary = html[html.index('id="summary"'):html.index('id="notes"')]
    cells = re.findall(r"<td>([^<]*)</td>", summary)
    assert cells == ["Reliable", "3", "2", "Guest care", "–", "–"]       # Hari, then the Operator; second not shared yet
    assert "Operator<small>Written answers</small>" in summary and "Hari<small>Written answers</small>" in summary
    for word in ("total", "average", "overall", "added up", "rank"):
        assert word not in re.sub(r"<[^>]+>", " ", summary).lower().replace("no total and no average", ""), word
    post(hari, f"/shift/hiring/candidates/{app_id}/score", **{f"level_{first}": "", f"level_{second}": ""})
    assert "No levels saved. Choose a level for an answer first." in flashed(hari)
    # a note with words from the "never write" list gets a nudge, and is still saved
    post(hari, f"/shift/hiring/candidates/{app_id}/note", body="Seems very mature for a teenager.")
    said = flashed(hari)
    assert "Note saved." in said and any("words about age" in m for m in said)
    assert hdb.notes(app_id)[0]["body"] == "Seems very mature for a teenager."
    assert "Seems very mature" in page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "mature" not in json.dumps(people_db.audit_feed())           # the log holds no free text


def test_interviews_and_scorecards_from_the_page(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    posting_id = live()
    app_id = candidate(posting_id)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    assert "No interviews logged." in page(hari, f"/shift/hiring/candidates/{app_id}")
    post(hari, f"/shift/hiring/candidates/{app_id}/interview", on_date="2026-11-10", at_time="15:30",
         mode="video", interviewer="Hari and the Operator")
    interview_id = hdb.interviews(app_id)[0]["id"]
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Nov 10, 15:30 · video" in html and "Scheduled" in html and "My scorecard" not in html
    post(hari, f"/shift/hiring/candidates/{app_id}/interviews/{interview_id}/outcome", outcome="attended")
    assert "Recorded. The applicant must be told where things stand within 45 days." in flashed(hari)
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Tell them where things stand by Dec 25" in html and "45 days left" in html
    kit_block = html[html.index(f'id="kit-{interview_id}"'):html.index('id="add-interview"')]
    # every criterion, in the posting's order, the same for every candidate
    assert kit_block.index("1. Reliable") < kit_block.index("2. Guest care")
    assert "Ask them to tell you more about: Tell us about a time you showed reliable." in kit_block
    assert "Submit scorecard" in kit_block and "Save draft" in kit_block
    scorecard = f"/shift/hiring/candidates/{app_id}/interviews/{interview_id}/scorecard"
    post(operator, scorecard, action="submit", **{f"level_{first}": "4", f"note_{first}": "Named the dates.",
                                                  f"level_{second}": "1"})
    post(hari, scorecard, action="save", **{f"level_{first}": "3", f"note_{first}": "Clear example."})
    assert "Draft saved. Submit it when you are done." in flashed(hari)
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    summary = html[html.index('id="summary"'):html.index('id="notes"')]
    assert "1 scorecard from another interviewer will show here once you submit your own." in summary
    assert "Named the dates." not in html and "Operator<small>Interview</small>" not in summary
    assert "Clear example.</textarea>" in html                           # the draft is still there to finish
    post(hari, scorecard, action="submit", **{f"level_{first}": "3", f"level_{second}": "2"})
    assert "Scorecard submitted. It can't be changed now." in flashed(hari)
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    summary = html[html.index('id="summary"'):html.index('id="notes"')]
    assert "will show here once you submit" not in summary
    assert re.findall(r"<td>([^<]*)</td>", summary) == ["Reliable", "3", "4", "Guest care", "2", "1"]
    assert "The notes behind these levels" in summary and "Named the dates." in summary     # now it is shared
    assert "You submitted your scorecard." in html and "Submit scorecard" not in html
    post(hari, scorecard, action="submit", **{f"level_{first}": "1"})
    assert "You have already submitted your scorecard for that interview." in flashed(hari)
    assert "Operator<small>Interview</small>" in page(operator, f"/shift/hiring/candidates/{app_id}")


# --- messages, notices and the offer checks ---------------------------------------------------

def test_messages_use_the_brief_wording_and_open_the_leaders_own_app(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    people_db.set_setting("hiring_contact_phone", "519-555-0100", "Operator")
    posting_id = live()
    app_id = candidate(posting_id)
    hari = kit.open_as(world, "hari")
    html = page(hari, f"/shift/hiring/candidates/{app_id}/contact")
    text = html.replace("&#39;", "'")
    assert ("Hi Jordan1, thank you for applying for Team member at Chick-fil-A Wharncliffe &amp; Wonderland. "
            "We would like to invite you to an interview. Please reply with a few times that work for you.") in text
    assert ("Accommodations are available on request for the interview and for anything used in it. "
            "If you need one, please tell Morgan Example at hiring@example.com or 519-555-0100.") in text
    assert ("thank you for applying for Team member (posted November 10, 2026). A hiring decision has been "
            "made for this posting, and we are pleased to offer you the position. Morgan Example will contact "
            "you with the details.") in text
    assert ("We have policies for accommodating employees with disabilities. Morgan Example will share them "
            "with you, and you can ask for an accommodation at any time.") in text
    # not interviewed yet: nobody is told a decision "has been made" that this page cannot know
    assert ("thank you for applying for Team member (posted November 10, 2026). We will not be moving forward "
            "with your application. We appreciate the time you gave us and wish you well.") in text
    assert "A hiring decision has not yet been made for this posting. We will contact you when it has." in text
    assert "Nothing is sent from Team Hub" in text
    links = re.findall(r'href="((?:mailto|sms):[^"]+)"', html)
    assert len(links) == 8 and all(link.startswith(("mailto:jordan1@example.com?subject=", "sms:519555014"))
                                   for link in links)
    assert "sms:5195550142?&amp;body=Hi%20Jordan1" in html
    # after an interview the wording says "interviewing", and names the planned one in the invitation
    hdb.add_interview(app_id, "2026-11-12", "15:30", "phone", "Hari", "Hari")
    text = page(hari, f"/shift/hiring/candidates/{app_id}/contact").replace("&#39;", "'")
    assert "invite you to an interview on November 12, 2026 at 15:30, by phone." in text
    clock(monkeypatch, "2026-11-12 16:00")
    hdb.set_interview_outcome(hdb.interviews(app_id)[0]["id"], app_id, "attended", "Hari")
    kit.unlock(hari, "hari")                                             # two days on: the pages had locked
    text = page(hari, f"/shift/hiring/candidates/{app_id}/contact").replace("&#39;", "'")
    assert "Hi Jordan1, thank you for interviewing for Team member (posted November 10, 2026)." in text
    assert ("A hiring decision has been made for this posting, and we will not be moving forward with your "
            "application. We appreciate the time you gave us and wish you well.") in text
    assert "A notice is due by Dec 27" in text
    for promo in ("coupon", "free", "discount", "deal"):
        assert promo not in " ".join(m["body"] for m in people_hiring.messages(
            hdb.candidate(app_id), hdb.candidate_contact(app_id), hdb.settings(), hdb.interviews(app_id))).lower()


def test_no_name_or_address_can_break_a_message_link(world):
    record = {"first_name": "Sam\r\nBcc: evil@example.com", "posting_title": "Team\nmember?&cc=x@evil.example",
              "posting_published": "2026-11-10 09:00"}
    contact = {"email": "sam@example.com?bcc=evil@example.com\r\nX: y", "phone": "519 555-0142\r\n&body=hi"}
    conf = dict(hdb.settings(), employer_name="Shop\r\nCc: evil@example.com", hiring_contact_name="Mo\nrgan")
    built = people_hiring.messages(record, contact, conf, [])
    assert [m["key"] for m in built] == ["invite", "offer", "no", "waiting"]
    for m in built:
        for link in (m["mailto"], m["sms"]):
            assert "\r" not in link and "\n" not in link and " " not in link
            assert "%0D" not in link.upper()                              # no carriage return survives at all
        address, query = m["mailto"][len("mailto:"):].split("?", 1)
        assert address == "sam@example.com%3Fbcc%3Devil@example.com%20X%3A%20y"   # one address, nothing added
        assert sorted(part.split("=")[0] for part in query.split("&")) == ["body", "subject"]
        subject = unquote(query.split("&")[0].split("=", 1)[1])
        assert "\n" not in subject and "\r" not in subject
        body = unquote(query.split("&")[1].split("=", 1)[1])
        assert "Hi Sam Bcc: evil@example.com," in body                    # the name is one line of plain text
        assert "\nBcc:" not in body and "\nCc:" not in body and "\r" not in body
        assert m["sms"].startswith("sms:5195550142?&body=") and m["sms"].count("&body=") == 1
    links = people_hiring.message_links("", "", "Subject", "Body")
    assert links == {"mailto": "", "sms": ""}


def test_logging_a_notice_and_the_notices_page(world, monkeypatch):
    clock(monkeypatch, "2026-09-01 10:00")
    posting_id = live()
    early, later = candidate(posting_id, 1), candidate(posting_id, 2)
    for app_id in (early, later):
        hdb.add_interview(app_id, "2026-09-01", "", "in person", "Hari", "Hari")
    hdb.set_interview_outcome(hdb.interviews(early)[0]["id"], early, "attended", "Hari")
    clock(monkeypatch, "2026-10-10 10:00")
    hdb.set_interview_outcome(hdb.interviews(later)[0]["id"], later, "cancelled", "Hari")
    hdb.add_interview(later, "2026-10-10", "", "phone", "Hari", "Hari")
    hdb.set_interview_outcome(hdb.interviews(later)[-1]["id"], later, "attended", "Hari")
    clock(monkeypatch, "2026-10-20 10:00")
    hari = kit.open_as(world, "hari")
    html = page(hari, "/shift/hiring/notices")
    assert "<b>2</b> owed · <b>1</b> overdue" in html and "within 45 days of their last interview" in html
    assert html.index(f'id="o{early}"') < html.index(f'id="o{later}"')
    first_row = html[html.index(f'id="o{early}"'):html.index(f'id="o{later}"')]
    assert 'class="hr-late"' in first_row and "Overdue by 4 days" in first_row and "Oct 16" in first_row
    assert "35 days left" in html and "No notices logged yet." in html
    contact = f"/shift/hiring/candidates/{early}/contact"
    assert "A notice is overdue" in page(hari, contact)
    post(hari, f"/shift/hiring/candidates/{early}/notice", kind="no-decision-yet", method="phone",
         given_on="2026-10-21", said="No decision has been made yet.")
    assert "The date they were told can't be in the future." in flashed(hari)
    post(hari, f"/shift/hiring/candidates/{early}/notice", kind="no-decision-yet", method="phone",
         given_on="2026-10-20", said="Hi Jordan1, no decision has been made yet.")
    assert "Notice logged. It is kept for three years." in flashed(hari)
    html = page(hari, "/shift/hiring/notices")
    assert f'id="o{early}"' not in html and "<b>1</b> owed · <b>0</b> overdue" in html
    log = html[html.index('id="log"'):]
    for part in ("Oct 20", "Jordan1 Sample", "Team member", "No decision yet", "Phone", "Hari"):
        assert part in log, part
    contact_page = page(hari, contact)
    assert "Hi Jordan1, no decision has been made yet." in contact_page and "after the Oct 16 deadline" in contact_page
    assert [r["detail"] for r in people_db.audit_feed() if r["action"] == "notice logged"] == ["no-decision-yet"]
    assert "no decision has been made" not in json.dumps(people_db.audit_feed())


def test_offer_checks_open_only_at_the_offer_stage(world):
    posting_id = live()
    app_id = candidate(posting_id, adult="no")
    hari = kit.open_as(world, "hari")
    contact = f"/shift/hiring/candidates/{app_id}/contact"
    html = page(hari, contact)
    block = html[html.index('id="offer-checks"'):html.index('id="records"')]
    assert "<details class=\"card tool\">" in block and "18 or older?" not in block
    assert "open here when the application reaches the offer stage" in block
    assert 'id="offer"' not in html
    post(hari, f"/shift/hiring/candidates/{app_id}/offer")
    assert "Move the application to the offer stage first." in flashed(hari)
    hdb.move_stage(app_id, "new", "offer", "Hari")
    html = page(hari, contact).replace("&#39;", "'")
    block = html[html.index('id="offer-checks"'):html.index('id="records"')]
    assert "<details class=\"card tool\" open>" in block
    assert re.search(r"Legally entitled to work in Canada\?</td><td class=\"who\">Yes<", block)
    assert re.search(r"18 or older\?</td><td class=\"who\">No<", block)
    for reminder in ("Ask for proof of age only after a conditional offer.",
                     "Minimum age: 14 for serving areas, 15 for the kitchen.", "A trial shift is paid work.",
                     "Give the new hire their written job information before their first day",
                     "tell them about our policies for accommodating employees with disabilities"):
        assert reminder in block, reminder
    post(hari, f"/shift/hiring/candidates/{app_id}/offer")
    post(hari, f"/shift/hiring/candidates/{app_id}/offer-reply", reply="accepted")
    assert any("Move them to hired yourself" in m for m in flashed(hari))
    assert hdb.candidate(app_id)["stage"] == "offer"                     # recording an answer moves nobody
    assert "The last offer was accepted." in page(hari, contact)
    assert [e["kind"] for e in table("hr_events")][-2:] == ["offer", "offer_reply"]


def test_only_the_name_reaches_the_team_hub_roster(world):
    posting_id = live()
    app_id = candidate(posting_id)
    operator = kit.open_as(world, "operator")
    post(operator, f"/shift/hiring/candidates/{app_id}/roster")
    assert "Only someone who has been hired can be added to the roster." in flashed(operator)
    hdb.move_stage(app_id, "new", "offer", "Operator")
    hdb.move_stage(app_id, "offer", "hired", "Operator", checks_done=True)
    assert "Add to the Team Hub roster" in page(operator, f"/shift/hiring/candidates/{app_id}/contact")
    assert "The Operator adds a new hire" in page(kit.open_as(world, "hari"),
                                                  f"/shift/hiring/candidates/{app_id}/contact")
    post(operator, f"/shift/hiring/candidates/{app_id}/roster")
    assert "Added to the Team Hub roster. Only their name was copied." in flashed(operator)
    member = next(m for m in shift_db.roster() if m["name"] == "Jordan1 Sample")
    assert set(member.values()) & {EMAIL, PHONE, "Alex Doe"} == set()
    backup = shift_db.export_json()
    assert "Jordan1 Sample" in backup
    for private in (EMAIL, PHONE, "5195550142", "farm stand", "Alex Doe", "food bank", "hr_applications"):
        assert private not in backup, private
    post(operator, f"/shift/hiring/candidates/{app_id}/roster")
    assert "That name is already on the Team Hub roster." in flashed(operator)
    assert sum(1 for m in shift_db.roster(include_inactive=True) if m["name"] == "Jordan1 Sample") == 1
    assert "is on the Team Hub roster" in page(operator, f"/shift/hiring/candidates/{app_id}/contact")
    assert table("hr_events")[-1]["kind"] == "roster"


# --- AI assist ---------------------------------------------------------------------------------

def ai_world(world, monkeypatch, posting_id=None, **changes):
    """A live posting with AI assist on and in use, and an applicant who
    was shown the AI statement."""
    ai_in_use(monkeypatch)
    posting_id = posting_id or live()
    hdb.set_ai(posting_id, True, "Operator")
    result = hdb.apply(posting_id, {
        "first_name": "Jordan", "last_name": "Sample", "email": "jordan@example.com", "phone": PHONE,
        "work_entitled": "yes", "adult": "no", "heard_from": "friend", "referrer": "Sam Patel",
        "av_mon_morning": "1", "experience": "Secret experience at a bakery.", "consent": "1", **changes},
        ai_shown=True)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hdb.save_answers(result["id"], {
        first: "Jordan here. I opened the farm stand every Saturday, and Sam Patel counted on me. "
               "Call me at 519-555-0142 or jordan@example.com. She said he was reliable.",
        second: "A guest was lost, so I walked her to her table."})
    return posting_id, result["id"], first, second


def good_reply(first, second):
    return {"criteria": [
        {"criterion_id": first, "evidence_summary": "Describes opening a stand every week.",
         "quote": "I opened the farm stand every Saturday", "suggested_level": 3,
         "note": "Matches “Level three.”"},
        {"criterion_id": second, "evidence_summary": "Describes helping a guest.",
         "quote": "walked a guest all the way home", "suggested_level": 4, "note": "Matches “Level four.”"},
        {"criterion_id": 999, "evidence_summary": "Made up.", "quote": "x", "suggested_level": 1, "note": ""},
    ], "human_checks": ["Human check needed: the second answer is short."]}


def test_ai_assist_runs_only_where_it_was_disclosed(world, monkeypatch):
    posting_id = live()
    quiet = candidate(posting_id)                                        # applied before AI was mentioned
    calls = kit.fake_ai(monkeypatch, lambda system, user: good_reply(1, 2))
    hari = kit.open_as(world, "hari")
    post(hari, f"/shift/hiring/candidates/{quiet}/ai")
    assert "AI assist for hiring is switched off in People settings." in flashed(hari)
    people_db.set_setting("ai_hiring", "1", "Operator")
    post(hari, f"/shift/hiring/candidates/{quiet}/ai")
    assert "AI assist is not switched on for this posting." in flashed(hari)
    hdb.set_ai(posting_id, True, "Operator")
    post(hari, f"/shift/hiring/candidates/{quiet}/ai")
    assert any("applied while the posting did not say AI is used" in m for m in flashed(hari))
    assert "applied while the posting did not say AI is used" in page(hari, f"/shift/hiring/candidates/{quiet}")
    stale_form = hdb.apply(posting_id, {"first_name": "Robin", "last_name": "Sample", "email": "robin@example.com",
                                        "work_entitled": "yes", "adult": "yes", "consent": "1"}, ai_shown=False)["id"]
    hdb.save_answers(stale_form, {hdb.get_posting(posting_id)["criteria"][0]["id"]: "An answer."})
    post(hari, f"/shift/hiring/candidates/{stale_form}/ai")
    assert calls == [] and table("ai_jobs") == []                        # nothing was ever sent
    # with no key on the server it is off, whatever the switches say, even for someone who was told
    _, shown_to, _, _ = ai_world(world, monkeypatch, posting_id, phone="519-555-0177")
    assert table("hr_applications")[-1]["ai_disclosed"] == 1
    monkeypatch.delenv("OPENAI_API_KEY")
    post(hari, f"/shift/hiring/candidates/{shown_to}/ai")
    assert "AI assist for hiring is switched off in People settings." in flashed(hari) and calls == []


def test_what_the_ai_is_sent_has_no_name_contact_details_or_anything_else(world, monkeypatch):
    posting_id, app_id, first, second = ai_world(world, monkeypatch)
    calls = kit.fake_ai(monkeypatch, lambda system, user: good_reply(first, second))
    hari = kit.open_as(world, "hari")
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    assert len(calls) == 1
    system, user = calls[0]
    assert system.startswith(people_ai.GUARDRAILS) and "Task: hiring_score." in system
    assert "Never say whether the applicant should be interviewed, hired, moved on or turned down." in system
    document = json.loads(user)
    assert document["kind"] == "hiring_score" and set(document["data"]) == {"criteria", "answers"}
    assert [c["name"] for c in document["data"]["criteria"]] == ["Reliable", "Guest care"]
    assert set(document["data"]["criteria"][0]) == {"criterion_id", "name", "definition", "anchors"}
    sent = {a["criterion_id"]: a["text"] for a in document["data"]["answers"]}
    assert sent[first] == ("The applicant here. I opened the farm stand every Saturday, and a team member "
                           "counted on me. Call me at [removed] or [removed]. They said they was reliable.")
    for private in ("Jordan", "Sample", "jordan@example.com", "519-555-0142", "Sam Patel", "Patel", "bakery",
                    "Secret", "friend", "mon", "morning", "adult", "work_entitled", "availability",
                    "experience", "heard"):
        assert private not in user, private
    job = table("ai_jobs")[0]
    assert job["input_json"] == user and job["status"] == "done" and job["requested_by"] == "Hari"
    assert job["target_id"] == app_id and job["prompt_version"] == people_ai.PROMPT_VERSION
    assert table("hr_events")[-1]["kind"] == "ai" and hdb.candidate(app_id)["stage"] == "new"
    assert "AI suggestion asked" in [r["action"] for r in people_db.audit_feed()]


def test_the_ai_reply_is_checked_before_anyone_sees_it(world, monkeypatch):
    posting_id, app_id, first, second = ai_world(world, monkeypatch)
    kit.fake_ai(monkeypatch, lambda system, user: good_reply(first, second))
    hari = kit.open_as(world, "hari")
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    output = people_ai.latest_job("hiring_score", app_id)["output"]
    by_id = {c["criterion_id"]: c for c in output["criteria"]}
    assert set(by_id) == {first, second}                                 # the made-up criterion is dropped
    assert by_id[first]["suggested_level"] == 3 and by_id[first]["quote"] == "I opened the farm stand every Saturday"
    # a quote that is not in the answer: no level, and a check for the person
    assert by_id[second]["suggested_level"] is None and by_id[second]["quote"] == ""
    assert any("did not point to a real phrase from this answer" in c for c in by_id[second]["checks"])
    assert by_id[first]["checks"] == []
    # the AI's own check line is never passed on in its own words
    assert output["human_checks"] == [people_ai.OWN_WORDS_LEFT_OUT]
    # the validator alone, on the awkward cases
    payload = {"criteria": [{"criterion_id": 1, "name": "Reliable"}],
               "answers": [{"criterion_id": 1, "text": "I “showed up” early every day."}], "withheld": [2]}
    checked = people_hiring._ai_validate({"criteria": [
        {"criterion_id": 1, "evidence_summary": "x" * 2000, "quote": 'i "showed  up" EARLY', "suggested_level": 9,
         "note": "Strong answer. I recommend you hire this person."},
        {"criterion_id": 1, "evidence_summary": "A second go.", "quote": "early", "suggested_level": 1, "note": ""},
        {"criterion_id": "1", "quote": "early"}, {"criterion_id": True, "quote": "early"}, "junk",
    ], "human_checks": "not a list"}, payload)
    assert len(checked["criteria"]) == 1
    only = checked["criteria"][0]
    assert only["quote"] == 'i "showed up" EARLY' and only["suggested_level"] is None    # 9 is not a level
    assert len(only["evidence_summary"]) == 600 and only["note"] == ""                    # the decision is cut
    assert any("read like a hiring decision" in c for c in only["checks"])
    assert checked["human_checks"] == [people_hiring.WITHHELD_CHECK]
    with pytest.raises(ValueError):
        people_hiring._ai_validate({"criteria": "nope"}, payload)
    flagged = people_hiring._ai_validate({"criteria": [{
        "criterion_id": 1, "evidence_summary": "Mentions their church and their kids.", "quote": "early",
        "suggested_level": 2, "note": ""}]}, payload)
    assert flagged["criteria"][0]["flagged"] == ["Pregnancy and family", "Religion"]
    assert any("touches on pregnancy and family, religion" in c for c in flagged["criteria"][0]["checks"])
    assert flagged["criteria"][0]["suggested_level"] is None         # one word is not a quotation
    assert flagged["human_checks"] == [people_hiring.WITHHELD_CHECK]


def test_a_suggestion_shows_only_after_the_leader_has_saved_their_own_level(world, monkeypatch):
    posting_id, app_id, first, second = ai_world(world, monkeypatch)
    kit.fake_ai(monkeypatch, lambda system, user: good_reply(first, second))
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Ask for a second read" in html and "Suggested, for reviewer decision" not in html
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "Suggested, for reviewer decision. Not a decision." not in html and "The AI suggests" not in html
    assert "2 more will show once you save your own level for those answers." in html
    post(hari, f"/shift/hiring/candidates/{app_id}/score", **{f"level_{first}": "2"})
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert html.count("Suggested, for reviewer decision. Not a decision.") == 1
    block = html[html.index(f'id="ai-{first}"'):html.index(f'id="k{second}"')]
    assert "The AI suggests <strong>level 3</strong>." in block and "“I opened the farm stand every Saturday”" in block
    assert "Your own level stands unless you change it." in block
    assert f'id="ai-{second}"' not in html and "1 more will show once you save your own level for that answer." in html
    assert hdb.scores_for(app_id, "Hari", world["ids"]["hari"])["mine"][first]["level"] == 2     # unchanged
    assert hdb.candidate(app_id)["stage"] == "new"                       # it never moves anyone
    # the Operator has scored nothing yet, so sees no suggestion either
    assert "The AI suggests" not in page(operator, f"/shift/hiring/candidates/{app_id}")
    # a failed request says so and changes nothing
    kit.fake_ai(monkeypatch, "this is not json")
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "The last request did not finish" in html and "The AI suggests" not in html


def test_an_answer_that_may_mention_health_is_never_sent(world, monkeypatch):
    ai_in_use(monkeypatch)
    posting_id = live()
    hdb.set_ai(posting_id, True, "Operator")
    app_id = hdb.apply(posting_id, {"first_name": "Jordan", "last_name": "Sample", "email": "jordan@example.com",
                                    "work_entitled": "yes", "adult": "yes", "consent": "1"}, ai_shown=True)["id"]
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hdb.save_answers(app_id, {first: "Even with my anxiety I never missed a shift.",
                              second: "I helped a guest find her table."})
    calls = kit.fake_ai(monkeypatch, lambda system, user: {"criteria": [
        {"criterion_id": second, "evidence_summary": "Helped a guest.", "quote": "helped a guest",
         "suggested_level": 3, "note": ""}]})
    hari = kit.open_as(world, "hari")
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    assert "anxiety" not in calls[0][1] and str(first) not in [str(a["criterion_id"]) for a in
                                                               json.loads(calls[0][1])["data"]["answers"]]
    assert people_hiring.WITHHELD_CHECK in people_ai.latest_job("hiring_score", app_id)["output"]["human_checks"]
    # if every answer is like that, nothing is sent at all
    other = hdb.apply(posting_id, {"first_name": "Robin", "last_name": "Sample", "email": "robin@example.com",
                                   "work_entitled": "yes", "adult": "yes", "consent": "1"}, ai_shown=True)["id"]
    hdb.save_answers(other, {first: "I asked for an accommodation at my last job."})
    post(hari, f"/shift/hiring/candidates/{other}/ai")
    assert "None of the answers could be sent to the AI. Score them yourself." in flashed(hari)
    assert len(calls) == 1


# --- the numbers, and retention -----------------------------------------------------------------

def test_the_numbers_page_shows_counts_and_says_when_there_is_too_little(world, monkeypatch):
    clock(monkeypatch, "2026-10-01 08:00")
    posting_id = live()
    other = live(title="Kitchen team")
    apps = [candidate(posting_id, n) for n in range(1, 7)]
    candidate(other, 7)
    clock(monkeypatch, "2026-10-03 08:00")
    for app_id in apps[:5]:
        hdb.move_stage(app_id, "new", "screening", "Hari")
    clock(monkeypatch, "2026-10-20 08:00")
    hari = kit.open_as(world, "hari")
    html = page(hari, "/shift/hiring/metrics")
    assert "<b>7</b> applications in the last 90 days · <b>0</b> hired" in html
    assert "Below 5 cases a percentage or a middle value would mislead" in html
    assert "<b>71%</b> <span class=\"pp-bar-n\">(5 of 7)</span>" in html           # reached screening
    assert "0 of 0 · not enough data yet" in html and "0 so far · not enough data yet" in html
    assert "2.0 days" in html and "(from 5)" in html
    assert 'style="width: 71%"' in html and "<canvas" not in html and "<svg class=\"chart" not in html
    one = page(hari, f"/shift/hiring/metrics?posting={posting_id}&days=30")
    assert "<b>6</b> applications in the last 30 days" in one and "<b>83%</b>" in one
    assert f'<option value="{posting_id}" selected>' in one and '<option value="30" selected>' in one
    for junk in ("?posting=abc&days=7", "?posting=999999999999&days=", "?posting=-1"):
        assert "<b>7</b> applications in the last 90 days" in page(hari, "/shift/hiring/metrics" + junk)
    assert "A friend or family member who works here" in html and "Given on time" in html


def test_the_retention_page_and_the_purge(world, monkeypatch):
    clock(monkeypatch, "2024-01-10 09:00")
    posting_id = live()
    old = candidate(posting_id, 1)
    hdb.move_stage(old, "new", "not-selected", "Hari", reason="Position filled")
    clock(monkeypatch, "2026-11-20 09:00")
    operator = kit.open_as(world, "operator")
    html = page(operator, "/shift/hiring/retention")
    assert "<b>1</b> due to be removed · <b>0</b> on hold · <b>0</b> already removed" in html
    assert "Jordan1 Sample" in html and "Nothing is removed automatically" in html
    assert "Never removed:" in html and "Kept three years by law:" in html
    post(operator, "/shift/hiring/retention/purge")
    assert "Tick the box to remove these applicants' details. It can't be undone." in flashed(operator)
    assert hdb.candidate(old)["purged"] is False
    post(operator, "/shift/hiring/retention/purge", confirm="1")
    assert "Removed the personal details of 1 application." in flashed(operator)
    html = page(operator, "/shift/hiring/retention")
    assert "<b>0</b> due to be removed" in html and "<b>1</b> already removed" in html and "Jordan1" not in html
    row = [r for r in people_db.audit_feed() if r["action"] == "applications purged"][0]
    assert (row["who"], row["detail"]) == ("Operator", "1 applications")
    gone = shown(page(operator, f"/shift/hiring/candidates/{old}"))
    assert "This applicant's details were removed on Nov 20." in gone and "farm stand" not in gone
    assert "Jordan1" not in gone and 'name="level_' not in gone and "Add a note" not in gone
    contact = shown(page(operator, f"/shift/hiring/candidates/{old}/contact"))
    assert EMAIL not in contact and "mailto:" not in contact and "Hold: do not delete" not in contact
    post(operator, "/shift/hiring/retention/purge", confirm="1")
    assert "Nothing was due to be removed." in flashed(operator)


def test_nothing_about_an_applicant_is_printed_by_the_leader_pages(world, monkeypatch, capsys):
    posting_id, app_id, first, second = ai_world(world, monkeypatch)
    kit.fake_ai(monkeypatch, lambda system, user: good_reply(first, second))
    hari = kit.open_as(world, "hari")
    page(hari, f"/shift/hiring/candidates/{app_id}")
    page(hari, f"/shift/hiring/candidates/{app_id}/contact")
    post(hari, f"/shift/hiring/candidates/{app_id}/score", **{f"level_{first}": "2", f"note_{first}": "Private note."})
    post(hari, f"/shift/hiring/candidates/{app_id}/note", body="Another private note.")
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    kit.fake_ai(monkeypatch, lambda system, user: 1 / 0)
    post(hari, f"/shift/hiring/candidates/{app_id}/ai")
    out = capsys.readouterr()
    for private in ("Jordan", "jordan@example.com", PHONE, "farm stand", "Private note", "3333"):
        assert private not in out.out and private not in out.err, private


def test_correcting_details_and_the_banner_for_a_posting_that_needs_attention(world):
    posting_id = live()
    app_id = candidate(posting_id)
    hari = kit.open_as(world, "hari")
    contact = page(hari, f"/shift/hiring/candidates/{app_id}/contact")
    assert "Correct these at the applicant" in contact and 'name="first_name"' in contact
    post(hari, f"/shift/hiring/candidates/{app_id}/correct", first_name="Jordan", last_name="Sample",
         email="not-an-email", phone="")
    assert "That email doesn't look right. Check it and try again." in flashed(hari)
    post(hari, f"/shift/hiring/candidates/{app_id}/correct", first_name="Jordan", last_name="Sample",
         email="jordan.s@example.com", phone="")
    assert "Corrected." in flashed(hari)
    assert hdb.candidate_contact(app_id)["email"] == "jordan.s@example.com"
    assert "jordan.s@example.com" not in json.dumps(people_db.audit_feed())
    assert "details corrected" in [r["action"] for r in people_db.audit_feed()]
    # a wage floor that rises under a live posting is called out on the pipeline
    assert "no longer passes the publish check" not in page(hari, "/shift/hiring/")
    people_db.set_setting("min_wage_general", "18.40", "Operator")
    html = page(hari, "/shift/hiring/")
    assert "no longer passes the publish check" in html and "below Ontario&#39;s general minimum wage ($18.40" in html
    assert "This live posting needs attention." in page(hari, f"/shift/hiring/postings/{posting_id}")


def test_reaching_out_and_a_hold_are_recorded_from_the_contact_page(world):
    posting_id = live()
    app_id = candidate(posting_id)
    hari = kit.open_as(world, "hari")
    post(hari, f"/shift/hiring/candidates/{app_id}/contacted", method="smoke signal")
    assert "Choose how you reached out." in flashed(hari)
    post(hari, f"/shift/hiring/candidates/{app_id}/contacted", method="text")
    assert "Recorded that you reached out." in flashed(hari)
    resp = post(hari, f"/shift/hiring/candidates/{app_id}/hold", on="1")
    assert resp.headers["Location"].endswith(f"/candidates/{app_id}/contact#records")
    assert "On hold: it will not be deleted." in flashed(hari)
    contact = page(hari, f"/shift/hiring/candidates/{app_id}/contact")
    assert "On hold: do not delete" in contact and "Only the Operator can take a hold off." in contact
    assert "On hold: do not delete." in page(hari, f"/shift/hiring/candidates/{app_id}")
    assert [(e["kind"], e["detail"]) for e in table("hr_events")][-2:] == [("contacted", "text"), ("hold", "on")]
    assert "they were told when they applied" in page(kit.open_as(world, "operator"),
                                                     f"/shift/hiring/candidates/{applied(posting_id, 2)}/contact")
