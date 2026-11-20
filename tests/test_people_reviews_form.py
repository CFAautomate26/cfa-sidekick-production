"""Leader reviews: the review form, the checks before finishing, the
"before you finish" questions, holds, reopening and sharing. Everyone here
is invented."""

import re

import pytest

import people_db
import people_reviews
import people_reviews_db as rdb
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk

DENIED, NOT_YOURS, page, post, opened = rk.DENIED, rk.NOT_YOURS, rk.page, rk.post, rk.opened


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return kit.make_world(monkeypatch, tmp_path)


def gate_form(**answers):
    form = {f"gate_{key}": "no" for key in rdb.GATE_KEYS}
    form.update({f"gate_{key}": value for key, value in answers.items()})
    return form


def url(made, tail=""):
    return f"/shift/reviews/review/{made['review']}{tail}"


# --- the form -----------------------------------------------------------------------

def test_the_form_puts_each_standard_beside_its_evidence(world):
    subject = rk.add_subject(world, "lena", "rae", title="Team leader", area="Front of house")
    cycle = rk.add_cycle("Autumn check-in")
    made = {"review": rk.review_of(cycle, subject)}
    rk.observe(subject, "Opened on time all week.", -5, "reliable", "strength")
    rk.observe(subject, "Helped a new starter on the register.", -4, "teamwork")
    rk.observe(subject, "A general note about the month.", -3, None)
    rk.observe(subject, "From before this period.", -90, "reliable")
    left_out = rk.observe(subject, "The reviewer left this one out.", -2, "reliable")
    removed = rk.observe(subject, "This one was removed.", -2, "reliable")
    rdb.toggle_observation(left_out, "Rae")
    rdb.remove_observation(removed, "Rae")
    html = page(kit.open_as(world, "rae"), url(made))
    hero = html[html.index('<section class="hero compact">'):html.index("</section>")]
    for part in ("Lena", "Team leader", "Front of house", "Autumn check-in", "Draft", "reviewer: Rae", "due"):
        assert part in hero, part
    assert "What to write, and what never to write" in html
    model = people_db.active_model()
    for dim in model["dimensions"]:
        card = html[html.index(f'id="d-{dim["key"]}"'):]
        # (rewritten after the browser run, finding B6: the standards are no longer eleven separate
        # forms; each one's boxes belong to the page's single rating form and carry its key)
        card = card[:card.index("</fieldset>")]
        assert dim["name"] in card and dim["definition"] in card
        assert "What each level looks like" in card
        for level, anchor in zip(model["levels"], dim["anchors"]):
            assert level in card and anchor in card
        assert card.count('type="radio"') == 5 and "Not enough to rate yet" in card
        assert card.count(f'form="rv-rate" name="level-{dim["key"]}"') == 5
        assert f'form="rv-rate" name="comment-{dim["key"]}"' in card
    reliable = html[html.index('id="d-reliable"'):html.index('id="d-guest-care"')]
    assert "Opened on time all week." in reliable and "Did well" in reliable
    assert "Helped a new starter" not in reliable
    assert "No notes for this standard in this period." in html[html.index('id="d-guest-care"'):]
    loose = html[html.index('id="loose"'):]
    assert "Not tied to one standard" in loose and "A general note about the month." in loose
    assert html.count("A general note about the month.") == 1
    for hidden in ("From before this period.", "The reviewer left this one out.", "This one was removed."):
        assert hidden not in html, hidden
    assert "Dates only. Don't write the reason." in html
    assert "Every action needs a measure, a target and a day it gets checked." in html
    assert "3 standards still need" in html and "Check and finish" in html
    assert opened("review opened") == [("Rae", made["review"])]
    # with AI off, the page says nothing about AI at all
    assert "AI" not in re.sub(r"<[^>]+>", " ", html[html.index("<main"):])


