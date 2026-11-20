"""Where People records can and cannot go, checked across both features.

The first-run walk in tests/people_walk.py puts a leader review and an
application through every step (with the AI switched on and a stand-in for
the AI service), keeping every response and every private thing that was
typed. These tests then look for those private things in the places they
must never reach: the shift database and its backups, the server log, and
the rest of Team Hub. Everyone is invented.
"""

import glob
import json
import os
import re
import sqlite3

import pytest

import app as cfa_app
import people
import people_ai
import people_db
import people_hiring_db as hdb
import shift_db
from tests import people_testkit as kit
from tests import people_walk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE_SOURCES = sorted(glob.glob(os.path.join(ROOT, "people*.py")) + [os.path.join(ROOT, "careers.py")])
CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; manifest-src 'self'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
STRICT = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
          "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY"}
ON_ATTRIBUTE = re.compile(r"<[^>]*\son[a-z]+\s*=", re.I)
PRIVATE_PAGES = ("/shift/people", "/shift/reviews", "/shift/hiring", "/careers")


@pytest.fixture()
def walked(monkeypatch, tmp_path, capsys):
    """The whole first-run path, AI on, with what it printed along the way."""
    world = kit.make_world(monkeypatch, tmp_path, bare=True)
    capsys.readouterr()                                   # the shift database's own start-up lines
    walk = people_walk.first_run(world, monkeypatch, ai=True)
    printed = capsys.readouterr()
    walk.printed = printed.out + printed.err
    return walk


