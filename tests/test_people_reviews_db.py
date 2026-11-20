"""Leader reviews, the data layer: who is under review, the observation
log, cycles, and every step a review can take. Everyone here is invented."""

import copy
import json

import pytest

import people_db
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return kit.make_world(monkeypatch, tmp_path)


# --- the store ------------------------------------------------------------------

def test_review_tables_live_in_the_people_file_and_its_backup_only(world):
    made = rk.one_review(world)
    rk.observe(made["subject"], "Opened on time and had the line ready.")
    backup = json.loads(people_db.export_json("Operator"))["tables"]
    for table in ("rv_subjects", "rv_observations", "rv_cycles", "rv_reviews", "rv_ratings",
                  "rv_actions", "rv_reflections"):
        assert table in backup, table
        assert table not in shift_db.EXPORT_TABLES
    assert backup["rv_observations"][0]["body"].startswith("Opened on time")
    assert "Opened on time" not in shift_db.export_json()
    people_db.init_db()                                   # set-up is repeatable
    assert len(rdb.subjects()) == 1


# --- leaders under review -------------------------------------------------------

def test_a_leader_is_added_once_and_never_reviews_themselves(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae", title="Team leader", area="Front of house")
    row = rdb.get_subject(lena)
    assert (row["display_name"], row["leader_id"], row["leader_name"]) == ("Lena", ids["lena"], "Lena")
    assert (row["reviewer_leader_id"], row["reviewer_name"], row["active"]) == (ids["rae"], "Rae", 1)
    # the same sign-in, or the same name, is not added twice
    assert rdb.add_subject("Lena B", "Operator", leader_id=ids["lena"], leader_name="Lena") \
        == (None, "Lena is already on the list.")
    assert rdb.add_subject("lena", "Operator")[1] == "Lena is already on the list."
    assert rdb.add_subject("  ", "Operator")[1] == "Give the leader a name."
    # nobody reviews themselves: by id, by sign-in name, or by the name on the review
    for args in (dict(leader_id=ids["rae"], leader_name="Rae"), dict()):
        got = rdb.add_subject("Rae", "Operator", reviewer_leader_id=ids["rae"], reviewer_name="Rae", **args)
        assert got == (None, "A leader can't review themselves. Choose someone else.")
    assert rdb.subjects() == [rdb.get_subject(lena)]
    # half a link is no link
    by_name, problem = rdb.add_subject("Jo Example", "Operator", leader_id=ids["ada"])
    assert problem is None and rdb.get_subject(by_name)["leader_id"] is None
    assert rdb.get_subject(by_name)["reviewer_leader_id"] is None      # the Operator reviews


def test_changing_the_reviewer_moves_unfinished_reviews_only(world):
    ids = world["ids"]
    assert people_db.grant(ids["ada"], "Ada", "reviewer", "Operator")
    made = rk.one_review(world)
    done = rk.add_cycle("Summer", start=-200, end=-100, due=-90)       # a second cycle, finished
    finished = rk.review_of(done, made["subject"])
    rk.rate_all(finished)
    assert rk.finalise(finished) == "final"
    problem = rdb.update_subject(made["subject"], "Operator", display_name="Lena", leader_id=ids["lena"],
                                 leader_name="Lena", title="Director", area="Kitchen",
                                 reviewer_leader_id=ids["ada"], reviewer_name="Ada", active=True)
    assert problem is None
    assert rdb.get_subject(made["subject"])["title"] == "Director"
    assert rdb.get_review(made["review"])["reviewer_name"] == "Ada"
    assert rdb.get_review(finished)["reviewer_name"] == "Rae"          # keeps who wrote it
    # the edit is checked like an add
    assert rdb.update_subject(made["subject"], "Operator", display_name="Lena", leader_id=ids["lena"],
                              leader_name="Lena", reviewer_leader_id=ids["lena"], reviewer_name="Lena") \
        == "A leader can't review themselves. Choose someone else."
    assert rdb.update_subject(999, "Operator", display_name="Nobody") \
        == "That leader isn't on the list any more."


# --- observations ---------------------------------------------------------------

def test_an_observation_needs_a_real_past_date_and_some_words(world):
    subject = rk.add_subject(world)
    assert rdb.add_observation(subject, "", "reliable", "note", "Text", "Rae")[1] == "Choose the date you saw it."
    assert rdb.add_observation(subject, "2026-02-31", None, "note", "Text", "Rae")[1] == "Choose the date you saw it."
    assert rdb.add_observation(subject, rk.day(1), None, "note", "Text", "Rae")[1] == "That date hasn't happened yet."
    assert "too far back" in rdb.add_observation(subject, rk.day(-800), None, "note", "Text", "Rae")[1]
    assert rdb.add_observation(subject, rk.day(), None, "note", "  \n ", "Rae")[1] \
        == "Write what you saw or heard first."
    assert rdb.add_observation(999, rk.day(), None, "note", "Text", "Rae")[1] \
        == "That leader isn't under review right now."
    obs_id, problem = rdb.add_observation(subject, rk.day(-1), "Not A Key!", "odd", "x" * 5000, "Rae",
                                          world["ids"]["rae"])
    obs = rdb.get_observation(obs_id)
    assert problem is None and obs["dimension_key"] is None and obs["kind"] == "note"
    assert len(obs["body"]) == rdb.CAPS["observation"] and obs["in_review"] == 1
    assert (obs["author_name"], obs["author_leader_id"]) == ("Rae", world["ids"]["rae"])


def test_leave_out_and_remove(world):
    subject = rk.add_subject(world)
    first = rk.observe(subject, "Ran the lunch rush calmly.", -5)
    second = rk.observe(subject, "Left the closing list unfinished.", -2)
    assert [o["id"] for o in rdb.observations(subject)] == [second, first]      # newest first
    assert rdb.toggle_observation(first, "Rae") is False
    assert rdb.get_observation(first)["in_review"] == 0
    assert rdb.toggle_observation(first, "Rae") is True
    assert rdb.remove_observation(second, "Rae") is True
    assert rdb.remove_observation(second, "Rae") is False                       # already gone
    assert rdb.toggle_observation(second, "Rae") is None
    assert [o["id"] for o in rdb.observations(subject)] == [first]
    gone = rdb.observations(subject, removed=True)
    assert [(o["id"], o["removed_by"]) for o in gone] == [(second, "Rae")] and gone[0]["body"]
    assert rdb.toggle_observation(999, "Rae") is None


# --- cycles ---------------------------------------------------------------------

def test_a_cycle_needs_dates_standards_and_leaders(world):
    args = ("Autumn", rk.day(-30), rk.day(), rk.day(7), True, "Operator")
    assert rdb.create_cycle(*args)[1] == "Add the leaders you review first."
    rk.add_subject(world)
    assert rdb.create_cycle("", *args[1:])[1] == "Give the cycle a name."
    assert "start date" in rdb.create_cycle("A", "", rk.day(), rk.day(7), True, "Operator")[1]
    assert rdb.create_cycle("A", rk.day(), rk.day(-1), rk.day(7), True, "Operator")[1] \
        == "The period can't end before it starts."
    assert "two years" in rdb.create_cycle("A", rk.day(-900), rk.day(), rk.day(7), True, "Operator")[1]
    assert "due date" in rdb.create_cycle("A", rk.day(-30), rk.day(), rk.day(-40), True, "Operator")[1]
    with people_db.connect() as conn:
        conn.execute("UPDATE standards_models SET active=0")
        conn.commit()
    assert rdb.create_cycle(*args)[1].startswith("Load Our standards first.")
    assert rdb.cycles() == []


def test_a_cycle_makes_a_draft_for_every_active_leader(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae")
    ada = rk.add_subject(world, "ada", None)
    hari = rk.add_subject(world, "hari", "rae")
    assert rdb.update_subject(hari, "Operator", display_name="Hari", leader_id=ids["hari"],
                              leader_name="Hari", active=False) is None
    cycle_id = rk.add_cycle()
    reviews = rdb.cycle_reviews(cycle_id)
    assert [(r["display_name"], r["status"], r["reviewer_name"], r["model_id"]) for r in reviews] == [
        ("Ada", "draft", None, ids["model"]), ("Lena", "draft", "Rae", ids["model"])]
    assert all(r["editable"] and not r["done"] for r in reviews)
    cycle = rdb.cycles()[0]
    assert cycle["total"] == 2 and cycle["counts"]["draft"] == 2 and cycle["status"] == "open"
    # a leader added later joins on the standards the cycle started with
    newer, problems = people_db.save_model(copy.deepcopy(people_db.PEOPLE_EXAMPLE_MODEL), "Operator")
    assert not problems and newer != ids["model"]
    assert rdb.update_subject(hari, "Operator", display_name="Hari", leader_id=ids["hari"],
                              leader_name="Hari", active=True) is None
    review_id, problem = rdb.add_review(cycle_id, hari, "Operator")
    assert problem is None and rdb.get_review(review_id)["model_id"] == ids["model"]
    assert rdb.add_review(cycle_id, hari, "Operator")[1] == "Hari already has a review in this cycle."
    # closing stops the writing
    assert rdb.close_cycle(cycle_id, "Operator") is True
    assert rdb.close_cycle(cycle_id, "Operator") is False
    assert rdb.get_review(rk.review_of(cycle_id, lena))["editable"] is False
    assert rdb.save_rating(rk.review_of(cycle_id, lena), "reliable", 3, False, "", "Rae") is False
    assert rdb.add_review(cycle_id, ada, "Operator")[0] is None


# --- writing a review -------------------------------------------------------------

def test_a_rating_is_a_level_or_not_enough_and_only_in_a_draft(world):
    made = rk.one_review(world)
    rid = made["review"]
    assert rdb.save_rating(rid, "reliable", 3, False, "On time every shift I saw.", "Rae") is True
    assert rdb.save_rating(rid, "teamwork", None, True, "", "Rae") is True
    rated = rdb.ratings(rid)
    assert (rated["reliable"]["level"], rated["reliable"]["not_enough"]) == (3, 0)
    assert (rated["teamwork"]["level"], rated["teamwork"]["not_enough"]) == (None, 1)
    assert rated["reliable"]["rated_by"] == "Rae" and rated["reliable"]["rated_at"]
    # "not enough" wins over a level sent with it; a second save replaces the first
    assert rdb.save_rating(rid, "reliable", 4, True, "", "Rae") is True
    assert rdb.ratings(rid)["reliable"]["level"] is None
    for bad in (0, 5, "", None, "three", True):
        assert rdb.save_rating(rid, "guest-care", bad, False, "", "Rae") is False
    assert rdb.save_rating(rid, "no-such-standard", 2, False, "", "Rae") is False
    assert rdb.save_rating(999, "reliable", 2, False, "", "Rae") is False
    assert "guest-care" not in rdb.ratings(rid)


def test_an_action_needs_all_four_parts(world):
    rid = rk.one_review(world)["review"]
    full = ("Run the 2 pm line check", "Line checks signed", "5 of 5 shifts", rk.day(21))
    for missing in range(4):
        parts = list(full)
        parts[missing] = " "
        assert rdb.add_action(rid, *parts, "Rae") == (
            None, "Every action needs a measure, a target and a day it gets checked.")
    assert rdb.add_action(rid, full[0], full[1], full[2], "next week", "Rae")[0] is None
    action_id, problem = rdb.add_action(rid, *full, "Rae")
    assert problem is None
    assert [(a["action"], a["measure"], a["target"], a["check_date"]) for a in rdb.actions(rid)] == [full]
    # done only once the review has been shared; removed only while it is a draft
    assert rdb.mark_action_done(rid, action_id, "Lena") is False
    rk.rate_all(rid)
    assert rk.finalise(rid) == "final"
    assert rdb.remove_action(rid, action_id, "Rae") is False
    assert rdb.add_action(rid, *full, "Rae")[1] == "This review can't be changed right now."
    assert rdb.mark_action_done(rid, action_id, "Lena") is False
    assert rdb.share(rid, "Rae")
    assert rdb.mark_action_done(rid, action_id, "Lena") is True
    assert rdb.mark_action_done(rid, action_id, "Lena") is False
    assert rdb.actions(rid)[0]["done_by"] == "Lena"
    assert rdb.mark_action_done(rid + 1, action_id, "Lena") is False


def test_time_away_takes_observations_out_of_the_evidence(world):
    made = rk.one_review(world)
    rid, subject = made["review"], made["subject"]
    before = rk.observe(subject, "Coached a new team member through the register.", -20)
    during = rk.observe(subject, "Closing list not signed.", -10)
    left_out = rk.observe(subject, "A note the reviewer leaves out.", -8)
    outside = rk.observe(subject, "Too old for this period.", -90)
    removed = rk.observe(subject, "Removed.", -7)
    rdb.toggle_observation(left_out, "Rae")
    rdb.remove_observation(removed, "Rae")
    assert [e["id"] for e in rdb.evidence(rdb.get_review(rid))] == [before, during]
    assert outside not in [e["id"] for e in rdb.evidence(rdb.get_review(rid))]
    assert rdb.add_time_away(rid, rk.day(-12), rk.day(-9), "Rae") is None
    assert rdb.add_time_away(rid, rk.day(-12), rk.day(-9), "Rae") is None       # the same range twice is one
    review = rdb.get_review(rid)
    assert rdb.time_away(review) == [(rk.day(-12), rk.day(-9))]
    assert json.loads(review["time_away"]) == [[rk.day(-12), rk.day(-9)]]       # dates only
    assert [e["id"] for e in rdb.evidence(review)] == [before]
    assert rdb.add_time_away(rid, rk.day(-3), rk.day(-5), "Rae") == "The last day away can't be before the first."
    assert rdb.add_time_away(rid, "soon", "", "Rae") == "Choose the first and last day away."
    assert rdb.add_time_away(rid, rk.day(-1), "", "Rae") is None                # one day
    assert rdb.remove_time_away(rid, rk.day(-12), rk.day(-9), "Rae") is True
    assert rdb.remove_time_away(rid, rk.day(-12), rk.day(-9), "Rae") is False
    assert [e["id"] for e in rdb.evidence(rdb.get_review(rid))] == [before, during]


# --- finishing --------------------------------------------------------------------

def test_every_standard_is_rated_before_the_gate(world):
    rid = rk.one_review(world)["review"]
    review, model = rdb.get_review(rid), people_db.active_model()
    assert [d["key"] for d in rdb.unrated(review, model)] == list(rk.KEYS)
    rdb.save_rating(rid, "reliable", 2, False, "", "Rae")
    rdb.save_rating(rid, "teamwork", None, True, "", "Rae")
    assert [d["key"] for d in rdb.unrated(review, model)] == ["guest-care"]
    assert rdb.finalise(rid, rk.NO, "Rae") == (None, "unrated")
    rdb.save_rating(rid, "guest-care", 4, False, "", "Rae")
    assert rdb.unrated(review, model) == []
    # every question needs an answer, and only "no" or "yes"
    assert rdb.finalise(rid, {}, "Rae") == (None, "gate")
    assert rdb.finalise(rid, {**rk.NO, "injury": "maybe"}, "Rae") == (None, "gate")
    assert rdb.finalise(rid, None, "Rae") == (None, "gate")
    assert rdb.get_review(rid)["status"] == "draft"
    assert rdb.finalise(999, rk.NO, "Rae") == (None, "gone")


def test_all_no_finalises(world):
    rid = rk.one_review(world)["review"]
    rk.rate_all(rid)
    assert rdb.finalise(rid, rk.NO, "Rae") == ("final", None)
    review = rdb.get_review(rid)
    assert review["status"] == "final" and review["finalised_by"] == "Rae" and review["finalised_at"]
    assert review["done"] and not review["editable"]
    assert rdb.gate_answers(review) == {"answers": rk.NO, "since_release": None}
    assert review["gate_by"] == "Rae"
    assert rdb.finalise(rid, rk.NO, "Rae") == (None, "gone")           # not twice
    # nothing is written once it is final
    assert rdb.save_rating(rid, "reliable", 1, False, "", "Rae") is False
    assert rdb.save_summary(rid, "x", "y", "Rae") is False
    assert rdb.add_time_away(rid, rk.day(-3), rk.day(-2), "Rae") == "This review can't be changed right now."


@pytest.mark.parametrize("key", rdb.GATE_KEYS)
def test_one_yes_or_not_sure_holds_the_review(world, key):
    rid = rk.one_review(world)["review"]
    rk.rate_all(rid)
    assert rdb.finalise(rid, {**rk.NO, key: "yes"}, "Rae") == ("on_hold", None)
    review = rdb.get_review(rid)
    assert review["status"] == "on_hold" and review["finalised_at"] is None and not review["done"]
    assert rdb.gate_answers(review)["answers"][key] == "yes"
    # held means held: nothing is written, shared or finalised
    assert rdb.save_rating(rid, "reliable", 1, False, "", "Rae") is False
    assert rdb.share(rid, "Rae") is False
    assert rdb.finalise(rid, rk.NO, "Rae") == (None, "gone")
    assert rdb.reopen(rid, "Operator") is False
    # the log says it was held, never which answer held it
    row = next(r for r in people_db.audit_feed() if r["action"] == "review put on hold")
    assert row["detail"] == "" and key not in json.dumps(row)


def test_release_reopen_and_the_question_after_a_release(world):
    rid = rk.one_review(world)["review"]
    rk.rate_all(rid)
    assert rk.finalise(rid, leave="yes") == "on_hold"
    assert rdb.release(rid, "free text about why", "Operator") is False        # fixed notes only
    assert rdb.release(rid, "carry_on", "Operator") is True
    assert rdb.release(rid, "carry_on", "Operator") is False
    review = rdb.get_review(rid)
    assert review["status"] == "draft" and review["released_by"] == "Operator"
    assert review["hold_note"] == rdb.RELEASE_NOTES["carry_on"]
    assert rdb.gate_answers(review)["answers"]["leave"] == "yes"       # the record of why stays
    # the six questions are not asked again: has anything changed since?
    assert rdb.finalise(rid, rk.NO, "Rae") == (None, "gate")
    assert rdb.finalise(rid, None, "Rae", changed="yes") == ("on_hold", None)
    assert rdb.gate_answers(rdb.get_review(rid))["since_release"] == "yes"
    assert rdb.release(rid, "recheck", "Operator") is True
    assert rdb.finalise(rid, None, "Rae", changed="no") == ("final", None)
    review = rdb.get_review(rid)
    assert rdb.gate_answers(review) == {"answers": {**rk.NO, "leave": "yes"}, "since_release": "no"}
    # the Operator can send a final review back
    assert rdb.reopen(rid, "Operator") is True
    review = rdb.get_review(rid)
    assert review["status"] == "draft" and review["finalised_at"] is None and review["editable"]
    assert rdb.reopen(rid, "Operator") is False


def test_share_then_acknowledge_and_nothing_else_moves(world):
    made = rk.one_review(world)
    rid = made["review"]
    assert rdb.share(rid, "Rae") is False                              # a draft is never shared
    assert "isn't waiting" in rdb.acknowledge(rid, rk.day(), "", "Lena")
    rk.rate_all(rid)
    rk.finalise(rid)
    assert rdb.share(rid, "Rae") is True and rdb.share(rid, "Rae") is False
    assert rdb.reopen(rid, "Operator") is False                        # not once it is shared
    assert rdb.calibrate(rid, "reliable", 2, False, "Second look", "Operator").startswith("Levels can be changed")
    assert rdb.acknowledge(rid, "", "", "Lena") == "Enter the date the review was discussed with you."
    assert rdb.acknowledge(rid, rk.day(2), "", "Lena") == "That date hasn't happened yet."
    assert "before the review period" in rdb.acknowledge(rid, rk.day(-400), "", "Lena")
    assert rdb.acknowledge(rid, rk.day(-1), "I would like more time on the floor.", "Lena") is None
    review = rdb.get_review(rid)
    assert review["status"] == "acknowledged" and review["discussed_on"] == rk.day(-1)
    assert review["ack_comment"] == "I would like more time on the floor." and review["ack_at"]
    assert "isn't waiting" in rdb.acknowledge(rid, rk.day(-1), "again", "Lena")
    assert rdb.get_review(rid)["ack_comment"].startswith("I would like")


def test_evidence_is_settled_once_a_review_is_finalised(world):
    made = rk.one_review(world)
    rid, subject = made["review"], made["subject"]
    kept = rk.observe(subject, "Handled a guest complaint calmly.", -4)
    rk.rate_all(rid)
    rk.finalise(rid)
    with people_db.connect() as conn:                                  # the clock moves on a minute
        conn.execute("UPDATE rv_reviews SET finalised_at='2000-01-01 00:00' WHERE id=?", (rid,))
        conn.execute("UPDATE rv_observations SET created_at='1999-12-31 23:00' WHERE id=?", (kept,))
        conn.commit()
    late = rk.observe(subject, "Written after the review was finished.", -5)
    assert [e["id"] for e in rdb.evidence(rdb.get_review(rid))] == [kept]
    # a note that was part of it can no longer be left out or removed
    assert rdb.get_observation(kept)["locked"] is True
    assert rdb.toggle_observation(kept, "Rae") is None and rdb.remove_observation(kept, "Rae") is False
    assert rdb.get_observation(late)["locked"] is False
    assert rdb.remove_observation(late, "Rae") is True
    # reopened, it is a draft again and the notes can change
    assert rdb.reopen(rid, "Operator") is True
    assert rdb.get_observation(kept)["locked"] is False
    assert rdb.toggle_observation(kept, "Rae") is False


# --- calibration --------------------------------------------------------------------

def test_calibration_needs_a_final_review_and_a_reason(world):
    rid = rk.one_review(world)["review"]
    rk.rate_all(rid, level=3)
    assert rdb.calibrate(rid, "reliable", 2, False, "Compared with the notes", "Operator").startswith(
        "Levels can be changed in calibration only")
    rk.finalise(rid)
    assert rdb.calibrate(rid, "reliable", 2, False, "   ", "Operator") \
        == "Give a reason for the change. It is kept with the review."
    assert rdb.calibrate(rid, "reliable", 9, False, "Reason", "Operator") == "Choose the new level."
    assert rdb.calibrate(rid, "reliable", 3, False, "Reason", "Operator") \
        == "That is already the level. Nothing was changed."
    assert rdb.calibrate(rid, "nope", 2, False, "Reason", "Operator") == "That standard isn't rated on this review."
    assert rdb.calibrate(rid, "reliable", 2, False, "The notes show reminders were needed.", "Operator") is None
    row = rdb.ratings(rid)["reliable"]
    assert (row["level"], row["cal_from"], row["cal_by"]) == (2, 3, "Operator")
    assert row["cal_reason"] == "The notes show reminders were needed." and row["cal_at"]
    # a second change keeps the level the reviewer gave
    assert rdb.calibrate(rid, "reliable", None, True, "Too few notes to say.", "Operator") is None
    row = rdb.ratings(rid)["reliable"]
    assert (row["level"], row["not_enough"], row["cal_from"]) == (None, 1, 3)
    logged = [r for r in people_db.audit_feed() if r["action"] == "level changed in calibration"]
    assert [r["detail"] for r in logged] == ["reliable: 2 to not enough", "reliable: 3 to 2"]
    assert all(r["who"] == "Operator" and r["object_id"] == rid for r in logged)
    assert "reminders" not in json.dumps(logged)                       # the reason is not in the log


def test_the_calibration_table_is_alphabetical_and_has_no_totals(world):
    ids = world["ids"]
    assert people_db.grant(ids["ada"], "Ada", "reviewer", "Operator")
    lena = rk.add_subject(world, "lena", "rae")
    hari = rk.add_subject(world, "hari", "rae")
    rae = rk.add_subject(world, "rae", "ada")
    ada = rk.add_subject(world, "ada", None)
    cycle_id = rk.add_cycle()
    rk.observe(lena, "On time for every open.", -3, "reliable")
    rk.observe(lena, "Stayed to finish the close.", -2, "reliable")
    for subject, levels in ((lena, (4, 4, 4)), (hari, (4, 3, 4)), (rae, (1, 2, 3))):
        rid = rk.review_of(cycle_id, subject)
        for key, level in zip(rk.KEYS, levels):
            assert rdb.save_rating(rid, key, level, False, "", "x")
        rk.finalise(rid)
    table = rdb.calibration(cycle_id)
    assert [r["review"]["display_name"] for r in table["rows"]] == ["Ada", "Hari", "Lena", "Rae"]
    assert [r["reviewer"] for r in table["rows"]] == ["Operator", "Rae", "Rae", "Ada"]
    assert table["rows"][0]["cells"] == {}                             # a draft shows no levels
    cell = table["rows"][2]["cells"]["reliable"]
    assert cell == {"level": 4, "not_enough": False, "evidence": 2, "changed": False}
    for row in table["rows"]:
        assert not {"total", "average", "score", "rank"} & set(row)
    by_name = {s["name"]: s for s in table["reviewers"]}
    assert set(by_name) == {"Ada", "Rae"}                              # only those with finished reviews
    assert by_name["Rae"]["levels"] == [0, 0, 1, 5] and by_name["Rae"]["given"] == 6
    assert by_name["Rae"]["top_two"] == 6 and by_name["Rae"]["mostly_top"] is True
    assert (by_name["Rae"]["flat_reviews"], by_name["Rae"]["flat_of"]) == (1, 2)
    assert by_name["Rae"]["mostly_flat"] is False
    assert by_name["Ada"]["mostly_top"] is False and by_name["Ada"]["mostly_flat"] is False
    team = {t["dim"]["key"]: t for t in table["team"]}
    assert team["reliable"]["levels"] == [1, 0, 0, 2] and team["reliable"]["total"] == 3
    assert rdb.calibration(999) is None


# --- reflection -------------------------------------------------------------------

def test_a_reflection_is_the_leaders_until_they_send_it(world):
    made = rk.one_review(world)
    rid = made["review"]
    words = {"reliable": "I opened on time all month.", "teamwork": "", "not-a-standard": "ignored"}
    assert rdb.save_reflection(rid, words, False, "Lena") is None
    assert rdb.reflections(rid) == {"reliable": "I opened on time all month."}
    assert rdb.get_review(rid)["reflection_at"] is None
    assert rdb.save_reflection(rid, {}, True, "Lena") == "Write something in at least one box first."
    assert rdb.save_reflection(rid, words, True, "Lena") is None
    assert rdb.get_review(rid)["reflection_at"]
    # still open while the review is on hold, so a hold never shows on the leader's side
    rk.rate_all(rid)
    assert rk.finalise(rid, health="yes") == "on_hold"
    assert rdb.reflection_open(rdb.get_review(rid)) is True
    assert rdb.save_reflection(rid, {"reliable": "Updated."}, False, "Lena") is None
    assert rdb.reflections(rid) == {"reliable": "Updated."}
    rdb.release(rid, "carry_on", "Operator")
    assert rdb.finalise(rid, None, "Rae", changed="no")[0] == "final"
    assert rdb.save_reflection(rid, {"reliable": "Too late."}, True, "Lena") \
        == "Your reflection can't be changed any more."
    assert rdb.reflections(rid) == {"reliable": "Updated."}


def test_no_reflection_when_the_cycle_does_not_ask(world):
    rid = rk.one_review(world, ask_reflection=False)["review"]
    review = rdb.get_review(rid)
    assert rdb.reflection_open(review) is False and rdb.visible_to_leader(review) is False
    assert rdb.save_reflection(rid, {"reliable": "Hello"}, True, "Lena") is not None
    assert rdb.reflections(rid) == {}


# --- what is waiting, for the menu ------------------------------------------------

def test_nav_counts_for_each_kind_of_person(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae")
    ada = rk.add_subject(world, "ada", None)
    rae = rk.add_subject(world, "rae", None)
    cycle_id = rk.add_cycle(due=-1)                                    # already past due
    assert rdb.nav_counts("operator", None, "Operator") == {"on_hold": 0, "past_due": 3, "drafts": 2}
    assert rdb.nav_counts("leader", ids["rae"], "Rae") == {"drafts": 1, "mine": 1}      # her own reflection
    assert rdb.nav_counts("leader", ids["lena"], "Lena") == {"drafts": 0, "mine": 1}
    assert rdb.nav_counts("leader", ids["hari"], "Hari") == {"drafts": 0, "mine": 0}
    # id and name must both match: a renumbered or renamed sign-in sees nothing
    assert rdb.nav_counts("leader", ids["lena"], "Someone Else") == {"drafts": 0, "mine": 0}
    assert rdb.nav_counts("leader", ids["hari"], "Lena") == {"drafts": 0, "mine": 0}
    assert rdb.nav_counts("leader", ids["rae"], "Not Rae") == {"drafts": 0, "mine": 0}
    assert rdb.nav_counts("leader", None, "") == {"drafts": 0, "mine": 0}
    rid = rk.review_of(cycle_id, lena)
    assert rdb.save_reflection(rid, {"reliable": "Mine."}, True, "Lena") is None
    assert rdb.nav_counts("leader", ids["lena"], "Lena")["mine"] == 0          # sent
    rk.rate_all(rid)
    assert rk.finalise(rid, concern="yes") == "on_hold"
    assert rdb.nav_counts("operator", None, "Operator") == {"on_hold": 1, "past_due": 2, "drafts": 2}
    assert rdb.nav_counts("leader", ids["rae"], "Rae")["drafts"] == 0
    assert rdb.nav_counts("leader", ids["lena"], "Lena")["mine"] == 0          # a hold is not hers to see
    rdb.release(rid, "carry_on", "Operator")
    rdb.finalise(rid, None, "Rae", changed="no")
    assert rdb.nav_counts("leader", ids["lena"], "Lena")["mine"] == 0          # final, not yet shared
    rdb.share(rid, "Rae")
    assert rdb.nav_counts("leader", ids["lena"], "Lena")["mine"] == 1
    rdb.acknowledge(rid, rk.day(), "", "Lena")
    assert rdb.nav_counts("leader", ids["lena"], "Lena")["mine"] == 0
    rdb.close_cycle(cycle_id, "Operator")                              # a closed cycle nags nobody
    assert rdb.nav_counts("operator", None, "Operator") == {"on_hold": 0, "past_due": 0, "drafts": 0}
    assert rdb.nav_counts("leader", ids["rae"], "Rae") == {"drafts": 0, "mine": 0}
    assert ada and rae


def test_a_reviewer_never_gets_their_own_review_even_if_assigned_by_mistake(world):
    ids = world["ids"]
    made = rk.one_review(world, "lena", "rae")
    with people_db.connect() as conn:                                  # a link that should never exist
        conn.execute("UPDATE rv_reviews SET reviewer_leader_id=?, reviewer_name='Lena' WHERE id=?",
                     (ids["lena"], made["review"]))
        conn.execute("UPDATE rv_subjects SET reviewer_leader_id=?, reviewer_name='Lena' WHERE id=?",
                     (ids["lena"], made["subject"]))
        conn.commit()
    assert rdb.reviews_for_reviewer(ids["lena"], "Lena") == []
    assert rdb.subjects_for_reviewer(ids["lena"], "Lena") == []
    assert rdb.is_self(rdb.get_subject(made["subject"]), ids["lena"], "Lena")
    assert rdb.is_self(rdb.get_subject(made["subject"]), 999, "lena")           # the name alone is enough
    assert not rdb.is_self(rdb.get_subject(made["subject"]), ids["rae"], "Rae")


# --- process health ---------------------------------------------------------------

def test_process_health_counts(world):
    lena = rk.add_subject(world, "lena", "rae")
    hari = rk.add_subject(world, "hari", "rae")
    ada = rk.add_subject(world, "ada", None)
    cycle_id = rk.add_cycle()
    rk.observe(lena, "On time.", -3, "reliable")
    rk.observe(lena, "Covered a shift.", -2, "reliable")
    rk.observe(lena, "Helped a guest find a lost phone.", -2, "guest-care")
    first = rk.review_of(cycle_id, lena)
    rdb.save_rating(first, "reliable", 4, False, "", "Rae")
    rdb.save_rating(first, "guest-care", 3, False, "", "Rae")
    rdb.save_rating(first, "teamwork", None, True, "", "Rae")
    rdb.mark_ai_shown(first, {"reliable": 4, "guest-care": 2, "teamwork": None, "not-rated": 1})
    rdb.mark_ai_shown(first, {"reliable": 1})                          # only the first showing counts
    assert rdb.ratings(first)["reliable"]["ai_level"] == 4
    on_time, _ = rdb.add_action(first, "Line check", "Checks signed", "5 of 5", rk.day(-1), "Rae")
    rdb.add_action(first, "Coach one opener", "Coaching notes", "1 a week", rk.day(-1), "Rae")
    rdb.add_action(first, "Later", "m", "t", rk.day(30), "Rae")
    rk.finalise(first)
    assert rdb.calibrate(first, "guest-care", 2, False, "Notes support the lower level.", "Operator") is None
    rdb.share(first, "Rae")
    with people_db.connect() as conn:                                  # done the day before it was due
        conn.execute("UPDATE rv_actions SET done_at=?, done_by='Lena' WHERE id=?",
                     (rk.day(-2) + " 09:00", on_time))
        conn.commit()
    second = rk.review_of(cycle_id, hari)
    rk.rate_all(second, level=2)
    rk.finalise(second)
    with people_db.connect() as conn:                                  # finished after the due date
        conn.execute("UPDATE rv_reviews SET finalised_at=? WHERE id=?", (rk.day(20) + " 10:00", second))
        conn.commit()
    got = rdb.health(cycle_id)
    assert (got["total"], got["finalised"], got["on_time"]) == (3, 2, 1)
    assert got["levels"] == [0, 4, 0, 1] and got["not_enough"] == 1 and got["ratings"] == 6
    assert (got["rated"], got["evidence_items"], got["no_evidence"]) == (5, 3, 3)
    # the reviewer's own level is what is compared with the suggestion, not the calibrated one
    assert (got["ai_shown"], got["ai_same"], got["ai_not_enough"]) == (3, 2, 1)
    assert (got["cal_up"], got["cal_down"], got["cal_other"]) == (0, 1, 0)
    assert (got["actions"], got["actions_due"], got["actions_on_time"]) == (3, 2, 1)
    assert got["quick"] == 3                                           # Lena's, saved just before finishing
    assert rdb.health(999) is None and ada


def test_the_log_never_holds_what_was_written(world):
    made = rk.one_review(world)
    rid = made["review"]
    secret = "Zebra-crossing-wording"
    rk.observe(made["subject"], secret)
    rdb.save_rating(rid, "reliable", 2, False, secret, "Rae")
    rdb.save_summary(rid, secret, secret, "Rae")
    rdb.add_action(rid, secret, secret, secret, rk.day(5), "Rae")
    rdb.save_reflection(rid, {"reliable": secret}, True, "Lena")
    rdb.add_time_away(rid, rk.day(-3), rk.day(-2), "Rae")
    feed = json.dumps(people_db.audit_feed())
    assert secret not in feed and rk.day(-3) not in feed
    actions = [r["action"] for r in people_db.audit_feed()]
    for expected in ("observation added", "rating saved", "summary saved", "action added",
                     "reflection sent", "time away dates changed", "review cycle started",
                     "leader added to reviews"):
        assert expected in actions, expected
