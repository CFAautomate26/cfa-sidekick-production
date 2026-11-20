"""Leader reviews, the pages: the home page, the leaders under review, the
observation log and review cycles, and who may open each. Everyone here is
invented."""

import re

import pytest

import app as cfa_app
import people_db
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk

DENIED, NOT_YOURS, page, post, opened = rk.DENIED, rk.NOT_YOURS, rk.page, rk.post, rk.opened


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return kit.make_world(monkeypatch, tmp_path)


# --- home ---------------------------------------------------------------------------

def test_home_is_for_reviewers_and_everyone_else_goes_to_their_own(world):
    made = rk.one_review(world)
    for who in ("lena", "ada", "hari"):                 # no grant, admin role only, hiring only
        resp = kit.open_as(world, who).get("/shift/reviews")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/reviews/mine"), who
    html = page(kit.open_as(world, "rae"), "/shift/reviews")
    assert "Leaders you review" in html and f'href="/shift/reviews/leaders/{made["subject"]}"' in html
    assert f'href="/shift/reviews/review/{made["review"]}"' in html and "Draft" in html
    assert "Start a review cycle" not in html and "/shift/reviews/calibration" not in html
    assert "/shift/reviews/health" not in html and 'href="/shift/reviews/leaders"' not in html
    html = page(kit.open_as(world, "operator"), "/shift/reviews")
    assert "Start a review cycle" in html and f'href="/shift/reviews/calibration/{made["cycle"]}"' in html
    assert 'href="/shift/reviews/leaders"' in html and "/shift/reviews/health" in html
    assert "conversation starter, not a verdict" in html


def test_the_people_home_and_menu_find_the_review_pages(world):
    made = rk.one_review(world, due=-1)
    html = page(kit.open_as(world, "operator"), "/shift/people")
    assert 'href="/shift/reviews"' in html and "1 past due" in html
    html = page(kit.open_as(world, "rae"), "/shift/people")
    assert 'href="/shift/reviews"' in html and "1 to finish" in html
    lena = kit.open_as(world, "lena")
    html = page(lena, "/shift/people")
    assert 'href="/shift/reviews/mine"' in html and 'href="/shift/reviews"' not in html
    assert "My review" in page(lena, "/shift/more") and "1 waiting" in page(lena, "/shift/more")
    assert "My review" not in page(kit.open_as(world, "hari"), "/shift/more")
    assert made


def test_home_explains_when_standards_are_missing(world):
    rk.add_subject(world)
    with people_db.connect() as conn:
        conn.execute("UPDATE standards_models SET active=0")
        conn.commit()
    op = kit.open_as(world, "operator")
    html = page(op, "/shift/reviews")
    assert "Load Our standards first." in html and 'href="/shift/people/standards"' in html
    assert "Start a review cycle" not in html
    html = post(op, "/shift/reviews/cycles/new", {"name": "Autumn", "period_start": rk.day(-30),
                                                  "period_end": rk.day(), "due_date": rk.day(7)})
    assert "Load Our standards first. A review rates each standard, so it needs them." in html
    assert rdb.cycles() == []


# --- the leaders under review ----------------------------------------------------------

def test_only_the_operator_sets_up_leaders(world):
    ids = world["ids"]
    for who in ("rae", "lena", "ada"):
        client = kit.open_as(world, who)
        assert NOT_YOURS in post(client, "/shift/reviews/leaders/add", {"display_name": "Sneaky"})
        resp = client.get("/shift/reviews/leaders")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
        assert NOT_YOURS in post(client, "/shift/reviews/leaders/1/edit", {"display_name": "X"})
        assert NOT_YOURS in post(client, "/shift/reviews/cycles/new", {"name": "X"})
    assert rdb.subjects() == [] and ids


def test_first_run_adds_every_team_hub_leader_with_the_operator_reviewing(world):
    op = kit.open_as(world, "operator")
    html = page(op, "/shift/reviews/leaders")
    assert "Start with every Team Hub leader" in html and "Nobody is under review yet." in html
    html = post(op, "/shift/reviews/leaders/add", {"everyone": "1"})
    assert "Added 4 leaders." in html and "Start with every Team Hub leader" not in html
    rows = rdb.subjects()
    assert [r["display_name"] for r in rows] == ["Ada", "Hari", "Lena", "Rae"]
    assert all(r["leader_id"] and r["reviewer_leader_id"] is None for r in rows)
    assert "Every Team Hub leader is already on the list." in post(
        op, "/shift/reviews/leaders/add", {"everyone": "1"})
    assert len(rdb.subjects()) == 4


