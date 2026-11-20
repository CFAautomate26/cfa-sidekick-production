"""People: one test (or a few) for each thing the three checks of the
finished section found after the review fixes (A = the attack on those
fixes, C = the compliance and documentation check, B = the run in a real
browser). Each is named for the finding it pins and failed before its fix.
What only a real browser shows is pinned by its cause: the header, the
attribute, the style rule or the markup. No real AI service is ever
called. Everyone here is invented."""

import json
import re
import threading
import time

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
import shift_app
import shift_db
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk
from tests.test_people_hiring_db import (ai_in_use, applied, clock, criterion, draft, live, live_with_ai,
                                         person, table)

page, post = rk.page, rk.post
UNLOCK = "/shift/people/unlock"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    made = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    careers.reset_throttles()
    return made


@pytest.fixture()
def public():
    """Someone who has never signed in to anything."""
    return cfa_app.app.test_client()


def text(resp) -> str:
    return resp.data.decode()


def flashed(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


# --- A-V1: the limit on wrong PINs holds when the guesses arrive together -------------------------

def slow_counting_verify(monkeypatch) -> list:
    """Stand in for the leader PIN check: as slow as the real hash, and it
    records every PIN that really reached it."""
    checked: list[str] = []
    real = shift_db.verify_leader

    def verify(name, pin):
        checked.append(pin)
        time.sleep(0.15)
        return real(name, pin)

    monkeypatch.setattr(shift_db, "verify_leader", verify)
    return checked


def burst(n: int, send) -> list:
    """Call send(i) from n threads released at the same moment."""
    gate, out = threading.Barrier(n), [None] * n

    def one(i):
        gate.wait()
        out[i] = send(i)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_va1_fifty_wrong_pins_at_the_lock_at_once_and_only_five_are_ever_checked(world, monkeypatch):
    signed_in = world["client"]
    kit.sign_in(signed_in, "rae")
    cookie = signed_in.get_cookie("session").value
    checked = slow_counting_verify(monkeypatch)

    def guess(i):
        client = cfa_app.app.test_client()
        client.set_cookie("session", cookie)
        client.post(UNLOCK, data={"unlock_code": f"{5000 + i}"})
        return flashed(client)[-1]

    said = burst(50, guess)
    assert len(checked) == people.UNLOCK_MAX_MISSES == 5
    assert said.count("Wrong PIN.") == 5
    assert said.count("Too many wrong PINs. Try again in 10 minutes.") == 45
    assert [r["action"] for r in people_db.audit_feed()].count("wrong PIN at the lock") == 5
    assert [r["action"] for r in people_db.audit_feed()].count("lock held after wrong PINs") == 1
    # held now: the right PIN is not even checked
    signed_in.post(UNLOCK, data={"unlock_code": world["pins"]["rae"]})
    assert len(checked) == 5 and signed_in.get("/shift/people").status_code == 302


def test_va1_the_right_pin_inside_a_burst_does_not_get_a_free_check(world, monkeypatch):
    signed_in = world["client"]
    kit.sign_in(signed_in, "rae")
    cookie = signed_in.get_cookie("session").value
    checked = slow_counting_verify(monkeypatch)
    pins = [f"{6000 + i}" for i in range(29)] + [world["pins"]["rae"]]

    def guess(i):
        client = cfa_app.app.test_client()
        client.set_cookie("session", cookie)
        return client.post(UNLOCK, data={"unlock_code": pins[i]}).headers["Location"]

    burst(30, guess)
    assert len(checked) <= 5                     # the right one is checked only if it was among the first five


def test_va1_the_count_is_in_the_people_store_not_in_this_process(world):
    client = world["client"]
    kit.sign_in(client, "lena")
    mine = f"l:{world['ids']['lena']}"
    for _ in range(5):
        client.post(UNLOCK, data={"unlock_code": "0000"})
    assert not hasattr(people, "_unlock_fails")                      # nothing a restart could clear
    with people_db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pin_tries")]
    assert len(rows) == 5 and {r["key"] for r in rows} == {mine}
    assert "0000" not in json.dumps(rows)                            # never the PIN itself
    assert "pin_tries" not in json.loads(people_db.export_json("Operator"))["tables"]
    # another server process (same store) refuses the sixth without checking it
    assert people_db.pin_try(mine, people._now(), people.UNLOCK_MAX_MISSES, people.UNLOCK_HOLD_SECONDS) == 0
    # ten minutes later the count has run out
    later = people._now() + people.UNLOCK_HOLD_SECONDS + 1
    assert people_db.pin_try(mine, later, people.UNLOCK_MAX_MISSES, people.UNLOCK_HOLD_SECONDS) == 1


def test_va1_a_request_that_read_the_clock_a_second_early_is_still_refused(world):
    now = people._now()
    for nth in range(1, 6):
        assert people_db.pin_try("op", now + 1, 5, 600) == nth
    assert people_db.pin_try("op", now, 5, 600) == 0      # it sees the five counted "after" it
    assert people_db.pin_try("l:7", now, 5, 600) == 1     # someone else's count is their own


def test_va1_fifty_wrong_pins_at_the_sign_in_form_at_once_and_only_five_are_checked(world, monkeypatch):
    checked = slow_counting_verify(monkeypatch)

    def guess(i):
        resp = cfa_app.app.test_client().post("/shift/login", data={"name": "Rae", "pin": f"{7000 + i}"})
        return ("held" if b"Too many wrong PINs" in resp.data else "wrong", resp.status_code)

    said = burst(50, guess)
    assert len(checked) == 5
    assert said.count(("wrong", 401)) == 5 and said.count(("held", 401)) == 45
    # and the Operator's PIN, which is a fast compare, the same way
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    tried = burst(40, lambda i: cfa_app.app.test_client().post(
        "/shift/login", data={"name": "Operator", "pin": f"{8000 + i}"}).data)
    assert sum(b"Wrong PIN." in page_ for page_ in tried) == 5
    assert sum(b"Too many wrong PINs" in page_ for page_ in tried) == 35


def test_va1_sign_in_still_clears_the_count_on_the_right_pin(world):
    client = cfa_app.app.test_client()
    for _ in range(4):
        assert client.post("/shift/login", data={"name": "Rae", "pin": "0000"}).status_code == 401
    assert len(shift_app._pin_fails["rae"]) == 4
    assert client.post("/shift/login", data={"name": "Rae", "pin": world["pins"]["rae"]}).status_code == 302
    assert "rae" not in shift_app._pin_fails


# --- A-V2 / C-V5: everyday health, family and time-away words are held back from the AI ----------

def reviews_ai_on():
    assert people_db.set_setting("ai_reviews", "1", "Operator")


HELD_BACK = (
    "Uses a wheelchair on the floor and still ran the front counter.",
    "Missed two closes for chemo; her cancer treatment is on Thursdays.",
    "Forgot her pills and had a seizure scare at the fryer.",
    "Out a week with covid.",
    "Her mom is in the ER so she left the close to Dev.",
    "Away three days for her grandmother's funeral.",
    "She is expecting in March.",
    "Hard of hearing on the headset.",
    "Left at two for a physio appointment.",
    "Elle était malade mardi.",
    "Missed two shifts in October and looked worn down on the floor.",
    "Was away for three weeks and handed nothing over.",
    "Called off three times this month.",
    "Has been off since the accident.",
    "Slower since coming back.",
    "Can't lift the fry baskets anymore.",
    "Asked about her pay and overtime.",
    "Hurt her back in the walk-in.",
    "Was unwell and went home.",
    "Needs Fridays off.",
)
STILL_SENT = (
    "Put away the stock without being asked.",
    "Missed the opening checklist twice this week.",
    "Ran the Mother's Day rush without a hitch.",
    "Was expecting a rush and staffed the line for it.",
    "The operation ran smoothly on Saturday.",
    "Counted the till and was off by two dollars.",
    "Was off the pace at lunch, then caught up.",
    "Deals with a miss the same shift, in private.",
)


def test_va2_everyday_health_family_and_time_away_notes_are_not_sent_to_the_ai(world):
    made = rk.one_review(world)
    for body in HELD_BACK + STILL_SENT:
        rk.observe(made["subject"], body, -5, "reliable")
    payload = people_reviews.ai_payload(rdb.get_review(made["review"]), people_db.active_model())
    sent = [e["text"] for e in payload["evidence"]]
    assert payload["withheld"]["notes"] == len(HELD_BACK), sent
    assert len(sent) == len(STILL_SENT)                               # ordinary work notes still go
    for word in ("wheelchair", "chemo", "seizure", "covid", "funeral", "in March", "hearing", "physio",
                 "malade", "shifts in October", "three weeks", "Called off", "accident", "coming back",
                 "fry baskets", "overtime", "walk-in", "unwell", "Fridays"):
        assert word not in " ".join(sent), word


def test_va2_a_reflection_box_about_treatment_days_is_not_sent_either(world):
    made = rk.one_review(world)
    rk.observe(made["subject"], "On time and ready.", -5, "reliable")
    assert rdb.save_reflection(made["review"], {
        "reliable": "My chemo days are Thursdays so I am slower then.",
        "teamwork": "I trained two new starters on the register."}, True, "Lena") is None
    payload = people_reviews.ai_payload(rdb.get_review(made["review"]), people_db.active_model())
    assert payload["withheld"]["reflection"] == 1
    assert [r["dimension_key"] for r in payload["reflection"]] == ["teamwork"]


def test_va2_a_note_that_may_be_personal_never_grounds_a_suggested_level(world):
    """The second line: a note that got through the word list but uses an
    everyday word that may be about health, family or time away."""
    made = rk.one_review(world)
    plain = rk.observe(made["subject"], "On time and ready for every open this week.", -6, "reliable")
    also = rk.observe(made["subject"], "Counted the till before the handover.", -5, "reliable")
    maybe = rk.observe(made["subject"], "Walked away from the line twice and looked exhausted.", -4, "teamwork")
    payload = people_reviews.ai_payload(rdb.get_review(made["review"]), people_db.active_model())
    assert payload["withheld"]["notes"] == 0 and len(payload["evidence"]) == 3      # all three were sent
    reply = {"dimensions": {
        "reliable": {"evidence_summary": "On time for opens.", "cited": [plain, also], "suggested_level": 3,
                     "anchor_used": "", "note": ""},
        "teamwork": {"evidence_summary": "Left the line twice.", "cited": [maybe], "suggested_level": 1,
                     "anchor_used": "", "note": ""}},
        "strengths": [], "growth_areas": [], "coaching_questions": [], "human_checks": []}
    kept = people_reviews._ai_validate(reply, payload)["dimensions"]
    assert kept["reliable"]["suggested_level"] == 3
    assert kept["teamwork"]["suggested_level"] is None and kept["teamwork"]["withheld"] is True
    line = next(c for c in kept["teamwork"]["checks"] if c.startswith("No level is suggested here: a note"))
    assert "may be about health, family or time away (“exhausted”, “away”)" in line
    assert "must never count in a review" in line


def test_va2_the_pages_say_the_check_is_a_word_list_and_what_it_cannot_catch(world, monkeypatch):
    reviews_ai_on()
    kit.fake_ai(monkeypatch, rk.draft_reply)
    made = rk.one_review(world)
    rk.observe(made["subject"], "On time.", -5, "reliable")
    html = page(kit.open_as(world, "rae"), f"/shift/reviews/review/{made['review']}")
    assert "a plain word list looks for wording about health, family, age" in html
    assert "A word list cannot catch every way of saying these things" in html
    assert "leave such notes out of the review yourself" in html
    assert "is not sent at all" not in html                           # the flat promise is gone
    mine = page(kit.open_as(world, "lena"), f"/shift/reviews/mine/{made['review']}")
    assert "The list cannot catch every way of saying these things" in mine
    assert "is not given to it." not in mine
    settings = page(kit.open_as(world, "operator"), "/shift/people/settings")
    assert "A word list cannot catch every way of saying these things" in settings


def test_vc5_a_to_work_on_note_that_reads_like_time_away_gets_the_time_away_prompt(world):
    subject = rk.add_subject(world)
    rae = kit.open_as(world, "rae")
    html = post(rae, f"/shift/reviews/leaders/{subject}/observe", {
        "obs_date": rk.day(-1), "kind": "growth", "body": "Missed two shifts in October."})
    assert html.count(people_reviews.TIME_AWAY_PROMPT) == 2           # the flash, and above the note
    assert "mark the dates as time away on the review instead" in people_reviews.TIME_AWAY_PROMPT
    assert "“missed two shifts” (Leave and complaints)" in html
    # a "did well" note with such a word is flagged as before, without the time-away line
    html = post(rae, f"/shift/reviews/leaders/{subject}/observe", {
        "obs_date": rk.day(-1), "kind": "strength", "body": "Covered for a teammate who was away sick."})
    assert html.count(people_reviews.TIME_AWAY_PROMPT) == 1 and "“was away”" in html


# --- C-V1: a question on a protected ground cannot be published --------------------------------

ASKS_ABOUT_A_GROUND = (
    "Are you single, and do you live with your parents?",
    "What country are you from, and what language do you speak at home?",
    "Do you have any physical limitations or injuries we should know about?",
    "What grade are you in, and are you over 16?",
    "Have you ever been in trouble with the police?",
    "Have you ever collected workers' compensation or been off work hurt?",
    "Any health conditions we should know about?",
    "How many children do you have?",
    "Are you Christian?",
    "Are you male or female, and are you gay?",
    "What are your pronouns?",
    "Do you have a boyfriend or girlfriend who works here?",
    "DOB?",
    "How tall are you, and how much do you weigh?",
    "Emergency contact name and relationship?",
    "Why can't you work weekends?",
    "Are you a member of a union?",
    "Do you have a pardon?",
)
ABOUT_THE_WORK = (
    "Describe a time you helped a guest with a disability.",
    "Tell us about a time you kept calm in a medical emergency.",
    "What would you do if a guest asked for a photo with the mascot?",
    "Tell us about a time you were charged with closing the store.",
    "How would you look after a family with young kids at a table?",
)


@pytest.mark.parametrize("question", ASKS_ABOUT_A_GROUND)
def test_vc1_a_plain_question_on_a_ground_fails_the_publish_check_tick_or_no_tick(world, question):
    for field in ("question", "interview_question"):
        posting_id = draft()
        criterion(posting_id, "Reliable", **({"question": question} if field == "question"
                                             else {"interview_question": question}))
        criterion(posting_id, "Guest care")
        problems = hdb.compliance_problems(hdb.get_posting(posting_id))
        assert len(problems) == 1 and "may not ask that" in problems[0], (field, question)
        assert hdb.publish(posting_id, "Operator", reviewed=True) == problems
        assert hdb.get_posting(posting_id)["status"] == "draft"


def test_vc1_an_honest_question_is_held_until_the_operator_confirms_that_exact_wording(world):
    posting_id = draft()
    criterion(posting_id, "Guest care", question=ABOUT_THE_WORK[0], interview_question=ABOUT_THE_WORK[1])
    criterion(posting_id, "Reliable")
    for honest in ABOUT_THE_WORK:
        assert hdb.question_hits(honest), honest                      # every one is held, none is hard-blocked
    crit = hdb.get_posting(posting_id)["criteria"][0]
    blocks = hdb.question_blocks(hdb.get_posting(posting_id))
    assert [(b["which"], b["term"]) for b in blocks] == [("question", "disability"), ("interview", "medical")]
    assert "the Operator can confirm that here, under the publish check" in blocks[0]["problem"]
    # a hiring leader sees why, and cannot confirm it
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    html = page(hari, f"/shift/hiring/postings/{posting_id}")
    assert "Questions held back" in html and "Only the Operator can confirm a question." in html
    assert "Confirm this question" not in html
    confirm = f"/shift/hiring/postings/{posting_id}/criteria/{crit['id']}/question-ok"
    assert rk.NOT_YOURS in post(hari, confirm, {"which": "question", "confirm": "1"})
    assert len(hdb.question_blocks(hdb.get_posting(posting_id))) == 2
    # the Operator needs the tick, one question at a time, and each is logged
    assert "Tick the box to confirm" in post(operator, confirm, {"which": "question"})
    assert len(hdb.question_blocks(hdb.get_posting(posting_id))) == 2
    assert hdb.QUESTION_OK_LABEL in page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "Confirmed. If the question is changed, it is checked again." in post(
        operator, confirm, {"which": "question", "confirm": "1"})
    assert [b["which"] for b in hdb.question_blocks(hdb.get_posting(posting_id))] == ["interview"]
    assert hdb.publish(posting_id, "Operator", reviewed=True) != []                 # one is still held
    post(operator, confirm, {"which": "interview", "confirm": "1"})
    assert hdb.compliance_problems(hdb.get_posting(posting_id)) == []
    logged = [(r["who"], r["object_id"], r["detail"]) for r in people_db.audit_feed()
              if r["action"] == "question confirmed as about the work"]
    assert logged == [("Operator", posting_id, f"criterion {crit['id']}, interview question"),
                      ("Operator", posting_id, f"criterion {crit['id']}, question for applicants")]
    assert "There is nothing to confirm" in post(operator, confirm, {"which": "question", "confirm": "1"})
    # changing the question voids the confirmation: the new wording is checked again
    changed = {**crit, "question": "Do you have a disability?"}
    assert hdb.save_criterion(crit["id"], posting_id, changed, "Hari") == []
    assert [b["which"] for b in hdb.question_blocks(hdb.get_posting(posting_id))] == ["question"]
    # and back to the confirmed wording does not bring the confirmation back by itself
    assert hdb.save_criterion(crit["id"], posting_id, crit, "Hari") == []
    assert [b["which"] for b in hdb.question_blocks(hdb.get_posting(posting_id))] == ["question"]


def test_vc1_on_a_live_posting_only_the_operators_tick_lets_such_a_question_in(world):
    posting_id = live()
    crit = hdb.get_posting(posting_id)["criteria"][0]
    form = {"name": crit["name"], "definition": crit["definition"], "question": ABOUT_THE_WORK[0],
            "interview_question": "", "wording_reviewed": "1", "questions_ok": "1",
            **{f"anchor{n}": a for n, a in enumerate(crit["anchors"], 1)}}
    save = f"/shift/hiring/postings/{posting_id}/criteria/{crit['id']}"
    hari, operator = kit.open_as(world, "hari"), kit.open_as(world, "operator")
    assert "This posting is live, so it has to keep passing the publish check." in post(hari, save, form)
    assert hdb.get_posting(posting_id)["criteria"][0]["question"] == crit["question"]   # a leader's tick is not enough
    assert "Criterion saved." in post(operator, save, form)
    saved = hdb.get_posting(posting_id)["criteria"][0]
    assert saved["question"] == saved["question_ok"] == ABOUT_THE_WORK[0]
    assert hdb.compliance_problems(hdb.get_posting(posting_id)) == []
    assert any(r["action"] == "question confirmed as about the work" and r["object_id"] == posting_id
               for r in people_db.audit_feed())
    # a question that is plainly about the applicant still needs the same deliberate tick: without it, refused
    assert "may not ask that" in post(operator, save, {**form, "question": "Are you pregnant?", "questions_ok": ""})


def test_vc1_a_copy_starts_without_the_confirmations_and_an_older_store_gains_the_columns(world):
    posting_id = draft()
    criterion(posting_id, "Guest care", question=ABOUT_THE_WORK[0])
    criterion(posting_id, "Reliable")
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.confirm_question(posting_id, crit["id"], "question", "Operator")
    copy = hdb.copy_posting(posting_id, "Hari")
    assert len(hdb.question_blocks(hdb.get_posting(copy))) == 1       # the Operator confirms it again there
    with people_db.connect() as conn:
        conn.execute("ALTER TABLE hr_criteria DROP COLUMN question_ok")
        conn.execute("ALTER TABLE hr_criteria DROP COLUMN interview_ok")
        conn.commit()
        hdb.init_schema(conn)
        assert {"question_ok", "interview_ok"} <= {r[1] for r in conn.execute("PRAGMA table_info(hr_criteria)")}


def test_vc1_the_documents_say_the_check_catches_listed_words_only():
    guide = " ".join(open("docs/people.md", encoding="utf-8").read().split())
    legal = " ".join(open("docs/legal-counsel/hiring-and-leader-reviews.md", encoding="utf-8").read().split())
    claude = " ".join(open("CLAUDE.md", encoding="utf-8").read().split())
    assert "catches listed words only" in guide and "catches the plain cases" not in guide
    assert "listed words only" in legal
    assert "a posting whose own question plainly asks about one cannot be published" not in legal
    assert "no question that asks about a prohibited ground" not in claude


# --- C-V2: a concern in a reflection, or in any comment, reaches the Operator -------------------

REFLECTION_CONCERN = "Rae has been harassing me about my schedule and I feel unsafe."


def test_vc2_a_concern_in_a_sent_reflection_is_put_in_front_of_the_operator(world):
    made = rk.one_review(world)
    rid = made["review"]
    lena, operator, rae = kit.open_as(world, "lena"), kit.open_as(world, "operator"), kit.open_as(world, "rae")
    # saved but not sent: it is the leader's alone, and nobody is told anything
    post(lena, f"/shift/reviews/mine/{rid}/reflection", {"r_reliable": REFLECTION_CONCERN})
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 0}
    html = post(lena, f"/shift/reviews/mine/{rid}/reflection", {"r_reliable": REFLECTION_CONCERN, "send": "1"})
    assert "Sent to your reviewer." in html and "concern" not in html.split("<main", 1)[-1].split("How this works")[0]
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 1, "comments": 0}
    assert people_reviews.extra_counts("leader", world["ids"]["rae"], "Rae") == {}
    home = page(operator, "/shift/reviews")
    listed = home[home.index('id="follow-up"'):home.index('id="cycles"')]
    assert f'href="/shift/reviews/review/{rid}#reflection"' in listed
    assert "The leader's reflection may raise a concern that needs follow-up." in listed
    assert "1 leader&#39;s comments or reflection may raise a concern" in page(operator, "/shift/people")
    assert "1 to follow up" in page(operator, "/shift/more")
    # the flag is the Operator's: the reviewer (who it may be about) reads the reflection without it
    theirs = page(rae, f"/shift/reviews/review/{rid}")
    assert REFLECTION_CONCERN in theirs and "may raise a concern" not in theirs
    assert 'id="follow-up"' not in page(rae, "/shift/reviews")
    mine = page(operator, f"/shift/reviews/review/{rid}")
    assert "The leader&#39;s reflection may raise a concern that needs follow-up." in mine
    assert "about you, their reviewer or another senior leader" in mine
    # only the Operator records the follow-up, with a tick; only that it happened is kept
    follow = f"/shift/reviews/review/{rid}/concern-followed"
    assert rk.NOT_YOURS in post(rae, follow, {"what": "reflection", "confirm": "1"})
    assert "Tick the box" in post(operator, follow, {"what": "reflection"})
    assert "Recorded as followed up." in post(operator, follow, {"what": "reflection", "confirm": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 0}
    assert "A concern in this reflection was recorded as followed up outside this tool by" in page(
        operator, f"/shift/reviews/review/{rid}")
    row = next(r for r in people_db.audit_feed() if r["action"] == "reflection followed up outside this tool")
    assert (row["who"], row["object_id"], row["detail"]) == ("Operator", rid, "")
    assert REFLECTION_CONCERN not in json.dumps(people_db.audit_feed())
    # the leader adds to it afterwards: it is back in front of the Operator
    post(lena, f"/shift/reviews/mine/{rid}/reflection",
         {"r_reliable": REFLECTION_CONCERN + " It happened again on Friday.", "send": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 1, "comments": 0}


def test_vc2_an_ordinary_reflection_is_not_flagged_and_the_page_gives_a_private_route(world):
    made = rk.one_review(world)
    lena = kit.open_as(world, "lena")
    form = page(lena, f"/shift/reviews/mine/{made['review']}")
    assert "<strong>Your reviewer reads this.</strong> To raise a concern about how you are treated, or " \
           "about your reviewer, speak to the Operator directly." in form
    post(lena, f"/shift/reviews/mine/{made['review']}/reflection",
         {"r_reliable": "I was on time for every open and trained two new starters.", "send": "1"})
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 0}
    assert "may raise a concern" not in page(kit.open_as(world, "operator"),
                                             f"/shift/reviews/review/{made['review']}")


@pytest.mark.parametrize("said", [
    "Sexual comments from my reviewer.", "I don't feel safe closing with him.",
    "He yelled at me in front of guests.", "I'm scared of him.", "He intimidates me.",
    "I will be contacting a lawyer.", "I'm going to the labour board.",
    "I was treated unfairly because of my religion.", "He makes comments about my body.",
])
def test_vc2_plain_complaints_are_on_the_concern_list(said):
    assert people_ai.concern_terms(said), said


def test_vc2_a_complaint_in_words_on_no_list_still_waits_for_the_operator(world):
    """The acknowledgement does not rest on a word list at all: every
    comment is listed until the Operator records that they read it."""
    made = rk.shared_review(world)
    said = "Things between me and my reviewer are not okay and I would like to talk to you alone."
    assert people_ai.concern_terms(said) == []
    assert rdb.acknowledge(made["review"], rk.day(0), said, "Lena") is None
    assert people_reviews.extra_counts("operator", None, "Operator") == {"concerns": 0, "comments": 1}
    operator = kit.open_as(world, "operator")
    assert f'/review/{made["review"]}#leader-comments' in page(operator, "/shift/reviews")
    html = page(operator, f"/shift/reviews/review/{made['review']}")
    assert "a word list cannot tell whether comments need follow-up" in html
    assert "I have read these comments, and anything they raise was followed up outside this tool." in html
    assert "1 comment to read" in page(operator, "/shift/more")
    # the reviewer reads the comments without any of that
    theirs = page(kit.open_as(world, "rae"), f"/shift/reviews/review/{made['review']}")
    assert said in theirs and "I have read these comments" not in theirs


# --- B1: the two AI waiting pages reload in a real browser --------------------------------------

def refresh_target(html: str) -> str:
    found = re.search(r'<meta http-equiv="refresh" content="(\d+); url=([^"]*)">', html)
    assert found, "no refresh tag with an explicit address"
    return found.group(2)


def test_vb1_the_review_page_refreshes_to_an_address_with_no_fragment_while_drafting(world, monkeypatch):
    reviews_ai_on()
    kit.fake_ai(monkeypatch, rk.draft_reply)
    made = rk.one_review(world)
    rk.observe(made["subject"], "On time.", -5, "reliable")
    rk.queue_job(made["review"], "working")
    rae = kit.open_as(world, "rae")
    # asking for a draft lands on the page with #ai, which is why the refresh may not carry one
    asked = rae.post(f"/shift/reviews/review/{made['review']}/ai")
    assert asked.headers["Location"].endswith(f"/shift/reviews/review/{made['review']}#ai")
    html = page(rae, f"/shift/reviews/review/{made['review']}")
    target = refresh_target(html)
    assert target == f"/shift/reviews/review/{made['review']}" and "#" not in target
    assert f'<a href="/shift/reviews/review/{made["review"]}">check now</a>' in html


def test_vb1_the_candidate_page_refreshes_to_an_address_with_no_fragment_while_reading(world, monkeypatch):
    posting_id = live_with_ai(monkeypatch)
    kit.fake_ai(monkeypatch, {"criteria": [], "human_checks": []})
    application_id = applied(posting_id, ai="1")
    with people_db.connect() as conn:
        conn.execute("UPDATE hr_applications SET ai_disclosed=1 WHERE id=?", (application_id,))
        conn.commit()
    asked = {c["id"]: "I stayed late to finish the count when a teammate was away."
             for c in hdb.get_posting(posting_id)["criteria"]}
    assert hdb.save_answers(application_id, asked) == "saved"
    with people_db.connect() as conn:
        conn.execute("INSERT INTO ai_jobs (kind, target_id, status, requested_by, requested_at) "
                     "VALUES ('hiring_score', ?, 'working', 'Hari', ?)", (application_id, shift_db.now_stamp()))
        conn.commit()
    html = page(kit.open_as(world, "hari"), f"/shift/hiring/candidates/{application_id}")
    assert "The AI is reading the answers." in html
    target = refresh_target(html)
    assert target == f"/shift/hiring/candidates/{application_id}" and "#" not in target
    assert f'<a href="/shift/hiring/candidates/{application_id}">check now</a>' in html


# --- B2: the calibration table cannot stretch the page sideways ---------------------------------

def test_vb2_the_table_scroller_contains_its_screen_reader_spans(world):
    made = rk.one_review(world)
    rk.rate_all(made["review"])
    assert rk.finalise(made["review"]) == "final"
    html = page(kit.open_as(world, "operator"), f"/shift/reviews/calibration/{made['cycle']}")
    rule = re.search(r"\.pp-scroll\s*\{([^}]*)\}", html)
    assert rule and "position: relative" in rule.group(1) and "overflow-x: auto" in rule.group(1)
    # the wide table and its absolutely positioned spans sit inside that scroller
    table_html = html[html.index('<div class="card table pp-scroll">'):html.index('id="reviewers"')]
    assert '<table class="plain rv-cal">' in table_html and '<span class="sr">' in table_html
    assert re.search(r"\.sr\s*\{[^}]*position: absolute", html)


# --- B3: a draft's public address follows its title until it has been public --------------------

def test_vb3_a_corrected_title_never_goes_live_under_its_first_wording(world, public):
    posting_id = draft(title="Young energetic counter girl")
    assert hdb.get_posting(posting_id)["slug"] == "young-energetic-counter-girl"
    # the address itself is read by the wording check while it is a draft
    assert {"young", "energetic", "girl"} <= {f["term"] for f in hdb.wording_flags(hdb.get_posting(posting_id))}
    fields = {"title": "Team member", "employment_type": "part-time", "description": hdb.get_posting(
        posting_id)["description"], "pay_min": "17.95", "pay_max": "19.50", "vacancy": "existing"}
    assert hdb.save_posting(posting_id, fields, "Hari") == []
    assert hdb.get_posting(posting_id)["slug"] == "team-member"
    assert hdb.wording_flags(hdb.get_posting(posting_id)) == []
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    operator = kit.open_as(world, "operator")
    html = page(operator, f"/shift/hiring/postings/{posting_id}")
    assert "Its public address will be" in html and "/careers/team-member</span>" in html
    assert "counter-girl" not in html
    assert hdb.publish(posting_id, "Operator") == []
    assert public.get("/careers/team-member").status_code == 200
    assert public.get("/careers/young-energetic-counter-girl").status_code == 404
    assert "young-energetic" not in public.get("/careers/").data.decode()
    # once it has been public the address is frozen: links to it are already out
    assert hdb.save_posting(posting_id, {**fields, "title": "Front counter team member"}, "Hari") == []
    assert hdb.get_posting(posting_id)["slug"] == "team-member"


def test_vb3_an_old_draft_with_a_stale_address_is_put_right_when_it_is_published(world):
    posting_id = draft(title="Team member")
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    with people_db.connect() as conn:                                 # as a draft saved before this fix
        conn.execute("UPDATE hr_postings SET slug='young-energetic-counter-girl' WHERE id=?", (posting_id,))
        conn.commit()
    assert hdb.publish(posting_id, "Operator", reviewed=True) == []
    assert hdb.get_posting(posting_id)["slug"] == "team-member"
    second = draft(title="Team member")                               # a second posting of the same job
    assert hdb.get_posting(second)["slug"] == "team-member-2"
    assert hdb.save_posting(second, {"title": "Team member", "employment_type": "part-time", "description": "x",
                                     "pay_min": "", "pay_max": "", "vacancy": ""}, "Hari") == []
    assert hdb.get_posting(second)["slug"] == "team-member-2"       # an unchanged title keeps its address