def test_one_standard_is_saved_at_a_time(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    resp = rae.post(url(made, "/rate"), data={"dimension_key": "reliable", "level": "3",
                                              "comment": "On time for every open I saw."})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(url(made) + "#d-reliable")
    row = rdb.ratings(made["review"])["reliable"]
    assert (row["level"], row["not_enough"], row["comment"], row["rated_by"]) \
        == (3, 0, "On time for every open I saw.", "Rae")
    html = post(rae, url(made, "/rate"), {"dimension_key": "teamwork", "level": "not_enough"})
    assert "Saved." in html and rdb.ratings(made["review"])["teamwork"]["not_enough"] == 1
    # (rewritten after the browser run, finding B6: every standard's boxes now belong to one form,
    # so each radio group carries its standard's key)
    assert re.search(r'name="level-reliable" value="3" checked', html)
    assert "On time for every open I saw.</textarea>" in html
    html = post(rae, url(made, "/rate"), {"dimension_key": "guest-care", "comment": "No level picked."})
    assert "Choose a level, or “Not enough to rate yet”, then save." in html
    assert "guest-care" not in rdb.ratings(made["review"])
    post(rae, url(made, "/rate"), {"dimension_key": "not-a-standard", "level": "2"})
    assert set(rdb.ratings(made["review"])) == {"reliable", "teamwork"}
    # the Operator can write on any review, and it says who did
    post(kit.open_as(world, "operator"), url(made, "/rate"), {"dimension_key": "guest-care", "level": "2"})
    assert rdb.ratings(made["review"])["guest-care"]["rated_by"] == "Operator"
    assert "1 standard still needs" not in page(rae, url(made))


def test_summary_actions_and_time_away(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    in_range = rk.observe(made["subject"], "Seen during the days away.", -10, "reliable")
    html = post(rae, url(made, "/summary"), {"strengths": "Calm in a rush.", "growth": "Hand over the close."})
    assert "Saved." in html and "Calm in a rush.</textarea>" in html
    assert rdb.get_review(made["review"])["growth"] == "Hand over the close."
    # an action without all four parts is refused, with the reason
    full = {"action": "Run the 2 pm line check", "measure": "Line checks signed",
            "target": "5 of 5 shifts", "check_date": rk.day(21)}
    for missing in full:
        html = post(rae, url(made, "/actions/add"), {**full, missing: ""})
        assert "Every action needs a measure, a target and a day it gets checked." in \
            html[html.index('class="flash"'):html.index("</section>")]
    assert rdb.actions(made["review"]) == []
    html = post(rae, url(made, "/actions/add"), full)
    assert "Action added." in html and "Run the 2 pm line check" in html and "Target: 5 of 5 shifts" in html
    action = rdb.actions(made["review"])[0]
    html = post(rae, url(made, f"/actions/{action['id']}/remove"))
    assert "Action removed." in html and rdb.actions(made["review"]) == []
    assert "That action couldn&#39;t be removed." in post(rae, url(made, f"/actions/{action['id']}/remove"))
    # time away: dates only, and notes inside it stop counting
    assert "Seen during the days away." in page(rae, url(made))
    html = post(rae, url(made, "/time-away"), {"start": rk.day(-12), "end": rk.day(-9)})
    assert "Dates saved." in html and "Seen during the days away." not in html
    assert rdb.time_away(rdb.get_review(made["review"])) == [(rk.day(-12), rk.day(-9))]
    assert "The last day away can&#39;t be before the first." in post(
        rae, url(made, "/time-away"), {"start": rk.day(-2), "end": rk.day(-4)})
    html = post(rae, url(made, "/time-away"), {"remove": f"{rk.day(-12)}|{rk.day(-9)}"})
    assert "Dates removed." in html and "Seen during the days away." in html
    assert in_range


def test_who_may_open_and_write_a_review(world):
    ids = world["ids"]
    lena = rk.add_subject(world, "lena", "rae")
    ada = rk.add_subject(world, "ada", None)                 # the Operator reviews Ada
    cycle = rk.add_cycle()
    mine, others = rk.review_of(cycle, lena), rk.review_of(cycle, ada)
    writes = (("/rate", {"dimension_key": "reliable", "level": "1"}),
              ("/summary", {"strengths": "x"}),
              ("/actions/add", {"action": "a", "measure": "m", "target": "t", "check_date": rk.day(5)}),
              ("/actions/1/remove", {}), ("/time-away", {"start": rk.day(-3)}), ("/ai", {}),
              ("/finalise", gate_form()), ("/share", {}))

    def shut(client, review_id):
        for tail in ("", "/finalise", "/print"):
            resp = client.get(f"/shift/reviews/review/{review_id}{tail}")
            assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people"), tail
        for tail, data in writes:
            assert DENIED in post(client, f"/shift/reviews/review/{review_id}{tail}", data), tail
        for tail in ("/release", "/reopen"):
            assert NOT_YOURS in post(client, f"/shift/reviews/review/{review_id}{tail}", {"note": "carry_on"})

    for who in ("lena", "ada", "hari"):                      # the subject, admin role only, hiring only
        shut(kit.open_as(world, who), mine)
    rae = kit.open_as(world, "rae")
    assert page(rae, f"/shift/reviews/review/{mine}")
    shut(rae, others)                                        # a reviewer, but not for Ada
    shut(rae, 999)                                           # not there: the same answer
    assert page(kit.open_as(world, "operator"), f"/shift/reviews/review/{others}")
    assert rdb.ratings(mine) == {} and rdb.ratings(others) == {}
    assert rdb.get_review(mine)["strengths"] == "" and rdb.actions(mine) == []
    # a renamed sign-in, then a removed grant: both fail closed on the next tap
    with people_db.connect() as conn:
        conn.execute("UPDATE rv_reviews SET reviewer_name='Rae Before' WHERE id=?", (mine,))
        conn.commit()
    shut(rae, mine)
    with people_db.connect() as conn:
        conn.execute("UPDATE rv_reviews SET reviewer_name='Rae' WHERE id=?", (mine,))
        conn.commit()
    assert page(rae, f"/shift/reviews/review/{mine}")
    people_db.revoke(next(g["id"] for g in people_db.grants() if g["capability"] == "reviewer"), "Operator")
    shut(rae, mine)
    assert ids


def test_a_reviewer_never_opens_their_own_review(world):
    ids = world["ids"]
    made = rk.one_review(world, "rae", None)
    with people_db.connect() as conn:                        # assigned to herself: must never work
        conn.execute("UPDATE rv_reviews SET reviewer_leader_id=?, reviewer_name='Rae' WHERE id=?",
                     (ids["rae"], made["review"]))
        conn.commit()
    rae = kit.open_as(world, "rae")
    for tail in ("", "/finalise", "/print"):
        resp = rae.get(url(made, tail))
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
    assert DENIED in post(rae, url(made, "/rate"), {"dimension_key": "reliable", "level": "4"})
    assert rdb.ratings(made["review"]) == {}
    assert f'/shift/reviews/review/{made["review"]}' not in page(rae, "/shift/reviews")


# --- the checks before finishing ------------------------------------------------------

def test_finishing_lists_what_is_not_rated_yet(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    rdb.save_rating(made["review"], "reliable", 3, False, "", "Rae")
    html = page(rae, url(made, "/finalise"))
    assert "2 standards still need a rating." in html
    assert "Guest care" in html and "Teamwork" in html and "Before you finish" not in html
    assert 'name="gate_leave"' not in html
    html = post(rae, url(made, "/finalise"), gate_form())
    assert "Rate every standard first." in html and rdb.get_review(made["review"])["status"] == "draft"


def test_wording_is_checked_and_the_reviewer_must_say_they_read_it(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    rk.observe(made["subject"], "Looked tired, maybe a health thing.", -3, "reliable")
    rk.rate_all(made["review"])
    rdb.save_rating(made["review"], "teamwork", 2, False, "Can be lazy when it is quiet.", "Rae")
    rdb.save_summary(made["review"], "Great with guests.", "Needs to stop being so emotional.", "Rae")
    rdb.add_action(made["review"], "Stay off the drama", "None seen", "Zero", rk.day(9), "Rae")
    html = page(rae, url(made, "/finalise"))
    assert "Check your wording" in html
    for where, term, category in (("Your comment on Teamwork", "lazy", "Personality labels"),
                                  ("Growth focus", "emotional", "Personality labels"),
                                  ("An agreed action", "drama", "Personality labels"),
                                  ("A note dated", "health", "Health and disability")):
        assert where in html and f"“{term}” ({category})" in html, where
    assert "Describe what the person did or said, not what they are like." in html
    assert "Strengths</p>" not in html                       # nothing to flag in the strengths
    assert "I have read these and my wording is about behaviour at work" in html
    html = post(rae, url(made, "/finalise"), gate_form())
    assert "Tick the box to say you have read the wording checks." in html
    assert rdb.get_review(made["review"])["status"] == "draft"
    html = post(rae, url(made, "/finalise"), {**gate_form(), "wording_read": "1"})
    assert "Review finalised." in html and rdb.get_review(made["review"])["status"] == "final"


def test_a_level_with_no_notes_behind_it_gets_a_warning(world):
    made = rk.one_review(world)
    rk.observe(made["subject"], "On time.", -3, "reliable")
    rdb.save_rating(made["review"], "reliable", 3, False, "", "Rae")
    rdb.save_rating(made["review"], "guest-care", 4, False, "", "Rae")
    rdb.save_rating(made["review"], "teamwork", None, True, "", "Rae")
    html = page(kit.open_as(world, "rae"), url(made, "/finalise"))
    warning = html[html.index("Rated with no notes listed."):html.index("<form")]
    assert "Guest care" in warning and "Reliable" not in warning and "Teamwork" not in warning
    assert "Check your wording" not in html


# --- the "before you finish" questions --------------------------------------------------

def test_the_questions_are_asked_as_written(world):
    made = rk.one_review(world)
    rk.rate_all(made["review"])
    html = page(kit.open_as(world, "rae"), url(made, "/finalise"))
    gate = html[html.index('id="gate"'):]
    assert "Before you finish" in gate
    assert "Some things must never count against a person. Answer for the review period." in gate
    for text in (
            "Was this leader away on any leave during the period (sick, family, pregnancy or parental, "
            "bereavement or any other leave)?",
            "Does this leader have an accommodation, or have they asked for one?",
            "Do you know of, or suspect, a health condition or disability that could be affecting their work?",
            "Is this leader pregnant, or have they recently returned from pregnancy or parental leave?",
            "Has this leader had a workplace injury or a WSIB claim?",
            "Has this leader raised a concern or complaint about safety, harassment, pay or how they were "
            "treated?"):
        assert text in gate, text
    assert len(rdb.GATE) == 6 and gate.count('value="no" required') == 6
    assert gate.count("Yes or not sure</span>") == 6 and "checked" not in gate       # no answer is pre-picked


def test_all_no_makes_it_final(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    rk.rate_all(made["review"])
    html = post(rae, url(made, "/finalise"), {k: v for k, v in gate_form().items() if k != "gate_injury"})
    assert "Answer every question before you finish." in html
    assert rdb.get_review(made["review"])["status"] == "draft"
    html = post(rae, url(made, "/finalise"), gate_form())
    assert "Review finalised. Share it when you are ready to talk it through." in html
    review = rdb.get_review(made["review"])
    assert (review["status"], review["finalised_by"], review["gate_by"]) == ("final", "Rae", "Rae")
    assert "Finalised" in html and "Share with Lena" in html and "Print view" in html
    # nothing on it can be written any more
    for tail in ("/rate", "/summary", "/actions/add", "/time-away", "/ai"):
        assert f'action="{url(made, tail)}"' not in html, tail
    assert "Check and finish" not in html
    for tail, data in (("/rate", {"dimension_key": "reliable", "level": "1"}), ("/summary", {"strengths": "x"}),
                       ("/time-away", {"start": rk.day(-3)})):
        assert "This review is finished, so it can&#39;t be changed." in post(rae, url(made, tail), data)
    assert rdb.ratings(made["review"])["reliable"]["level"] == 3
    resp = rae.get(url(made, "/finalise"))
    assert resp.status_code == 302 and resp.headers["Location"].endswith(url(made))
    assert ("Rae", "review finalised", "review", made["review"]) in rk.audit_actions()


@pytest.mark.parametrize("key", rdb.GATE_KEYS)
def test_one_yes_or_not_sure_holds_it(world, key):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    rk.rate_all(made["review"])
    html = post(rae, url(made, "/finalise"), gate_form(**{key: "yes"}))
    review = rdb.get_review(made["review"])
    assert review["status"] == "on_hold" and review["finalised_at"] is None      # held, not final
    assert people_reviews.HOLD_TEXT in html
    assert people_reviews.HOLD_TEXT == (
        "This review is on hold. Nothing is wrong: it means the Operator needs to look at this with you "
        "before it goes any further, so that nothing protected counts against anyone. Do not discuss the "
        "review with the leader yet.")
    # nothing editable, nothing to share or print, and no AI
    assert "<form" not in html[html.index("<main"):html.index("pp-lockbar")]
    assert "Share with" not in html and "Print view" not in html
    resp = rae.get(url(made, "/print"))
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/people")
    assert "This review is on hold, so nothing on it can be changed." in post(
        rae, url(made, "/rate"), {"dimension_key": "reliable", "level": "1"})
    assert "Only a finalised review can be shared." in post(rae, url(made, "/share"))
    # the reviewer's page does not repeat the answers; only the Operator sees which one
    question = dict(rdb.GATE)[key]
    assert question not in html and "Why it stopped" not in html
    assert NOT_YOURS in post(rae, url(made, "/release"), {"note": "carry_on"})
    assert rdb.get_review(made["review"])["status"] == "on_hold"
    op_html = page(kit.open_as(world, "operator"), url(made))
    held = op_html[op_html.index('id="hold-answers"'):]
    assert question in held[:held.index("</ul>")]
    assert sum(q in held for _, q in rdb.GATE) == 1
    assert "This tool does not decide discipline, pay, hours, demotion or dismissal" in op_html
    assert "escalation matrix" in op_html and "Release the hold" in op_html
    assert "1 on hold" in page(kit.open_as(world, "operator"), "/shift/people")


def test_only_the_operator_releases_and_the_gate_does_not_loop(world):
    made = rk.one_review(world)
    rae, op = kit.open_as(world, "rae"), kit.open_as(world, "operator")
    rk.rate_all(made["review"])
    post(rae, url(made, "/finalise"), gate_form(leave="yes"))
    # the note is one of the fixed sentences: there is nowhere to type the reason
    op_html = page(op, url(made))
    hold = op_html[op_html.index('id="hold-answers"'):op_html.index("</form>", op_html.index('id="hold-answers"'))]
    assert "<textarea" not in hold and 'type="text"' not in hold
    html = post(op, url(made, "/release"), {"note": "She was on leave in October"})
    assert "Choose what to record, then release." in html
    assert rdb.get_review(made["review"])["status"] == "on_hold"
    html = post(op, url(made, "/release"), {"note": "carry_on"})
    assert "Hold released." in html
    review = rdb.get_review(made["review"])
    assert (review["status"], review["released_by"]) == ("draft", "Operator")
    assert review["hold_note"] == "Handled outside this tool. The review carries on as written."
    assert "leave in October" not in str(people_db.audit_feed())
    # back with the reviewer: editable again, told only that it was looked at
    html = page(rae, url(made))
    assert "The Operator looked at this review and released it" in html
    assert f'action="{url(made, "/rate")}"' in html
    assert dict(rdb.GATE)["leave"] not in html
    # finishing asks one question, not the six again
    html = page(rae, url(made, "/finalise"))
    assert "Has anything changed since then" in html and 'name="gate_leave"' not in html
    assert "Answer every question before you finish." in post(rae, url(made, "/finalise"), gate_form())
    post(rae, url(made, "/finalise"), {"changed": "yes"})
    assert rdb.get_review(made["review"])["status"] == "on_hold"
    assert "something has changed since you last released" in page(op, url(made))
    post(op, url(made, "/release"), {"note": "recheck"})
    html = post(rae, url(made, "/finalise"), {"changed": "no"})
    assert "Review finalised." in html and rdb.get_review(made["review"])["status"] == "final"


def test_only_the_operator_reopens_a_final_review(world):
    made = rk.one_review(world)
    rae, op = kit.open_as(world, "rae"), kit.open_as(world, "operator")
    assert "Only a finalised review in an open cycle can be reopened." in post(op, url(made, "/reopen"))
    rk.rate_all(made["review"])
    rk.finalise(made["review"])
    assert "Reopen this review" not in page(rae, url(made))
    assert NOT_YOURS in post(rae, url(made, "/reopen"))
    assert rdb.get_review(made["review"])["status"] == "final"
    assert "Reopen this review" in page(op, url(made))
    html = post(op, url(made, "/reopen"))
    assert "Reopened. The review is a draft again." in html
    assert rdb.get_review(made["review"])["status"] == "draft"
    assert ("Operator", "review reopened", "review", made["review"]) in rk.audit_actions()
    assert f'action="{url(made, "/rate")}"' in page(rae, url(made))     # the reviewer can write again


def test_sharing_takes_a_final_review_and_its_reviewer(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    assert "Only a finalised review can be shared." in post(rae, url(made, "/share"))
    rk.rate_all(made["review"])
    rk.finalise(made["review"])
    html = post(rae, url(made, "/share"))
    assert "Shared. Lena can now read it under My review." in html
    review = rdb.get_review(made["review"])
    assert (review["status"], review["shared_by"]) == ("shared", "Rae")
    assert "Waiting for Lena to read it and confirm it was discussed." in html
    assert "Share with Lena" not in html and "Reopen this review" not in html
    assert "Only a finalised review can be shared." in post(rae, url(made, "/share"))
    # a leader with no sign-in linked gets a printed copy instead
    jo, _ = rdb.add_subject("Jo Example", "Operator")
    review_id, _ = rdb.add_review(made["cycle"], jo, "Operator")
    rk.rate_all(review_id, by="Operator")
    rk.finalise(review_id, by="Operator")
    op = kit.open_as(world, "operator")
    assert "no Team Hub sign-in linked" in page(op, f"/shift/reviews/review/{review_id}")
    assert "so print it for them." in post(op, f"/shift/reviews/review/{review_id}/share")


def test_a_closed_cycle_freezes_unfinished_reviews(world):
    made = rk.one_review(world)
    rae = kit.open_as(world, "rae")
    rdb.close_cycle(made["cycle"], "Operator")
    html = page(rae, url(made))
    assert "This cycle is closed, so this review was left unfinished and can't be changed." in html
    assert f'action="{url(made, "/rate")}"' not in html and "Check and finish" not in html
    assert "This cycle is closed, so the review can&#39;t be changed." in post(
        rae, url(made, "/rate"), {"dimension_key": "reliable", "level": "2"})
    assert rdb.ratings(made["review"]) == {}
    resp = rae.get(url(made, "/finalise"))
    assert resp.status_code == 302