def test_add_and_change_a_leader(world):
    ids = world["ids"]
    op = kit.open_as(world, "operator")
    html = page(op, "/shift/reviews/leaders")
    for title in rdb.TITLE_SUGGESTIONS:                  # suggestions, not a fixed list
        assert f'<option value="{title}">' in html
    assert re.search(r'name="reviewer".*?<option value="%d"[^>]*>Rae</option>' % ids["rae"], html, re.S)
    assert f'<option value="{ids["lena"]}">Lena</option>' in html     # as a sign-in to link
    # a leader holds "reviewer" or they are not offered, and not accepted
    html = post(op, "/shift/reviews/leaders/add", {"leader": ids["hari"], "reviewer": ids["lena"]})
    assert "That leader or reviewer isn&#39;t available any more." in html and rdb.subjects() == []
    html = post(op, "/shift/reviews/leaders/add", {
        "leader": ids["lena"], "display_name": "", "title": "Team leader", "area": "Front of house",
        "reviewer": ids["rae"]})
    assert "Lena is now under review." in html
    lena = rdb.subjects()[0]
    # the name is taken from the sign-in's own row, never from the form
    assert (lena["leader_id"], lena["leader_name"], lena["reviewer_name"]) == (ids["lena"], "Lena", "Rae")
    assert (lena["title"], lena["area"]) == ("Team leader", "Front of house")
    html = post(op, "/shift/reviews/leaders/add", {"display_name": "Jo Example"})        # by name only
    assert "Jo Example is now under review." in html and "No sign-in linked" in html
    html = post(op, "/shift/reviews/leaders/add", {"leader": ids["rae"], "reviewer": ids["rae"]})
    assert "A leader can&#39;t review themselves. Choose someone else." in html
    html = post(op, f"/shift/reviews/leaders/{lena['id']}/edit", {
        "leader": ids["lena"], "display_name": "Lena B", "title": "Director", "area": "", "reviewer": ""})
    assert "Saved." in html
    lena = rdb.get_subject(lena["id"])
    assert (lena["display_name"], lena["title"], lena["reviewer_leader_id"], lena["active"]) \
        == ("Lena B", "Director", None, 0)                 # the tick was left off: not under review
    assert "Not under review right now" in html


def test_a_link_that_no_longer_matches_is_flagged_for_the_operator(world):
    subject = rk.add_subject(world, "lena", "rae")
    op = kit.open_as(world, "operator")
    assert "Needs re-linking" not in page(op, "/shift/reviews/leaders")
    with people_db.connect() as conn:                      # a restore renumbered or renamed sign-ins
        conn.execute("UPDATE rv_subjects SET leader_name='Lena Before', reviewer_name='Rae Before' "
                     "WHERE id=?", (subject,))
        conn.commit()
    html = page(op, "/shift/reviews/leaders")
    assert "Needs re-linking" in html and "Reviewer needs re-linking" in html
    assert "so they can't open their review" in html and "only you can open" in html
    # and it fails closed for both of them
    resp = kit.open_as(world, "rae").get(f"/shift/reviews/leaders/{subject}")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
    # holding the capability is part of it too: a reviewer whose grant is removed is flagged
    rk.add_subject(world, "hari", "rae")
    grant = next(g for g in people_db.grants() if g["capability"] == "reviewer")
    people_db.revoke(grant["id"], "Operator")
    assert page(op, "/shift/reviews/leaders").count("Reviewer needs re-linking") == 2


