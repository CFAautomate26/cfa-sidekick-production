"""People: one test (or a few) for each thing the third round of checks
found, after the second round of fixes (A3 = the attack on those fixes,
C3 = correctness, the word lists and the documents, B3 = the run in a real
browser). Each is named for the finding it pins and failed before its fix.
What only a real browser shows is pinned by its cause: the status code,
the attribute, the style rule or the markup. No real AI service is ever
called. Everyone here is invented."""

import json
import re

import pytest

import app as cfa_app
import careers
import people
import people_ai
import people_db
import people_hiring
import people_hiring_db as hdb
import people_reviews
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk
from tests.test_people_hiring_db import (applied, clock, criterion, draft, interviewed, live, live_with_ai,
                                         person)

page, post = rk.page, rk.post


@pytest.fixture()
def world(monkeypatch, tmp_path):
    made = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    careers.reset_throttles()
    yield made
    people._revoked.clear()


@pytest.fixture()
def public():
    """Someone who has never signed in to anything."""
    return cfa_app.app.test_client()


def flashed(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


def grounds(text: str) -> list[tuple[str, str]]:
    """(the words found, which list) for every protected-ground finding."""
    return [(f["term"], f["key"]) for f in people_ai.language_check(text)
            if f["key"] in ("health", "family", "religion", "age", "origin", "gender", "leave")]


# --- A3-1 / C3-1: health wording that only a pattern finds holds an answer back from the AI -----

HEALTH_BY_PATTERN = (
    "I am legally blind in one eye and I can't lift heavy boxes, but I never missed an opening in two years.",
    "I got hurt at work last year, and I still opened every Saturday.",
    "I walk with a cane, and I was never late for a shift.",
    "After an operation on my knee I went straight back to opening shifts.",
    "I am unable to stand for a full shift, so I ran the drive-thru window instead.",
)


@pytest.mark.parametrize("answer", HEALTH_BY_PATTERN)
def test_a31_an_answer_with_health_wording_only_a_pattern_finds_is_not_sent_to_the_ai(world, monkeypatch, answer):
    found = people_ai.language_check(answer)
    assert any(f["key"] == "health" for f in found), found
    assert not any(f["term"] in people_hiring._NEVER_SEND for f in found)       # no listed word: a pattern only
    posting_id = live_with_ai(monkeypatch)
    app_id = hdb.apply(posting_id, person(1), ai_shown=True)["id"]
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    assert hdb.save_answers(app_id, {first: answer, second: "A guest was lost, so I walked them to their table."}) \
        == "saved"
    payload = people_hiring.ai_payload(hdb.candidate(app_id))
    assert payload["withheld"] == [first]
    assert [a["criterion_id"] for a in payload["answers"]] == [second]
    assert answer.split(",")[0] not in json.dumps(payload)


def test_a31_the_page_says_an_answer_was_not_sent_and_shows_no_level_for_it(world, monkeypatch):
    posting_id = live_with_ai(monkeypatch)
    app_id = hdb.apply(posting_id, person(1), ai_shown=True)["id"]
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hdb.save_answers(app_id, {first: HEALTH_BY_PATTERN[0], second: "A guest was lost, so I walked them to their table."})
    calls = kit.fake_ai(monkeypatch, lambda system, user: {"criteria": [
        {"criterion_id": first, "evidence_summary": "Reliable.", "quote": "never missed an opening",
         "suggested_level": 2, "note": ""},
        {"criterion_id": second, "evidence_summary": "Helps.", "quote": "I walked them to their table",
         "suggested_level": 3, "note": ""}], "human_checks": []})
    hari = kit.open_as(world, "hari")
    hari.post(f"/shift/hiring/candidates/{app_id}/ai")
    assert len(calls) == 1 and "legally blind" not in calls[0][1] and "lift" not in calls[0][1]
    hari.post(f"/shift/hiring/candidates/{app_id}/score", data={f"level_{first}": "3", f"level_{second}": "3"})
    html = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert "The AI suggests <strong>level 2</strong>" not in html
    assert html.count("The AI suggests <strong>level 3</strong>") == 1
    assert people_hiring.WITHHELD_CHECK in people_ai.latest_job("hiring_score", app_id)["output"]["human_checks"]


# --- A3-4 / B3-F3 / B3-F8: a refused interview comes back on the page, never in the address -----

def test_a34_a_refused_interview_keeps_what_was_typed_on_the_page_and_out_of_the_address(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    app_id = applied(live())
    hari = kit.open_as(world, "hari")
    resp = hari.post(f"/shift/hiring/candidates/{app_id}/interview",
                     data={"on_date": "2026-12-01", "at_time": "after Jordan's exam", "mode": "phone",
                           "interviewer": "Morgan Avery"})
    assert resp.status_code == 400 and "Location" not in resp.headers       # the page itself, no redirect
    html = resp.data.decode()
    panel = html[html.index('id="add-interview"'):html.index('id="summary"')]
    assert '<details class="card tool" id="add-interview" open>' in html
    assert "<strong>Not added yet.</strong> Enter the time like 3:30 pm or 15:30, or leave it empty." in panel
    assert 'name="on_date" type="date" required value="2026-12-01"' in panel
    assert 'value="after Jordan&#39;s exam"' in panel
    assert '<option value="phone" selected>' in panel
    assert 'name="interviewer" type="text" maxlength="80" value="Morgan Avery"' in panel      # B3-F3
    # the form posts to an address that ends at the panel, so the page that comes back opens there
    assert f'action="/shift/hiring/candidates/{app_id}/interview#add-interview"' in panel
    assert "iv_" not in html and hdb.interviews(app_id) == []
    # nothing typed is read from an address any more
    plain = page(hari, f"/shift/hiring/candidates/{app_id}?iv_on_date=2026-12-01&iv_at_time=zzz")
    assert '<details class="card tool" id="add-interview">' in plain and 'value="zzz"' not in plain
    assert 'name="at_time" type="time" value=""' in plain
    assert 'name="interviewer" type="text" maxlength="80" value="Hari"' in plain
    # put right, it saves
    post(hari, f"/shift/hiring/candidates/{app_id}/interview",
         {"on_date": "2026-12-01", "at_time": "15:30", "mode": "phone", "interviewer": "Morgan Avery"})
    assert [(i["on_date"], i["at_time"], i["interviewer"]) for i in hdb.interviews(app_id)] == [
        ("2026-12-01", "15:30", "Morgan Avery")]


def test_a34_a_date_more_than_a_year_away_is_said_beside_the_form(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    app_id = applied(live())
    resp = kit.open_as(world, "hari").post(
        f"/shift/hiring/candidates/{app_id}/interview",
        data={"on_date": "2028-06-01", "at_time": "14:30", "mode": "phone", "interviewer": "Hari"})
    html = resp.data.decode()
    assert resp.status_code == 400
    assert "That interview date is more than a year away." in html[html.index('id="add-interview"'):]
    assert 'name="at_time" type="time" value="14:30"' in html


# --- C3-2: an interview that was logged and never marked still keeps the three-year notice ------

def test_c32_a_notice_is_kept_three_years_when_the_interview_was_logged_but_never_marked(world, monkeypatch):
    clock(monkeypatch, "2026-09-03 09:00")
    posting_id = live()
    unmarked, called_off, met, never = (applied(posting_id, n) for n in (1, 2, 3, 4))
    assert hdb.add_interview(unmarked, "2026-09-02", "", "in person", "Hari", "Hari") is None    # left "Scheduled"
    assert hdb.add_interview(called_off, "2026-09-02", "", "phone", "Hari", "Hari") is None
    assert hdb.set_interview_outcome(hdb.interviews(called_off)[-1]["id"], called_off, "cancelled", "Hari") is None
    interviewed(met, "2026-09-03")
    for app_id in (unmarked, called_off, met, never):
        assert hdb.log_notice(app_id, "not-moving-forward", "email", "We will not be moving forward.",
                              "2026-09-03", "Hari") is None
        assert hdb.move_stage(app_id, "new", "not-selected", "Hari", reason="Position filled") is None
    assert hdb.interviews(unmarked)[-1]["outcome"] == "scheduled"
    clock(monkeypatch, "2028-10-03 09:00")                            # 25 months on
    assert hdb.purge_all("Operator") == (4, 0)
    kept = {app_id: [(n["said"], n["given_to"]) for n in hdb.notices(app_id)]
            for app_id in (unmarked, called_off, met, never)}
    assert kept[unmarked] == [("We will not be moving forward.", "Jordan1 Sample")]     # the record the law asks for
    assert kept[met] == [("We will not be moving forward.", "Jordan3 Sample")]
    assert kept[called_off] == [("", "")] and kept[never] == [("", "")]
    # three full years after it was given, it goes like the rest
    clock(monkeypatch, "2029-09-04 09:00")
    assert hdb.purge_all("Operator") == (0, 2)
    assert [(n["said"], n["given_to"]) for n in hdb.notices(unmarked)] == [("", "")]


def test_c32_the_retention_page_and_the_guide_say_what_interviewed_means(world):
    html = page(kit.open_as(world, "operator"), "/shift/hiring/retention")
    assert "one marked Attended, or one that was logged and never marked" in html
    assert "only ones marked did not show, cancelled or rescheduled" in html
    guide = " ".join(open("docs/people.md", encoding="utf-8").read().split())
    assert "marked Attended, or logged and never marked" in guide
    legal = " ".join(open("docs/legal-counsel/hiring-and-leader-reviews.md", encoding="utf-8").read().split())
    assert "an interview marked Attended, or one logged and never marked, keeps the notice" in legal


# --- C3-3: ordinary restaurant talk in a reflection is not marked as a concern ------------------

ORDINARY_REFLECTIONS = (
    "I kept our food safety scores at 100 percent on my shifts.",
    "I handled two guest complaints this month and both guests left happy.",
    "I sometimes feel uncomfortable giving feedback to friends on the team.",
    "I want to be less afraid of giving direct feedback.",
    "I do not like to yell across the kitchen, so I need a better way to call for help.",
    "I was uncomfortable leading huddle at first, but it is easier now.",
    "A customer complained about the wait and I comped the meal.",
    "I touched every table in the dining room during the rush.",
    "I grabbed the mop when the drink station flooded.",
    "I was scared to run my first Saturday close, and it went fine.",
)
REAL_CONCERNS = (
    "I am afraid to close with him.", "I'm scared of her.", "He yelled at me in front of guests.",
    "She makes me uncomfortable when we are alone in the office.", "I feel uncomfortable around him.",
    "He screamed at the whole team on Friday.", "I do not feel safe on the night close.",
    "I have a safety concern about the fryer guard that nobody will fix.", "He grabbed my arm.",
    "I made a complaint and nothing happened.", "It is unfair that I am the only one written up.",
    "I am uncomfortable closing with him.", "I am frightened of what he will do if I say something.",
    "I was too scared to report it.", "He touched me on the shoulder and I did not like it.",
)


@pytest.mark.parametrize("said", ORDINARY_REFLECTIONS)
def test_c33_ordinary_restaurant_talk_is_not_on_the_concern_list(said):
    assert people_ai.concern_terms(said) == [], said


@pytest.mark.parametrize("said", REAL_CONCERNS)
def test_c33_wording_about_how_someone_was_treated_still_is(said):
    assert people_ai.concern_terms(said), said


def test_c33_every_sent_reflection_waits_for_the_operator_flagged_or_not(world):
    made = rk.one_review(world)
    rid = made["review"]
    lena, operator, rae = kit.open_as(world, "lena"), kit.open_as(world, "operator"), kit.open_as(world, "rae")
    said = "Things with my reviewer are not okay and I would like to talk to you alone."
    assert people_ai.concern_terms(said) == []                        # words on no list
    post(lena, f"/shift/reviews/mine/{rid}/reflection", {"r_reliable": said})         # saved, not sent
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 0}
    post(lena, f"/shift/reviews/mine/{rid}/reflection", {"r_reliable": said, "send": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator") == {
        "concerns": 0, "comments": 0, "reflections": 1}
    assert people_reviews.extra_counts("leader", world["ids"]["rae"], "Rae") == {}
    home = page(operator, "/shift/reviews")
    listed = home[home.index('id="follow-up"'):home.index('id="cycles"')]
    assert f'href="/shift/reviews/review/{rid}#reflection"' in listed
    assert "The leader sent a reflection. Read it." in listed and "may raise a concern" not in listed
    assert "so does every reflection a leader sends" in listed
    assert "1 leader sent a reflection for you to read." in page(operator, "/shift/people")
    assert "1 reflection to read" in page(operator, "/shift/more")
    mine = page(operator, f"/shift/reviews/review/{rid}")
    assert "The leader sent this reflection. Read it: a word list cannot tell whether a reflection raises a " \
           "concern, so every one waits here for you." in mine
    assert "I have read this reflection, and anything it raises was followed up outside this tool." in mine
    # the reviewer (who it may be about) sees the reflection and none of that
    theirs = page(rae, f"/shift/reviews/review/{rid}")
    assert said in theirs and "waits here for you" not in theirs and "Record that I read it" not in theirs
    assert 'id="follow-up"' not in page(rae, "/shift/reviews")
    # the Operator ticks it: gone from every list, and only that it was read is kept
    follow = f"/shift/reviews/review/{rid}/concern-followed"
    assert "Tick the box" in post(operator, follow, {"what": "reflection"})
    assert "Recorded as read." in post(operator, follow, {"what": "reflection", "confirm": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 0}
    assert said not in json.dumps(people_db.audit_feed())
    # changed afterwards, it comes back
    post(lena, f"/shift/reviews/mine/{rid}/reflection", {"r_reliable": said + " It is about Friday.", "send": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator")["reflections"] == 1


def test_c33_the_guide_says_every_reflection_is_listed(world):
    guide = " ".join(open("docs/people.md", encoding="utf-8").read().split())
    assert "Every reflection a leader sends waits for you in the same way" in guide
    assert "A reflection is checked by the word list only" not in guide


# --- C3-4: honest notes and postings the lists used to catch ------------------------------------

@pytest.mark.parametrize("honest", [
    "Caught a temperature problem in the walk-in cooler and called it in right away.",
    "Put the delivery away before the rush started.",
    "Was away from the front counter for 15 minutes during the rush.",
    "Helped a family with a stroller find a table and carried their tray over.",
    "Treats every guest like family.",
    "The old oil was not changed before lunch.",
    "Has built a real culture of thank-yous on her shifts.",
    "Handled a guest complaint about a cold sandwich, remade it and offered a cookie.",
    "A customer complained about the wait and she walked the order out herself.",
    "Called out the orders clearly all through lunch.",
    "Rang in a kids' meal wrong twice, then fixed both.",
])
def test_c34_honest_work_notes_are_no_longer_held_or_stripped_of_a_level(honest):
    assert grounds(honest) == [], honest
    assert people_ai.wider_terms(honest) == [], honest


@pytest.mark.parametrize("personal, key", [
    ("Was away for two weeks in October.", "leave"), ("She was away again on Friday.", "leave"),
    ("Has been away from work since the accident.", "leave"),
    ("Left early for a family emergency.", "family"), ("Her family comes first, so she will not close.", "family"),
    ("He is too old for the Saturday close.", "age"), ("She is 62 years old and slowing down.", "age"),
    ("His culture makes him too quiet with guests.", "origin"), ("Not a culture fit.", "origin"),
    ("He made a complaint about me to the Operator.", "leave"),
    ("Two people called out on Saturday.", "leave"), ("Her kids keep her from closing.", "family"),
])
def test_c34_the_same_words_about_a_person_are_still_caught(personal, key):
    assert key in {k for _, k in grounds(personal)}, grounds(personal)


def test_c34_a_family_run_restaurant_on_a_posting_raises_no_flag(world):
    posting_id = draft(description=(
        "We are a family-run restaurant with a real culture of looking after each other and our guests. "
        + "Serve guests at the counter and keep your station clean and stocked through the rush. " * 3))
    assert {f["term"] for f in hdb.wording_flags(hdb.get_posting(posting_id))} == set()


# --- C3-5: everyday wording about a ground that was on no list ----------------------------------

@pytest.mark.parametrize("note, key", [
    ("She has been to the clinic three times this month and it shows on the floor.", "health"),
    ("He is on new tablets and has been slow since.", "health"),
    ("He has a bad knee and will not do stock.", "health"),
    ("Had a panic attack in the back on Tuesday.", "health"),
    ("Goes to counselling on Wednesdays so she cannot open.", "health"),
    ("Dyslexia makes the checklists slow for her.", "health"),
    ("On restricted duty since the burn so she is no use on fries.", "health"),
    ("She is due in March and is already slowing down.", "family"),
    ("He keeps swapping closes to pick up his little one from the sitter.", "family"),
    ("English is not her first language so guests struggle to understand her.", "origin"),
    ("He is new to Canada and does not get how we do things here.", "origin"),
    ("He's off on comp again.", "leave"),
    ("He put in a claim after slipping in the kitchen and has been slow ever since.", "leave"),
    ("She went to the Operator about how I talk to her, so I am watching her closely.", "leave"),
    ("Fasting this month so she runs out of steam by dinner.", "religion"),
    ("She wears a headscarf and I keep her off the window.", "religion"),
    ("He's only 17 so I do not trust him with a close.", "age"),
])
def test_c35_everyday_wording_about_a_ground_is_now_caught_in_a_note(note, key):
    assert key in {k for _, k in grounds(note)}, grounds(note)


@pytest.mark.parametrize("work", [
    "Took the orders on the tablets in the drive-thru lane.", "Was 15 minutes late on Tuesday.",
    "Only 15 cars got through in the first half hour.", "She's off the pace at lunch.",
    "Is due for a break at two.", "The truck order is due in the morning.",
])
def test_c35_the_new_patterns_leave_the_work_alone(work):
    assert grounds(work) == [], grounds(work)


@pytest.mark.parametrize("question", [
    "Do you have little ones at home?", "Do you have a partner?", "Are you new to Canada?",
    "Where did you grow up?", "How is your back? This job has a lot of lifting.",
    "Have you ever made a claim against an employer?", "Do you have a car?",
    "Do you take any tablets we should know about?",
])
def test_c35_a_question_that_asks_it_in_everyday_words_is_held(question):
    assert hdb.question_hits(question), question


def test_c35_the_pages_still_say_the_list_cannot_read_meaning(world):
    posting_id = draft()
    criterion(posting_id, "Guest care", question="Is there any reason you could not work Friday nights?")
    criterion(posting_id, "Reliable", question="Do you have little ones at home?")
    blocks = hdb.question_blocks(hdb.get_posting(posting_id))
    assert [(b["name"], b["term"]) for b in blocks] == [("Reliable", "little ones")]      # the other one slips by
    html = page(kit.open_as(world, "operator"), f"/shift/hiring/postings/{posting_id}")
    assert "The list cannot read meaning" in html and "read every question before publishing" in html


# --- C3-6 / B3-F11: the public address follows a corrected title, and folds accents -------------

def test_c36_a_number_at_the_end_of_the_first_title_does_not_stay_in_the_address(world):
    fields = {"title": "Team Member 16+", "employment_type": "part-time", "description": "x" * 120,
              "pay_min": "17.95", "pay_max": "19.50", "vacancy": "existing"}
    posting_id, problems = hdb.create_posting(fields, "Hari")
    assert not problems and hdb.get_posting(posting_id)["slug"] == "team-member-16"
    assert hdb.save_posting(posting_id, {**fields, "title": "Team Member"}, "Hari") == []
    assert hdb.get_posting(posting_id)["slug"] == "team-member"
    # a second posting of the same job still gets, and keeps, its own number
    second, _ = hdb.create_posting({**fields, "title": "Team Member"}, "Hari")
    assert hdb.get_posting(second)["slug"] == "team-member-2"
    assert hdb.save_posting(second, {**fields, "title": "Team Member"}, "Hari") == []
    assert hdb.get_posting(second)["slug"] == "team-member-2"
    # ...until the first one moves away: a draft's address is exactly what its title gives now
    assert hdb.save_posting(posting_id, {**fields, "title": "Kitchen team"}, "Hari") == []
    assert hdb.save_posting(second, {**fields, "title": "Team Member"}, "Hari") == []
    assert (hdb.get_posting(posting_id)["slug"], hdb.get_posting(second)["slug"]) == ("kitchen-team", "team-member")


def test_b3f11_accented_letters_are_folded_not_dropped(world):
    posting_id = draft(title="Équipier de cuisine (soir)")
    assert hdb.get_posting(posting_id)["slug"] == "equipier-de-cuisine-soir"
    assert hdb._slug_base("Crème brûlée à la carte") == "creme-brulee-a-la-carte"
    assert hdb._slug_base("日本") == "job"                                # nothing left to make an address from


# --- C3-7: a bare "2:30" is asked about, not stored as half past two at night -------------------

@pytest.mark.parametrize("typed, stored", [
    ("2:30", None), ("1:00", None), ("6:45", None), ("3.30", None),
    ("02:30", "02:30"),                                                 # two digits: what a time picker sends
    ("7:30", "07:30"), ("9:00", "09:00"), ("11:15", "11:15"), ("14:30", "14:30"),
    ("2:30 pm", "14:30"), ("2:30 am", "02:30"), ("6am", "06:00"),
])
def test_c37_a_bare_afternoon_hour_is_not_guessed(typed, stored):
    assert hdb.clock_time(typed) == stored


def test_c37_the_invitation_never_reads_half_past_two_at_night_from_a_bare_time(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    app_id = applied(live())
    assert hdb.add_interview(app_id, "2026-11-12", "2:30", "phone", "Hari", "Hari") == \
        "Enter the time like 3:30 pm or 15:30, or leave it empty."
    assert hdb.interviews(app_id) == []


# --- C3-8 / B3-F8 / B3-F1: the ratings page loses nothing that was typed ------------------------

def test_c38_a_comment_typed_with_no_level_comes_back_in_its_box_with_the_reason_beside_it(world):
    made = rk.one_review(world)
    rid = made["review"]
    rae = kit.open_as(world, "rae")
    # the standard's own Save, with a comment and no level
    resp = rae.post(f"/shift/reviews/review/{rid}/rate",
                    data={"dimension_key": "guest-care", "comment-guest-care": "Walked a guest to their car."})
    assert resp.status_code == 400 and "Location" not in resp.headers
    html = resp.data.decode()
    card = html[html.index('id="d-guest-care"'):html.index('id="d-teamwork"')]
    assert "<strong>Not saved yet: choose a level.</strong>" in card
    assert ">Walked a guest to their car.</textarea>" in card
    assert "Choose a level, or “Not enough to rate yet”, then save." in html
    assert rdb.ratings(rid).get("guest-care") is None
    # another standard's Save: that one is saved, and the comment without a level is kept on the page
    resp = rae.post(f"/shift/reviews/review/{rid}/rate", data={
        "dimension_key": "reliable", "level-reliable": "3", "comment-reliable": "On time all month.",
        "comment-guest-care": "Walked a guest to their car.", "level-teamwork": "not_enough",
        "strengths": "", "growth": ""})
    assert resp.status_code == 400
    html = resp.data.decode()
    assert "Saved 2 standards." in html
    assert "A comment needs a level to be saved with. Choose a level for: Guest care." in html
    card = html[html.index('id="d-guest-care"'):html.index('id="d-teamwork"')]
    assert "<strong>Not saved yet: choose a level.</strong>" in card
    assert ">Walked a guest to their car.</textarea>" in card
    assert "Not saved yet" not in html[html.index('id="d-reliable"'):html.index('id="d-guest-care"')]
    stored = rdb.ratings(rid)
    assert stored["reliable"]["level"] == 3 and stored["teamwork"]["not_enough"]
    # each Save posts to an address that ends at its own standard, so the page comes back there
    assert f'formaction="/shift/reviews/review/{rid}/rate#d-guest-care"' in html
    # with a level picked, the same comment saves
    assert "Saved." in post(rae, f"/shift/reviews/review/{rid}/rate", {
        "dimension_key": "guest-care", "level-guest-care": "4", "comment-guest-care": "Walked a guest to their car.",
        "level-reliable": "3", "comment-reliable": "On time all month.", "level-teamwork": "not_enough"})
    assert rdb.ratings(rid)["guest-care"]["comment"] == "Walked a guest to their car."


def test_c38_save_all_with_nothing_changed_says_so(world):
    made = rk.one_review(world)
    rid = made["review"]
    rae = kit.open_as(world, "rae")
    form = {"dimension_key": "", "strengths": "", "growth": ""}
    for key in rk.KEYS:
        form[f"level-{key}"], form[f"comment-{key}"] = "3", "Seen on shift."
    assert "Saved 3 standards." in post(rae, f"/shift/reviews/review/{rid}/rate", form)
    again = post(rae, f"/shift/reviews/review/{rid}/rate", form)
    assert "Nothing to save: every rating is already saved." in again
    assert "Choose a level for at least one standard" not in again
    # with no level anywhere, it still asks for one
    empty = post(rae, f"/shift/reviews/review/{rk.one_review(world, 'ada')['review']}/rate", {"dimension_key": ""})
    assert "Choose a level for at least one standard, then save." in empty


def test_b3f1_the_summary_save_keeps_the_levels_and_comments_picked_on_the_page(world):
    made = rk.one_review(world)
    rid = made["review"]
    rae = kit.open_as(world, "rae")
    html = page(rae, f"/shift/reviews/review/{rid}")
    # the strengths and growth boxes and their button belong to the one ratings form
    assert '<textarea id="s-strengths" form="rv-rate" name="strengths"' in html
    assert f'form="rv-rate" formaction="/shift/reviews/review/{rid}/summary#summary">Save</button>' in html
    landed = post(rae, f"/shift/reviews/review/{rid}/summary", {
        "strengths": "Keeps the line moving.", "growth": "Hand the close to someone else.",
        "level-reliable": "4", "comment-reliable": "Opened every Saturday."})
    assert "Saved." in landed and "Also saved 1 rating you had picked." in landed
    assert rdb.ratings(rid)["reliable"]["level"] == 4
    assert rdb.get_review(rid)["strengths"] == "Keeps the line moving."
    # a rating's own Save brings the strengths and growth boxes along too
    post(rae, f"/shift/reviews/review/{rid}/rate", {
        "dimension_key": "teamwork", "level-teamwork": "2", "comment-teamwork": "",
        "strengths": "Keeps the line moving at lunch.", "growth": "Hand the close to someone else."})
    assert rdb.get_review(rid)["strengths"] == "Keeps the line moving at lunch."
    # the buttons that are still their own forms say so on the page
    assert html.count("Save your ratings first: this button reloads the page") >= 2


# --- C3-9: two messages that made no sense ------------------------------------------------------

DUTIES = ("This job means standing for most of a shift and lifting up to 20 kg. Can you do these duties, "
          "with or without accommodation?")


def test_c39_the_duties_question_is_held_under_its_own_sentence(world):
    posting_id = draft()
    criterion(posting_id, "Reliable", question=DUTIES)
    criterion(posting_id, "Guest care")
    assert hdb.question_hits(DUTIES) == [("accommodation", "health or disability", False)]
    block = hdb.question_blocks(hdb.get_posting(posting_id))[0]
    assert "leave and complaints" not in block["problem"]
    assert ("A question may ask whether someone can do the duties of the job with or without accommodation. "
            "It may not ask what they need, or why. If this one only asks about the duties, the Operator can "
            "confirm that here, under the publish check.") in block["problem"]
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.confirm_question(posting_id, crit["id"], "question", "Operator")
    assert hdb.publish(posting_id, "Operator", reviewed=True) == []


def test_c39_on_a_live_posting_the_refusal_sends_a_leader_to_the_operator_not_to_a_place_with_nothing_there(world):
    posting_id = live()
    crit = hdb.get_posting(posting_id)["criteria"][0]
    refused = hdb.save_criterion(crit["id"], posting_id, {**crit, "question": "Describe a time you helped a "
                                                          "guest with a disability."}, "Hari", reviewed=True)
    assert refused[0].startswith("This posting is live")
    assert "ask the Operator to make this change: on a live posting they tick the box on the criterion form" \
        in refused[1]
    assert "confirm that here, under the publish check" not in " ".join(refused)


# --- C3-10: the two confirmation rules no test would have caught --------------------------------

def test_c310_a_confirmed_interview_question_loses_its_confirmation_when_reworded(world):
    posting_id = draft()
    asked = "Tell us about a time you kept calm in a medical emergency."
    criterion(posting_id, "Guest care", interview_question=asked)
    criterion(posting_id, "Reliable")
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.confirm_question(posting_id, crit["id"], "interview", "Operator")
    assert hdb.get_posting(posting_id)["criteria"][0]["interview_ok"] == asked
    assert hdb.question_blocks(hdb.get_posting(posting_id)) == []
    # saved again word for word, the confirmation stays
    assert hdb.save_criterion(crit["id"], posting_id, {**crit, "name": "Guest care, always"}, "Hari") == []
    assert hdb.get_posting(posting_id)["criteria"][0]["interview_ok"] == asked
    # reworded, it is gone and the question is held again
    reworded = {**crit, "interview_question": "Tell us about your own medical history."}
    assert hdb.save_criterion(crit["id"], posting_id, reworded, "Hari") == []
    assert hdb.get_posting(posting_id)["criteria"][0]["interview_ok"] == ""
    assert [b["which"] for b in hdb.question_blocks(hdb.get_posting(posting_id))] == ["interview"]


def test_c310_nothing_is_confirmed_on_a_closed_posting(world):
    posting_id = draft()
    asked = "Describe a time you helped a guest with a disability."
    criterion(posting_id, "Guest care", question=asked)
    criterion(posting_id, "Reliable")
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.confirm_question(posting_id, crit["id"], "question", "Operator")
    assert hdb.publish(posting_id, "Operator", reviewed=True) == []
    assert hdb.close_posting(posting_id, "Operator")
    with people_db.connect() as conn:
        conn.execute("UPDATE hr_criteria SET question_ok='' WHERE id=?", (crit["id"],))
        conn.commit()
    assert hdb.confirm_question(posting_id, crit["id"], "question", "Operator") is False
    operator = kit.open_as(world, "operator")
    landed = post(operator, f"/shift/hiring/postings/{posting_id}/criteria/{crit['id']}/question-ok",
                  {"which": "question", "confirm": "1"})
    assert "There is nothing to confirm on that question any more." in landed
    assert hdb.get_posting(posting_id)["criteria"][0]["question_ok"] == ""


# --- C3-11: the documents say what the tool does today ------------------------------------------

def test_c311_the_guide_matches_the_fixes():
    guide = " ".join(open("docs/people.md", encoding="utf-8").read().split())
    assert "what you typed stays in its box" in guide                 # (b) a comment with no level
    assert "and the comment has to be typed again" not in guide
    assert "is exactly what its title gives" in guide                 # (a) the address follows the title
    assert "any answer in which the word list finds health wording" in guide      # (c) what hiring AI is not sent
    assert "an answer that says it another way is sent" in guide
    legal = " ".join(open("docs/legal-counsel/hiring-and-leader-reviews.md", encoding="utf-8").read().split())
    assert "an unflagged reflection is not listed" not in legal
    assert "Every reflection a leader has sent is listed for the Operator" in legal
    claude = " ".join(open("CLAUDE.md", encoding="utf-8").read().split())
    assert "every reflection a leader sends, waits in front of the Operator" in claude


# --- B3-F8: the reason a save was refused is on screen, beside the form -------------------------

def test_b3f8_a_message_stays_on_screen_while_the_page_scrolls(world):
    operator = kit.open_as(world, "operator")
    operator.post("/shift/people/settings", data={"lock_minutes": "30"})          # anything that flashes
    html = page(operator, "/shift/people/settings")
    assert re.search(r"\.pp-flashes \{ position: sticky; top: 8px;", html)
    assert re.search(r"\.pp-flashes ~ \[id\], \.pp-flashes ~ \* \[id\] \{ scroll-margin-top:", html)
    box = html[html.index('<div class="pp-flashes">'):]
    box = box[:box.index("</label></div>")]
    assert '<div class="flash" role="status">Settings saved.</div>' in box          # the message is inside the box
    assert box.endswith('<label class="pp-flash-x no-print" for="pp-flash-hide">Hide')
    assert html.index('<input type="checkbox" id="pp-flash-hide" class="pp-flash-hide sr no-print"') \
        < html.index('<div class="pp-flashes">')                      # the tick box that hides it comes just before
    assert "<script" not in html                                      # still no scripts: the tick box hides it
    # with nothing to say there is no box at all
    assert '<div class="pp-flashes">' not in page(operator, "/shift/people/settings")


def test_b3f8_a_refused_criterion_comes_back_open_with_what_was_typed(world):
    posting_id = live()
    crit = hdb.get_posting(posting_id)["criteria"][0]
    form = {"name": crit["name"], "definition": crit["definition"], "question": "How old are you?",
            "interview_question": "", "wording_reviewed": "1",
            **{f"anchor{n}": a for n, a in enumerate(crit["anchors"], 1)}, "anchor4": "TYPEDANCHOR and more."}
    hari = kit.open_as(world, "hari")
    resp = hari.post(f"/shift/hiring/postings/{posting_id}/criteria/{crit['id']}", data=form)
    assert resp.status_code == 400 and "Location" not in resp.headers
    html = resp.data.decode()
    fold = html[html.index(f'<details class="card tool" id="c{crit["id"]}"'):]
    fold = fold[:fold.index("</details>")]
    assert fold.startswith(f'<details class="card tool" id="c{crit["id"]}" open>')
    assert "Not saved yet. What you typed is still in the boxes below." in fold
    assert "This posting is live, so it has to keep passing the publish check." in fold
    assert ">How old are you?</textarea>" in fold and ">TYPEDANCHOR and more.</textarea>" in fold
    assert 'name="wording_reviewed" value="1" checked' in fold
    assert f'action="/shift/hiring/postings/{posting_id}/criteria/{crit["id"]}#c{crit["id"]}"' in fold
    assert hdb.get_posting(posting_id)["criteria"][0]["question"] == crit["question"]       # nothing was saved
    # the other folds stay closed and show what is stored
    other = hdb.get_posting(posting_id)["criteria"][1]
    assert f'<details class="card tool" id="c{other["id"]}">' in html
    # a hand-written criterion that is refused comes back the same way
    resp = hari.post(f"/shift/hiring/postings/{posting_id}/criteria",
                     data={"name": "", "definition": "TYPEDMEANING for the job.", "question": ""})
    assert resp.status_code == 400
    html = resp.data.decode()
    own = html[html.index('id="add-own"'):]
    assert 'id="add-own" style="margin-top:12px;" open>' in html
    assert "Not added yet. What you typed is still in the boxes below." in own
    assert ">TYPEDMEANING for the job.</textarea>" in own
    # on a closed posting there is no form to come back to: the reason is flashed as before
    assert hdb.close_posting(posting_id, "Operator")
    resp = hari.post(f"/shift/hiring/postings/{posting_id}/criteria/{crit['id']}", data=form)
    assert resp.status_code == 302 and "That posting is closed, so it can't be changed." in flashed(hari)


# --- B3-F5: nothing typed on the candidate page is wiped while the AI is reading ----------------

def test_b3f5_the_candidate_pages_boxes_are_paused_while_the_page_reloads_itself(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    posting_id = live_with_ai(monkeypatch)
    app_id = hdb.apply(posting_id, person(1), ai_shown=True)["id"]
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hdb.save_answers(app_id, {first: "I opened every Saturday.", second: "I walked a guest to their table."})
    interviewed(app_id, "2026-11-10")
    hari = kit.open_as(world, "hari")
    calm = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert 'http-equiv="refresh"' not in calm and "<fieldset class=\"hr-set\" disabled>" not in calm
    assert calm.count('<fieldset class="hr-set">') == 4               # answers, scorecard, interview, note
    with people_db.connect() as conn:                                 # a request the AI has not answered yet
        conn.execute("INSERT INTO ai_jobs (kind, target_id, status, requested_by, requested_at) "
                     "VALUES ('hiring_score', ?, 'queued', 'Hari', ?)", (app_id, shift_db.now_stamp()))
        conn.commit()
    busy = page(hari, f"/shift/hiring/candidates/{app_id}")
    assert 'http-equiv="refresh" content="6;' in busy
    assert busy.count('<fieldset class="hr-set" disabled>') == 4 and '<fieldset class="hr-set">' not in busy
    assert "<strong>The AI is reading the answers. Wait before you type.</strong>" in busy
    assert "the boxes on this page are paused until then" in busy and "check now</a>" in busy


# --- B3-F2 / B3-F9 / B3-F10 / B3-F6: the style rules the browser run asked for ------------------

def test_b3f2_the_time_box_looks_like_every_other_box(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    app_id = applied(live())
    html = page(kit.open_as(world, "hari"), f"/shift/hiring/candidates/{app_id}")
    rule = re.search(r"input\[type=time\] \{([^}]*)\}", html).group(1)
    for part in ("width: 100%", "min-height: 48px", "font-size: 1rem", "border: 1px solid var(--gray-400)",
                 "padding: 11px 14px"):
        assert part in rule, part
    assert "type=time" not in open("templates/shared/cfa_style.html", encoding="utf-8").read()   # the gap this fills


def test_b3f9_links_that_stand_alone_are_44px_tall(world):
    made = rk.one_review(world)
    operator = kit.open_as(world, "operator")
    post(kit.open_as(world, "lena"), f"/shift/reviews/mine/{made['review']}/reflection",
         {"r_reliable": "I was on time for every open.", "send": "1"})
    home = page(operator, "/shift/people")
    rule = re.search(r"\.sec-head \.more, \.chip, \.btnrow a\.textlink, table\.plain td a, a\.pp-go \{([^}]*)\}", home)
    assert rule and "min-height: 44px" in rule.group(1) and "inline-flex" in rule.group(1)
    assert '<a class="textlink pp-go" href="/shift/reviews#follow-up">See which</a>' in home
    for path in ("templates/people/home.html",):
        assert 'class="textlink" href="{{ reviews_url }}#follow-up"' not in open(path, encoding="utf-8").read()


def test_b3f10_words_in_a_people_table_stay_whole_and_wide_tables_scroll(world):
    html = page(kit.open_as(world, "operator"), "/shift/people/audit")
    assert ".pp-scroll table.plain th, .pp-scroll table.plain td { overflow-wrap: normal; }" in html
    assert '<div class="card table pp-scroll"' in html
    metrics = open("templates/people/hiring/metrics.html", encoding="utf-8").read()
    for table in re.finditer(r'<table class="plain"', metrics):
        before = metrics[:table.start()]
        card = before[before.rindex('<div class="card'):]
        assert card.startswith('<div class="card pp-scroll">'), card[:60]


def test_b3f6_a_long_standard_name_wraps_inside_its_save_button(world):
    made = rk.one_review(world)
    html = page(kit.open_as(world, "rae"), f"/shift/reviews/review/{made['review']}")
    assert ".rv-set .btn { white-space: normal; }" in html