def people_tables() -> set[str]:
    with people_db.connect() as conn:
        return {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


# --- the first run itself -------------------------------------------------------

@pytest.mark.parametrize("ai", [False, True])
def test_the_first_run_path_works_end_to_end_on_a_fresh_store(monkeypatch, tmp_path, ai):
    """Unlock, access, standards, leaders, a cycle, a review shared and
    acknowledged; then a posting, an application, the pipeline, a notice and
    the numbers. Each step is reached from the page before it (see
    people_walk), with the AI off and with it on."""
    walk = people_walk.first_run(kit.make_world(monkeypatch, tmp_path, bare=True), monkeypatch, ai=ai)
    kinds = {job["kind"] for job in _rows("ai_jobs")}
    assert kinds == ({"review_draft", "hiring_score"} if ai else set())
    assert all(resp.status_code < 500 for _, _, _, resp in walk.seen)
    # the applicant was told about AI exactly when it was in use
    assert bool(_rows("hr_applications")[0]["ai_disclosed"]) == ai


def _rows(table: str) -> list[dict]:
    with people_db.connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


# --- the shift database and its backups -----------------------------------------

def test_people_records_live_in_their_own_file(walked):
    ours, theirs = people_db.db_path(), os.path.abspath(shift_db.DB_PATH)
    assert os.path.abspath(ours) != theirs and os.path.basename(ours) == "people.db"
    assert os.path.exists(ours) and os.path.exists(theirs) and not os.path.samefile(ours, theirs)
    with sqlite3.connect(theirs) as conn:
        shift_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = people_tables()
    assert {"rv_reviews", "rv_observations", "hr_applications", "hr_answers", "ai_jobs"} <= tables
    assert not (tables - {"meta"}) & shift_tables         # each store has its own "meta", nothing else shared
    assert not {t for t in shift_tables if t.startswith(("rv_", "hr_", "standards_", "ai_", "access_"))}


def test_the_shift_backup_holds_no_people_table_and_nothing_that_was_typed(walked):
    backup = shift_db.export_json()
    payload = json.loads(backup)
    for name in people_tables() - {"meta"}:
        assert name not in payload and name not in shift_db.EXPORT_TABLES, name
    # A hired applicant's name is the one thing that crosses, by the Operator's own tap.
    hired = f"{people_walk.APPLICANT['first']} {people_walk.APPLICANT['last']}"
    assert [m["name"] for m in payload["team_members"] if people_walk.APPLICANT["last"] in m["name"]] == [hired]
    without_roster = json.dumps({key: value for key, value in payload.items() if key != "team_members"})
    for private in walked.private:
        assert private not in without_roster, private
    for private in walked.private:
        if private != people_walk.APPLICANT["last"]:
            assert private not in backup, private
    # the scheduled copy of that backup is the same document
    resp = cfa_app.app.test_client().get(f"/scheduled/shift-backup?token={cfa_app.SCHEDULE_SECRET}")
    assert resp.status_code == 200
    scheduled = resp.get_data(as_text=True)
    assert "rv_reviews" not in scheduled and "hr_applications" not in scheduled
    for private in walked.private:
        if private != people_walk.APPLICANT["last"]:
            assert private not in scheduled, private


def test_the_raw_shift_database_file_holds_nothing_that_was_typed(walked):
    with sqlite3.connect(shift_db.DB_PATH) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        dump = "\n".join(conn.iterdump())
    for private in walked.private:
        if private != people_walk.APPLICANT["last"]:
            assert private not in dump, private
    assert people_walk.APPLICANT["email"] not in dump and "Fernhollow" not in dump


def test_the_people_backup_is_the_only_copy_and_leaves_the_pepper_out(walked):
    backup = json.loads(people_db.export_json("Operator"))
    # everything but the two tables of local housekeeping (the link pepper, and who has the lock open)
    assert set(backup["tables"]) == people_tables() - {"meta", "unlocks"}
    assert people_walk.OBSERVATION in json.dumps(backup) and people_walk.ANSWERS[0] in json.dumps(backup)
    token = walked.ids["private"].rsplit("/", 1)[1]
    assert token not in json.dumps(backup) and "token_pepper" not in json.dumps(backup)


# --- what People code may never read --------------------------------------------

def test_people_code_never_touches_private_meetings_or_goals():
    """1:1 agendas, member threads, daily goals and goal updates are private
    to the people in them. No People file names those tables or the
    functions that read them."""
    assert len(PEOPLE_SOURCES) >= 8
    for path in PEOPLE_SOURCES:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("oneonone", "daily_goals", "goal_updates"):
            assert forbidden not in source, (os.path.basename(path), forbidden)


def test_people_code_sends_nothing_to_slack_groupme_or_email():
    """Nothing about a named person leaves through a chat or mail channel:
    no People file posts anywhere but the AI engine."""
    for path in PEOPLE_SOURCES:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("import requests", "import smtplib", "import slack_coverage", "import app",
                          "from app import", "slack.com", "api.groupme", "formsubmit", "chat.postmessage"):
            assert forbidden not in source.lower(), (os.path.basename(path), forbidden)


# --- the server log ---------------------------------------------------------------

def test_nothing_private_is_printed_across_both_flows(walked, capsys):
    """Names, contact details, answers, review text, PINs and the
    applicant's private link never reach stdout or stderr, where Render
    keeps them as logs."""
    assert len(walked.ai_calls) == 2                      # both AI jobs really ran
    for private in walked.private:
        assert private not in walked.printed, private
    for name in ("Quillfeather", "Jordan", "Lena", "Morgan Example", "jordan.quill", "555-0142"):
        assert name not in walked.printed, name
    for pin in set(kit.PINS.values()):
        assert pin not in walked.printed, "a PIN was printed"
    assert not re.search(r"[0-9a-f]{32}", walked.printed)         # no private-link token, no hash
    print("the capture is listening")                     # and the check itself can see a print
    assert "listening" in capsys.readouterr().out


def test_a_failed_ai_job_logs_its_kind_and_nothing_else(monkeypatch, tmp_path, capsys):
    world = kit.make_world(monkeypatch, tmp_path, bare=True)
    walk = people_walk.Walk(world)
    people_walk.set_up(walk)

    def falls_over(system, user):
        raise RuntimeError("could not read: " + user)     # an error that quotes what it was sent

    kit.fake_ai(monkeypatch, falls_over)
    capsys.readouterr()
    job = people_ai.start_job("review_draft", 1, {
        "levels": ["a", "b", "c", "d"], "dimensions": [],
        "evidence": [{"id": 1, "date": "2026-11-01", "dimension_key": None, "kind": "note",
                      "text": people_walk.OBSERVATION}], "reflection": []}, "Operator", wait=True)
    printed = capsys.readouterr()
    found = people_ai.latest_job("review_draft", 1)
    assert job and found["status"] == "failed"
    assert "WARNING: People AI review_draft job failed" in printed.out
    assert people_walk.OBSERVATION not in printed.out + printed.err
    assert people_walk.OBSERVATION not in (found["error"] or "")


# --- the rest of Team Hub ---------------------------------------------------------

def test_a_people_fault_never_breaks_the_leader_tools_or_the_hub(monkeypatch, tmp_path):
    world = kit.make_world(monkeypatch, tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("People is down")

    working = people.nav_rows
    monkeypatch.setattr(people, "nav_rows", broken)
    for who in ("operator", "rae", "lena"):
        client = cfa_app.app.test_client()
        kit.sign_in(client, who)
        for path in ("/shift/", "/shift/more", "/hub/library", "/hub/menu"):
            resp = client.get(path)
            assert resp.status_code == 200, (who, path)
            assert "/shift/people" not in resp.get_data(as_text=True)
    member = cfa_app.app.test_client()
    kit.sign_in(member, "member")
    assert member.get("/hub/").status_code == 200 and member.get("/hub/menu").status_code == 200
    # the same when the feature counts fall over inside People instead
    monkeypatch.setattr(people, "nav_rows", working)
    for module in people._FEATURE_MODULES.values():
        monkeypatch.setattr(module, "nav_counts", broken)
    client = cfa_app.app.test_client()
    kit.sign_in(client, "operator")
    menu = client.get("/shift/more").get_data(as_text=True)
    assert 'href="/shift/people"' in menu                 # the row is still there, without a badge
    assert client.get("/shift/").status_code == 200


def test_people_names_never_show_up_on_other_team_hub_pages(walked):
    """A leader review and an application leave no trace on the leader home,
    the menu, the Hub or the roster page, apart from a hired person's name
    on the roster the Operator added them to."""
    for who in ("operator", "hari", "ada"):
        for path in ("/shift/", "/shift/more", "/hub/library", "/hub/menu", "/shift/todo"):
            page = walked.page(who, path)
            for private in walked.private + [people_walk.APPLICANT["first"]]:
                assert private not in page, (who, path, private)


# --- the pages themselves ---------------------------------------------------------

def test_every_page_in_both_flows_is_locked_down_and_runs_no_script(walked):
    pages = [item for item in walked.seen if item[2].startswith(PRIVATE_PAGES)]
    html = [item for item in pages if item[3].mimetype == "text/html"]
    assert len(html) > 80 and {who for who, *_ in html} == {"operator", "lena", "public"}
    assert any(path.startswith("/careers") for _, _, path, _ in html)
    for who, method, path, resp in pages:
        for header, value in STRICT.items():
            assert resp.headers.get(header) == value, (who, method, path, header)
    for who, method, path, resp in html:
        assert resp.headers.get("Content-Security-Policy") == CSP, (who, method, path)
        body = resp.get_data(as_text=True)
        assert "<script" not in body.lower(), (who, path)
        assert not ON_ATTRIBUTE.search(body), (who, path, ON_ATTRIBUTE.search(body).group(0)[-60:])
        assert "javascript:" not in body.lower(), (who, path)
        for outside in re.findall(r'(?:src|href)="(https?://[^"]+)"', body):
            assert outside.startswith("http://localhost/"), (who, path, outside)    # nothing loaded from elsewhere


def test_the_public_pages_set_no_cookie(walked):
    public = [item for item in walked.seen if item[0] == "public"]
    assert len(public) >= 7 and any(method == "POST" for _, method, _, _ in public)
    for _, method, path, resp in public:
        assert "Set-Cookie" not in resp.headers, (method, path)
        assert "Cookie" not in resp.headers.get("Vary", ""), (method, path)   # the session was never read
    # the pages a mistake leads to as well
    client = cfa_app.app.test_client()
    slug = walked.ids["slug"]
    for path, data in (("/careers/no-such-posting", None), ("/careers/a/" + "0" * 32, None),
                       (f"/careers/{slug}/apply", {"first_name": "Only"}),
                       (f"/careers/{slug}/apply", {"website": "robot"})):
        resp = client.post(path, data=data) if data else client.get(path)
        assert "Set-Cookie" not in resp.headers, path
    assert client.get_cookie("session") is None


def test_the_applicant_page_shows_no_score_stage_or_leader_note(walked):
    page = walked.page("public", walked.ids["private"])
    for hidden in ("Hired", "Offer", "Screening", "Level 3", people_walk.INTERVIEW_NOTE, "Hari",
                   "score", "Suggested"):
        assert hidden not in page, hidden
    assert "A hiring decision has been made for this posting." in page


def test_scoring_pages_in_the_flow_never_carry_contact_details(walked):
    at = f"/shift/hiring/candidates/{walked.ids['candidate']}"
    for who, method, path, resp in walked.html():
        if path == at and resp.status_code == 200:
            body = resp.get_data(as_text=True)
            assert people_walk.APPLICANT["email"] not in body and people_walk.APPLICANT["phone"] not in body
    assert hdb.candidate_contact(walked.ids["candidate"])["email"] == people_walk.APPLICANT["email"]


def test_what_the_ai_was_sent_names_nobody(walked):
    """Both requests, exactly as stored: no name, no contact detail, no
    title, nothing but the notes and answers with names taken out."""
    sent = " ".join(user for _, user in walked.ai_calls)
    stored = " ".join(job["input_json"] or "" for job in _rows("ai_jobs"))
    for text in (sent, stored):
        for name in ("Lena", "Quillfeather", "Jordan", people_walk.APPLICANT["email"],
                     people_walk.APPLICANT["phone"], "Morgan", people_walk.EXPERIENCE):
            assert name not in text, name
    assert "zinnia checklist" in sent and "persimmon inventory" in sent      # the evidence itself did go