def test_a_sign_in_that_was_switched_off_stays_linked_but_is_not_offered_to_others(world):
    ids = world["ids"]
    subject = rk.add_subject(world, "lena", "rae")
    shift_db.set_leader_active(ids["lena"], False)
    op = kit.open_as(world, "operator")
    html = page(op, "/shift/reviews/leaders")
    assert "Needs re-linking" not in html                      # the id and name still match
    assert html.count(f'<option value="{ids["lena"]}"') == 1   # only on her own form, already chosen
    assert f'<option value="{ids["lena"]}" selected>Lena</option>' in html
    post(op, f"/shift/reviews/leaders/{subject}/edit", {
        "leader": ids["lena"], "display_name": "Lena", "title": "Supervisor", "reviewer": ids["rae"],
        "active": "1"})
    row = rdb.get_subject(subject)
    assert (row["leader_id"], row["leader_name"], row["title"]) == (ids["lena"], "Lena", "Supervisor")
    # the helper adds active sign-ins only
    post(op, "/shift/reviews/leaders/add", {"everyone": "1"})
    assert [r["display_name"] for r in rdb.subjects()] == ["Ada", "Hari", "Lena", "Rae"]


# --- the observation log ----------------------------------------------------------------

def test_a_reviewer_writes_and_tends_the_log(world):
    subject = rk.add_subject(world, "lena", "rae", title="Team leader", area="Front of house")
    rae = kit.open_as(world, "rae")
    log = f"/shift/reviews/leaders/{subject}"
    html = page(rae, log)
    assert "Tap the microphone on your keyboard to talk instead of type." in html
    assert "Notes that count toward a review are shown to Lena with that review" in html
    assert "What to write, and what never to write" in html
    assert f'value="{shift_db.today_local()}"' in html and "No notes yet." in html
    assert 'value="reliable"' in html and "Not tied to one standard" in html
    assert opened("observation log opened") == [("Rae", subject)]
    html = post(rae, f"{log}/observe", {"obs_date": rk.day(-1), "dimension_key": "guest-care",
                                        "kind": "strength", "body": "Walked a guest to their car in the rain."})
    assert "Note saved." in html and "Walked a guest to their car in the rain." in html
    assert "Did well" in html and "Guest care" in html and "Check the wording" not in html
    note = rdb.observations(subject)[0]
    assert (note["author_name"], note["author_leader_id"], note["kind"]) == ("Rae", world["ids"]["rae"], "strength")
    # an unknown standard is filed under none; the note is still saved
    post(rae, f"{log}/observe", {"obs_date": rk.day(-40), "dimension_key": "made-up", "kind": "note",
                                 "body": "Last month: covered two closes."})
    older = rdb.observations(subject)[-1]
    assert older["dimension_key"] is None
    html = page(rae, log)
    months = re.findall(r'<div class="sec-head"><h2>([A-Z][a-z]+ \d{4})</h2>', html)
    assert len(months) == 2 and html.index("Walked a guest") < html.index("Last month")      # newest first
    # leave out, count again, remove
    html = post(rae, f"/shift/reviews/observations/{note['id']}/toggle")
    assert "That note is left out of the review." in html and "Left out of the review" in html
    html = post(rae, f"/shift/reviews/observations/{note['id']}/toggle")
    assert "That note counts toward the review again." in html
    html = post(rae, f"/shift/reviews/observations/{older['id']}/remove")
    assert "Note removed." in html and "Last month: covered two closes." not in html
    assert "Removed notes" not in html                       # a reviewer does not see the removed list
    html = page(kit.open_as(world, "operator"), log)
    assert "Removed notes (1)" in html and "Last month: covered two closes." in html
    assert DENIED in post(rae, f"/shift/reviews/observations/{older['id']}/toggle")       # gone is gone


def test_the_wording_check_shows_above_the_note_and_never_blocks(world):
    subject = rk.add_subject(world)
    rae = kit.open_as(world, "rae")
    html = post(rae, f"/shift/reviews/leaders/{subject}/observe", {
        "obs_date": rk.day(-1), "kind": "growth",
        "body": "Seemed lazy on close and has been off sick a lot."})
    assert "Note saved. Have a look at the wording check shown above it." in html
    assert len(rdb.observations(subject)) == 1               # saved all the same
    assert "Check the wording of this note." in html
    assert "“lazy” (Personality labels)" in html and "“sick” (Health and disability)" in html
    assert "Describe what the person did or said, not what they are like." in html
    assert html.index("Check the wording of this note.") < html.index("Seemed lazy on close")


def test_a_note_that_cannot_be_saved_keeps_what_was_typed(world):
    subject = rk.add_subject(world)
    rae = kit.open_as(world, "rae")
    resp = rae.post(f"/shift/reviews/leaders/{subject}/observe", data={
        "obs_date": rk.day(3), "kind": "growth", "dimension_key": "teamwork",
        "body": "Typed out a long note about Tuesday's close."})
    html = resp.data.decode()
    assert resp.status_code == 400 and "That date hasn&#39;t happened yet." in html
    assert "Typed out a long note about Tuesday&#39;s close.</textarea>" in html
    assert re.search(r'<option value="teamwork" selected>', html) and rdb.observations(subject) == []


def test_who_may_open_a_log(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae")
    ada = rk.add_subject(world, "ada", None)                 # the Operator reviews Ada
    note = rk.observe(lena, "Kept the line moving at lunch.")
    other = rk.observe(ada, "Ran the huddle.", by="Operator")
    body = {"obs_date": rk.day(-1), "kind": "note", "body": "Should never be saved."}

    def shut(client, subject, obs):
        resp = client.get(f"/shift/reviews/leaders/{subject}")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
        assert DENIED in post(client, f"/shift/reviews/leaders/{subject}/observe", body)
        assert DENIED in post(client, f"/shift/reviews/observations/{obs}/toggle")
        assert DENIED in post(client, f"/shift/reviews/observations/{obs}/remove")

    # the leader the notes are about, a leader with no grant, an admin-role leader, a hiring leader
    for who in ("lena", "ada", "hari"):
        shut(kit.open_as(world, who), lena, note)
    rae = kit.open_as(world, "rae")
    assert "Kept the line moving at lunch." in page(rae, f"/shift/reviews/leaders/{lena}")
    shut(rae, ada, other)                                    # a reviewer, but not Ada's
    assert rae.get("/shift/reviews/leaders/999").status_code == 302          # not there: the same answer
    assert "Ran the huddle." in page(kit.open_as(world, "operator"), f"/shift/reviews/leaders/{ada}")
    # nothing was written or changed by any of that
    assert len(rdb.observations(lena)) == 1 and rdb.get_observation(note)["in_review"] == 1
    assert len(rdb.observations(ada)) == 1 and rdb.get_observation(other)["in_review"] == 1
    # access goes when the grant goes, on the next tap
    grant = next(g for g in people_db.grants() if g["capability"] == "reviewer")
    people_db.revoke(grant["id"], "Operator")
    shut(rae, lena, note)
    assert ids


def test_a_reviewer_never_opens_their_own_log_even_if_assigned(world):
    ids = world["ids"]
    subject = rk.add_subject(world, "rae", None)
    with people_db.connect() as conn:                        # a row that should never exist
        conn.execute("UPDATE rv_subjects SET reviewer_leader_id=?, reviewer_name='Rae' WHERE id=?",
                     (ids["rae"], subject))
        conn.commit()
    rk.observe(subject, "About Rae herself.", by="Operator")
    rae = kit.open_as(world, "rae")
    resp = rae.get(f"/shift/reviews/leaders/{subject}")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
    assert "Leaders you review" not in page(rae, "/shift/reviews")
    assert "About Rae herself." not in page(rae, "/shift/reviews")


def test_a_renamed_or_renumbered_reviewer_fails_closed(world):
    ids = world["ids"]
    subject = rk.add_subject(world, "lena", "rae")
    rae = kit.open_as(world, "rae")
    assert page(rae, f"/shift/reviews/leaders/{subject}")
    for change in ("reviewer_name='Raelynn'", f"reviewer_leader_id={ids['rae'] + 50}"):
        with people_db.connect() as conn:
            conn.execute("UPDATE rv_subjects SET reviewer_leader_id=?, reviewer_name='Rae' WHERE id=?",
                         (ids["rae"], subject))
            conn.execute(f"UPDATE rv_subjects SET {change} WHERE id=?", (subject,))
            conn.commit()
        resp = rae.get(f"/shift/reviews/leaders/{subject}")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people"), change


# --- cycles -------------------------------------------------------------------------------

def test_the_operator_starts_and_closes_a_cycle(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae")
    op = kit.open_as(world, "operator")
    html = post(op, "/shift/reviews/cycles/new", {
        "name": "Autumn check-in", "period_start": rk.day(-60), "period_end": rk.day(),
        "due_date": rk.day(14), "ask_reflection": "1"})
    assert "Cycle started." in html and "Autumn check-in" in html
    cycle = rdb.cycles()[0]
    assert (cycle["ask_reflection"], cycle["status"], cycle["total"]) == (1, "open", 1)
    review = rdb.cycle_reviews(cycle["id"])[0]
    assert (review["status"], review["model_id"], review["reviewer_name"]) == ("draft", ids["model"], "Rae")
    assert f'href="/shift/reviews/review/{review["id"]}"' in html and "Reviewer: Rae" in html
    # a bad form changes nothing and says why
    html = post(op, "/shift/reviews/cycles/new", {"name": "Backwards", "period_start": rk.day(),
                                                  "period_end": rk.day(-5), "due_date": rk.day(5)})
    assert "The period can&#39;t end before it starts." in html and len(rdb.cycles()) == 1
    # a leader added later can join it
    hari = rk.add_subject(world, "hari", None)
    html = page(op, f"/shift/reviews/cycles/{cycle['id']}")
    assert "Not in this cycle yet" in html and "Add Hari to this cycle" in html
    html = post(op, f"/shift/reviews/cycles/{cycle['id']}/add", {"subject_id": hari})
    assert "Added." in html and "Not in this cycle yet" not in html
    assert len(rdb.cycle_reviews(cycle["id"])) == 2
    # closing takes a tick, and says what it leaves unfinished
    assert "2 reviews are not finished" in html
    html = post(op, f"/shift/reviews/cycles/{cycle['id']}/close")
    assert "Tick the box to close the cycle." in html and rdb.get_cycle(cycle["id"])["status"] == "open"
    html = post(op, f"/shift/reviews/cycles/{cycle['id']}/close", {"confirm": "1"})
    assert "Cycle closed." in html and "closed cycle" in html and "Close this cycle" not in html
    assert "That cycle was already closed." in post(op, f"/shift/reviews/cycles/{cycle['id']}/close",
                                                    {"confirm": "1"})
    html = page(op, "/shift/reviews")
    assert "Closed cycles" in html and "No review cycle is open." in html
    assert lena


def test_cycle_pages_are_the_operators(world):
    made = rk.one_review(world)
    for who in ("rae", "lena"):
        client = kit.open_as(world, who)
        for path in (f"/shift/reviews/cycles/{made['cycle']}", f"/shift/reviews/calibration/{made['cycle']}",
                     "/shift/reviews/health"):
            resp = client.get(path)
            assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people"), path
        assert NOT_YOURS in post(client, f"/shift/reviews/cycles/{made['cycle']}/close", {"confirm": "1"})
        assert NOT_YOURS in post(client, f"/shift/reviews/cycles/{made['cycle']}/add", {"subject_id": 1})
    assert rdb.get_cycle(made["cycle"])["status"] == "open"
    resp = kit.open_as(world, "operator").get("/shift/reviews/cycles/999")
    assert resp.status_code == 302


def test_signed_out_locked_and_team_members_never_reach_a_review_page(world):
    made = rk.one_review(world)
    paths = ("/shift/reviews", "/shift/reviews/mine", f"/shift/reviews/review/{made['review']}",
             f"/shift/reviews/leaders/{made['subject']}", f"/shift/reviews/mine/{made['review']}")
    anon = cfa_app.app.test_client()
    member = cfa_app.app.test_client()
    kit.sign_in(member, "member")
    locked = cfa_app.app.test_client()
    kit.sign_in(locked, "rae")                               # signed in, PIN not entered again
    for path in paths:
        resp = anon.get(path)
        assert resp.status_code == 302 and "/shift/people" not in resp.headers["Location"], path
        resp = member.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/"), path
        resp = locked.get(path)
        assert resp.status_code == 302 and "/shift/people/unlock" in resp.headers["Location"], path
    resp = locked.post(f"/shift/reviews/leaders/{made['subject']}/observe",
                       data={"obs_date": rk.day(-1), "body": "While locked."})
    assert resp.status_code == 302 and "/shift/people/unlock" in resp.headers["Location"]
    assert rdb.observations(made["subject"]) == []
