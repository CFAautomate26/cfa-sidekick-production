"""Leader reviews: calibration, sharing, what the leader sees and
confirms, the print view, process health, and the house rules every page
keeps (no scripts, strict headers, a log line on every open). Everyone
here is invented."""

import json
import os
import re

import pytest

import app as cfa_app
import people
import people_db
import people_reviews
import people_reviews_db as rdb
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk

DENIED, NOT_YOURS, page, post, opened = rk.DENIED, rk.NOT_YOURS, rk.page, rk.post, rk.opened
LABEL, draft_reply = rk.AI_LABEL, rk.draft_reply
ACK = ("I confirm that I received this review and that it was discussed with me on {date}. This confirms "
       "receipt and discussion only. It does not mean I agree with the review. I can add comments below, "
       "and they will be kept with this review.")
SUPPORT = ("If anything here is affecting your work that you would like support or an accommodation for, "
           "please tell the Operator at any time.")


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return kit.make_world(monkeypatch, tmp_path)


def mine(made, tail=""):
    return f"/shift/reviews/mine/{made['review']}{tail}"


def url(made, tail=""):
    return f"/shift/reviews/review/{made['review']}{tail}"


def main(html):
    return html[html.index("<main"):html.index("</main>")]


def full_review(world, monkeypatch):
    """A review that has been through everything a leader must never see:
    an AI draft, a hold and its release, and a calibration change. Then
    shared with Lena."""
    kit.fake_ai(monkeypatch, draft_reply)
    people_db.set_setting("ai_reviews", "1", "Operator")
    subject = rk.add_subject(world, "lena", "rae", title="Team leader", area="Front of house")
    cycle = rk.add_cycle("Autumn check-in")
    made = {"subject": subject, "cycle": cycle, "review": rk.review_of(cycle, subject)}
    rk.observe(subject, "Opened on time all week.", -5, "reliable", "strength")
    rk.observe(subject, "Covered a close at short notice.", -4, "reliable", "strength")
    rk.observe(subject, "Closing list left unsigned twice.", -3, "teamwork", "growth")
    rk.observe(subject, "A general note for the month.", -2, None)
    hidden = rk.observe(subject, "Hidden: the reviewer left this out.", -2, "reliable")
    rdb.toggle_observation(hidden, "Rae")
    rae = kit.open_as(world, "rae")
    post(rae, url(made, "/ai"))
    post(rae, url(made, "/rate"), {"dimension_key": "reliable", "level": "3",
                                   "comment": "On time and ready for every open I saw."})
    post(rae, url(made, "/rate"), {"dimension_key": "guest-care", "level": "not_enough"})
    post(rae, url(made, "/rate"), {"dimension_key": "teamwork", "level": "2",
                                   "comment": "Helps when asked."})
    post(rae, url(made, "/summary"), {"strengths": "Reliable opener.", "growth": "Hand over the close."})
    post(rae, url(made, "/actions/add"), {"action": "Sign the closing list", "measure": "Lists signed",
                                          "target": "Every close", "check_date": rk.day(14)})
    form = {f"gate_{key}": "no" for key in rdb.GATE_KEYS}
    post(rae, url(made, "/finalise"), {**form, "gate_leave": "yes"})
    op = kit.open_as(world, "operator")
    post(op, url(made, "/release"), {"note": "recheck"})
    post(rae, url(made, "/finalise"), {"changed": "no"})
    assert rdb.get_review(made["review"])["status"] == "final"
    post(op, f"/shift/reviews/calibration/{cycle}/change", {
        "review_id": made["review"], "dimension_key": "reliable", "level": "2",
        "reason": "Calibration-reason: zebra crossing."})
    assert "Shared." in post(rae, url(made, "/share"))
    return made


# --- calibration ----------------------------------------------------------------------

def test_the_calibration_page(world):
    ids = world["ids"]
    assert people_db.grant(ids["ada"], "Ada", "reviewer", "Operator")
    lena = rk.add_subject(world, "lena", "rae")
    hari = rk.add_subject(world, "hari", "rae")
    rae = rk.add_subject(world, "rae", "ada")
    rk.add_subject(world, "ada", None)
    cycle = rk.add_cycle()
    rk.observe(lena, "On time.", -3, "reliable")
    rk.observe(lena, "Stayed to finish.", -2, "reliable")
    for subject, levels in ((lena, (4, 4, 4)), (hari, (4, 3, None)), (rae, (1, 2, 3))):
        rid = rk.review_of(cycle, subject)
        for key, level in zip(rk.KEYS, levels):
            assert rdb.save_rating(rid, key, level, level is None, "", "x")
        rk.finalise(rid)
    html = main(page(kit.open_as(world, "operator"), f"/shift/reviews/calibration/{cycle}"))
    assert opened("calibration opened") == [("Operator", cycle)]
    head = re.findall(r'<th scope="col">([^<]+)</th>', html)
    assert head == ["Leader", "Reviewer", "Reliable", "Guest care", "Teamwork"]      # no total, no average
    rows = re.findall(r'<tr id="r-\d+">(.*?)</tr>', html, re.S)
    names = [re.search(r">([^<]+)</a>", row).group(1) for row in rows]
    assert names == ["Ada", "Hari", "Lena", "Rae"]                 # alphabetical, whatever the levels
    assert "Not finished yet" in rows[0] and "Operator" in rows[0]
    assert ">Rae</td>" in rows[1] and ">Ada</td>" in rows[3]       # the reviewer on each row
    cells = [re.sub(r"<span class=\"sr\">.*?</span>|<[^>]+>", " ", c).split()
             for c in re.findall(r'<td class="lvl">(.*?)</td>', rows[2], re.S)]
    assert cells == [["R4", "2", "notes"], ["R4", "0", "notes"], ["R4", "0", "notes"]]
    assert "–<span class=\"sr\"> Not enough to rate yet</span>" in rows[1] and "C3" in rows[1]
    assert "L1" in rows[3] and "D2" in rows[3] and "C3" in rows[3]
    # the two indicators, with their counts, as conversation starters
    assert "Conversation starters, not verdicts." in html
    rae_card = html[html.index('<span class="card-title">Rae</span>'):html.index('id="team"')]
    assert "5 of 5 levels are in the top two." in rae_card
    assert "Gives mostly the top two levels." in rae_card
    assert "In 1 of 1 review, nearly every standard got the same level." in rae_card
    assert "Gives nearly the same level across every standard." in rae_card
    ada_card = html[html.index('<span class="card-title">Ada</span>'):html.index('<span class="card-title">Rae</span>')]
    assert "1 of 3 levels are in the top two." in ada_card and "Gives mostly" not in ada_card
    # the team picture
    assert "Where most leaders are still getting there is where development time will pay off." in html
    team = html[html.index('id="team"'):html.index('id="change"')]
    assert team.count('<span class="card-title">') == 3 and "2 of 3" in team
    low = re.sub(r"<[^>]+>", " ", html).casefold()
    for word in ("average", "overall", "rank ", "ranked", "score", "top performer", "lowest"):
        assert word not in low, word
    assert "no totals and no ranking" in low


def test_a_calibration_change_needs_a_reason_and_is_logged(world):
    made = rk.one_review(world)
    other_cycle = rk.add_cycle("Summer", start=-200, end=-100, due=-90)
    op = kit.open_as(world, "operator")
    change = f"/shift/reviews/calibration/{made['cycle']}/change"
    form = {"review_id": made["review"], "dimension_key": "reliable", "level": "2", "reason": ""}
    rk.rate_all(made["review"], level=3)
    html = page(op, f"/shift/reviews/calibration/{made['cycle']}")
    assert "No review in this cycle is finalised" in html and 'name="reason"' not in html
    assert "Levels can be changed in calibration only while" in post(op, change, {**form, "reason": "Why"})
    rk.finalise(made["review"])
    assert 'name="reason"' in page(op, f"/shift/reviews/calibration/{made['cycle']}")
    assert "Give a reason for the change. It is kept with the review." in post(op, change, form)
    assert rdb.ratings(made["review"])["reliable"]["level"] == 3
    # the review has to belong to the cycle on the page
    assert "Choose a leader whose review is finalised." in post(
        op, f"/shift/reviews/calibration/{other_cycle}/change", {**form, "reason": "Why"})
    html = post(op, change, {**form, "reason": "The notes show reminders were needed."})
    assert "Level changed. The reason is kept with the review." in html
    row = rdb.ratings(made["review"])["reliable"]
    assert (row["level"], row["cal_from"], row["cal_by"], row["cal_reason"]) \
        == (2, 3, "Operator", "The notes show reminders were needed.")
    assert 'D2<span class="sr"> Developing</span>*<span class="sr"> changed in calibration</span>' in html
    logged = [r for r in people_db.audit_feed() if r["action"] == "level changed in calibration"]
    assert [(r["who"], r["object_id"], r["detail"]) for r in logged] == [
        ("Operator", made["review"], "reliable: 3 to 2")]
    # a reason with wording to keep out of reviews is saved, with a nudge
    html = post(op, change, {**form, "level": "not_enough", "reason": "Was off sick for most of it."})
    assert "Read your reason again" in html and rdb.ratings(made["review"])["reliable"]["not_enough"] == 1
    # the reviewer can see their level was changed, and why
    html = page(kit.open_as(world, "rae"), f"/shift/reviews/review/{made['review']}")
    assert "Changed in calibration by" in html and "The reviewer gave: Consistent." in html
    # never by a reviewer, and never once shared
    assert NOT_YOURS in post(kit.open_as(world, "rae"), change, {**form, "reason": "Mine"})
    rdb.share(made["review"], "Rae")
    assert "Levels can be changed in calibration only while" in post(op, change, {**form, "reason": "Late"})


# --- what the leader sees ---------------------------------------------------------------

def test_the_leader_reads_the_review_and_nothing_behind_it(world, monkeypatch):
    made = full_review(world, monkeypatch)
    lena = kit.open_as(world, "lena")
    assert "Read it and confirm" in page(lena, "/shift/reviews/mine")
    html = page(lena, mine(made))
    body = main(html)
    assert opened("own review opened") == [("Lena", made["review"])]
    model = people_db.active_model()
    reliable = body[body.index('id="d-reliable"'):body.index('id="d-guest-care"')]
    # the level (as calibrated) with what that level looks like, and only that level
    assert "Developing" in reliable and model["dimensions"][0]["anchors"][1] in reliable
    for other in (0, 2, 3):
        assert model["dimensions"][0]["anchors"][other] not in body
    assert "Consistent" not in reliable                         # the level before calibration
    assert "On time and ready for every open I saw." in reliable            # the reviewer's comment
    assert "Opened on time all week." in reliable and "Covered a close at short notice." in reliable
    guest = body[body.index('id="d-guest-care"'):body.index('id="d-teamwork"')]
    assert "Not enough to rate yet" in guest and "That is not a mark against you." in guest
    assert "Closing list left unsigned twice." in body and "A general note for the month." in body
    assert "Reliable opener." in body and "Hand over the close." in body
    assert "Sign the closing list" in body and "Measure: Lists signed" in body and "Target: Every close" in body
    assert "written by Rae" in body and "Autumn check-in" in body
    # never: AI output, the "before you finish" answers, the hold, calibration, drafts, left-out notes
    for hidden in (LABEL, "Summary for", "Suggested level", "AI", "Keeps the line moving",
                   "What would make the close easier", "Human check",
                   "Before you finish", "Yes or not sure", "on hold", "On hold", "released",
                   "Handled outside this tool", "calibration", "Calibration", "Calibration-reason",
                   "zebra", "Draft", "Finalised", "Hidden: the reviewer left this out.",
                   "Reopen", "Share with", "note 1"):
        assert hidden not in body, hidden
    for _, question in rdb.GATE:
        assert question not in html
    # the invitation sits above the confirmation, which is worded exactly, and promises no more
    assert SUPPORT in body and people_reviews.SUPPORT_TEXT == SUPPORT
    assert body.index(SUPPORT) < body.index("I confirm that I received this review")
    assert people_reviews.ACK_TEXT == ACK
    assert ACK.format(date="the date I enter below") in body
    assert len(re.findall(r"agree", html, re.I)) == 1 and "It does not mean I agree with the review." in html
    assert "Need it in another format" in body and "counts as paid time" in body


def test_nobody_else_opens_it_and_not_before_it_is_shared(world):
    ids = world["ids"]
    made = rk.one_review(world, ask_reflection=False)
    lena, hari, rae = (kit.open_as(world, who) for who in ("lena", "hari", "rae"))

    def shut(client):
        assert rk.sent_away(client, url(made, "/print"))
        resp = client.get(mine(made))
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/reviews/mine")
        for tail, data in (("/acknowledge", {"confirm": "1", "discussed_on": rk.day()}),
                           ("/reflection", {"r_reliable": "x", "send": "1"}), ("/actions/1/done", {})):
            assert DENIED in post(client, mine(made, tail), data), tail

    shut(lena)                                               # a draft is not hers to see
    assert "Nothing here yet." in page(lena, "/shift/reviews/mine")
    rk.rate_all(made["review"])
    assert rk.finalise(made["review"], pregnancy="yes") == "on_hold"
    shut(lena)                                               # nor a held one
    rdb.release(made["review"], "carry_on", "Operator")
    rdb.finalise(made["review"], None, "Rae", changed="no")
    shut(lena)                                               # nor a final one, until it is shared
    assert "Nothing here yet." in page(lena, "/shift/reviews/mine")
    rdb.share(made["review"], "Rae")
    assert page(lena, mine(made)) and "Read it and confirm" in page(lena, "/shift/reviews/mine")
    # another leader, the reviewer herself and the Operator have no "mine" for Lena's review
    shut(hari)
    for client in (rae, kit.open_as(world, "operator")):
        resp = client.get(mine(made))
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/reviews/mine")
        assert DENIED in post(client, mine(made, "/acknowledge"), {"confirm": "1", "discussed_on": rk.day()})
    assert "Nothing here yet." in page(kit.open_as(world, "operator"), "/shift/reviews/mine")
    assert rdb.get_review(made["review"])["status"] == "shared"
    # a link that no longer matches her sign-in fails closed
    with people_db.connect() as conn:
        conn.execute("UPDATE rv_subjects SET leader_id=? WHERE id=?", (ids["lena"] + 40, made["subject"]))
        conn.commit()
    shut(lena)
    with people_db.connect() as conn:
        conn.execute("UPDATE rv_subjects SET leader_id=?, leader_name='Lena Before' WHERE id=?",
                     (ids["lena"], made["subject"]))
        conn.commit()
    shut(lena)


def test_the_leader_confirms_receipt_and_discussion_only(world):
    made = rk.shared_review(world)
    lena = kit.open_as(world, "lena")
    assert "Tick the box to confirm, then save." in post(lena, mine(made, "/acknowledge"),
                                                         {"discussed_on": rk.day(-1)})
    assert "Enter the date the review was discussed with you." in post(
        lena, mine(made, "/acknowledge"), {"confirm": "1"})
    assert "That date hasn&#39;t happened yet." in post(
        lena, mine(made, "/acknowledge"), {"confirm": "1", "discussed_on": rk.day(3)})
    assert rdb.get_review(made["review"])["status"] == "shared"
    html = post(lena, mine(made, "/acknowledge"), {
        "confirm": "1", "discussed_on": rk.day(-1), "comment": "I see the close differently. Let's talk."})
    assert "Thank you. Your confirmation and comments are saved with the review." in html
    review = rdb.get_review(made["review"])
    assert (review["status"], review["discussed_on"]) == ("acknowledged", rk.day(-1))
    assert review["ack_comment"] == "I see the close differently. Let's talk." and review["ack_at"]
    body = main(html)
    assert ACK.format(date=cfa_app.app.jinja_env.filters["pp_day"](rk.day(-1))) in body
    assert "I see the close differently. Let&#39;s talk." in body and 'name="discussed_on"' not in body
    assert len(re.findall(r"agree", html, re.I)) == 1
    assert "You confirmed it" in page(lena, "/shift/reviews/mine")
    assert "This review isn&#39;t waiting for you to confirm it." in post(
        lena, mine(made, "/acknowledge"), {"confirm": "1", "discussed_on": rk.day(-2), "comment": "Changed."})
    assert rdb.get_review(made["review"])["ack_comment"].startswith("I see the close differently.")
    assert ("Lena", "review acknowledged", "review", made["review"]) in rk.audit_actions()
    # the reviewer and the Operator read the comments; receipt is not called agreement there either
    for who in ("rae", "operator"):
        html = page(kit.open_as(world, who), url(made))
        assert "I see the close differently." in html and "Acknowledged" in html
        assert "may raise a concern" not in html


def test_comments_that_may_raise_a_concern_flag_the_operator_and_never_stop_the_leader(world):
    made = rk.shared_review(world)
    lena = kit.open_as(world, "lena")
    html = post(lena, mine(made, "/acknowledge"), {
        "confirm": "1", "discussed_on": rk.day(-1),
        "comment": "I felt harassed on closes and I do not feel safe raising it."})
    assert "Thank you. Your confirmation and comments are saved with the review." in html     # never blocked
    assert rdb.get_review(made["review"])["status"] == "acknowledged"
    assert "concern" not in main(html) and "follow-up" not in main(html)       # nothing is said to her
    op = kit.open_as(world, "operator")
    html = page(op, url(made))
    comments = html[html.index('id="leader-comments"'):]
    assert "The leader&#39;s comments may raise a concern that needs follow-up" in comments
    assert "This tool does not decide discipline, pay, hours, demotion or dismissal" in comments
    assert "I felt harassed on closes" in comments
    assert "about you or another senior leader, or the leader is under 18, stop and call a lawyer first" in comments
    assert "Comments to follow up" in page(op, "/shift/reviews")
    # the flag is the Operator's: the reviewer sees the comments without it
    html = page(kit.open_as(world, "rae"), url(made))
    assert "I felt harassed on closes" in html and "may raise a concern" not in html
    assert "Comments to follow up" not in page(kit.open_as(world, "rae"), "/shift/reviews")


def test_the_leader_marks_a_next_step_done(world):
    made = rk.one_review(world)
    action, _ = rdb.add_action(made["review"], "Sign the closing list", "Lists signed", "Every close",
                               rk.day(7), "Rae")
    rk.rate_all(made["review"])
    rk.finalise(made["review"])
    rdb.share(made["review"], "Rae")
    lena = kit.open_as(world, "lena")
    assert "Mark done" in page(lena, mine(made))
    html = post(lena, mine(made, f"/actions/{action}/done"))
    assert "Marked done." in html and "Mark done" not in main(html)
    assert rdb.actions(made["review"])[0]["done_by"] == "Lena"
    assert "That was already marked done." in post(lena, mine(made, f"/actions/{action}/done"))
    assert "Done" in page(kit.open_as(world, "rae"), url(made))


# --- the leader's reflection ----------------------------------------------------------

def test_the_reflection_is_asked_for_written_and_sent(world, monkeypatch):
    made = rk.one_review(world)
    lena, rae = kit.open_as(world, "lena"), kit.open_as(world, "rae")
    assert "Waiting for you" in page(lena, "/shift/reviews/mine")
    html = page(lena, mine(made))
    body = main(html)
    assert "Your reflection" in body and body.count("<textarea") == 3
    assert "You don't need to share anything personal here" in body
    assert "<strong>Who reads this:</strong> your reviewer and the Operator." in body
    assert "If the store switches on its AI drafting tool for reviews" in body
    assert "counts as paid time" in body
    for hidden in ("Draft", "rating", "Rating", "level you"):
        assert hidden not in body, hidden
    # saved but not sent: the reviewer sees nothing yet
    html = post(lena, mine(made, "/reflection"), {"r_reliable": "I opened on time all month.", "send": "0"})
    assert "Saved. Only you can see it until you send it." in html
    assert "I opened on time all month.</textarea>" in html
    assert "I opened on time all month." not in page(rae, url(made))
    assert "Write something in at least one box first." in post(lena, mine(made, "/reflection"), {"send": "1"})
    html = post(lena, mine(made, "/reflection"), {"r_reliable": "I opened on time all month.",
                                                  "r_teamwork": "I trained two new starters.", "send": "1"})
    assert "Sent to your reviewer." in html and "Update my reflection" in html
    assert "Sent" in page(lena, "/shift/reviews/mine") and "Waiting for you" not in page(lena, "/shift/reviews/mine")
    # the reviewer reads it, labelled as the leader's own words
    html = page(rae, url(made))
    block = html[html.index('id="reflection"'):html.index("</details>", html.index('id="reflection"'))]
    assert "Lena's own reflection" in block and "Their own words" in block
    assert "I trained two new starters." in block and "<textarea" not in block
    # with AI switched on, the leader is told before they write
    kit.fake_ai(monkeypatch, draft_reply)
    people_db.set_setting("ai_reviews", "1", "Operator")
    body = main(page(lena, mine(made)))
    assert "An AI tool is also given it, with your name and team members' names taken out" in body
    assert "The AI does not decide anything. A person does." in body
    assert "If the store switches on" not in body


def test_a_hold_never_shows_on_the_leaders_side(world):
    made = rk.one_review(world)
    lena = kit.open_as(world, "lena")
    post(lena, mine(made, "/reflection"), {"r_reliable": "My own account.", "send": "1"})
    before = main(page(lena, mine(made)))
    listed = main(page(lena, "/shift/reviews/mine"))
    rk.rate_all(made["review"])
    assert rk.finalise(made["review"], health="yes") == "on_hold"
    # the page, the list and the menu count are exactly what they were
    assert main(page(lena, mine(made))) == before
    assert main(page(lena, "/shift/reviews/mine")) == listed
    post(lena, mine(made, "/reflection"), {"r_reliable": "Still mine.", "send": "1"})      # still hers to write
    assert rdb.reflections(made["review"]) == {"reliable": "Still mine."}
    assert people_reviews.nav_counts("leader", world["ids"]["lena"], "Lena")["mine"] == 0
    # once the review is written the reflection is read-only
    rdb.release(made["review"], "carry_on", "Operator")
    rdb.finalise(made["review"], None, "Rae", changed="no")
    body = main(page(lena, mine(made)))
    assert "Your reflection can't be changed any more." in body and "<textarea" not in body
    assert "Still mine." in body
    assert "Your reflection can&#39;t be changed any more." in post(
        lena, mine(made, "/reflection"), {"r_reliable": "Too late.", "send": "1"})


# --- the print view ---------------------------------------------------------------------

def test_the_print_view_carries_the_review_and_the_signature_block(world, monkeypatch):
    made = full_review(world, monkeypatch)
    lena, rae, op = (kit.open_as(world, who) for who in ("lena", "rae", "operator"))
    for client in (lena, rae, op):
        html = page(client, url(made, "/print"))
        body = main(html)
        assert "Opened on time all week." in body and "Reliable opener." in body
        assert "Sign the closing list" in body and "Developing" in body
        assert SUPPORT in body and ACK.format(date="______________") in body
        assert "Leader's signature" in body and "Reviewer's signature" in body and body.count(">Date<") == 2
        assert "Leader declined to sign; copy provided on" in body
        assert len(re.findall(r"agree", html, re.I)) == 1
        for hidden in (LABEL, "Summary for", "AI", "Yes or not sure", "on hold", "Handled outside",
                       "alibration", "zebra", "Hidden: the reviewer left this out."):
            assert hidden not in body, hidden
    assert [who for who, _ in opened("review print view opened")] == ["Operator", "Rae", "Lena"]
    # once confirmed in Team Hub, the printed copy says when and carries the comments
    post(lena, mine(made, "/acknowledge"), {"confirm": "1", "discussed_on": rk.day(-1),
                                            "comment": "Thanks for the examples."})
    body = main(page(rae, url(made, "/print")))
    assert ACK.format(date=cfa_app.app.jinja_env.filters["pp_day"](rk.day(-1))) in body and "Thanks for the examples." in body
    assert "Confirmed in Team Hub" in body
    # other leaders never get it
    for who in ("hari", "ada"):
        assert rk.sent_away(kit.open_as(world, who), url(made, "/print"))


def test_a_review_is_not_printed_before_it_is_finished(world):
    made = rk.one_review(world)
    rae, op, lena = (kit.open_as(world, who) for who in ("rae", "operator", "lena"))
    for client in (rae, op, lena):
        assert rk.sent_away(client, url(made, "/print"))             # a draft
    rk.rate_all(made["review"])
    rk.finalise(made["review"])
    assert page(rae, url(made, "/print")) and page(op, url(made, "/print"))
    assert rk.sent_away(lena, url(made, "/print"))                   # final, but not shared with her yet
    rdb.share(made["review"], "Rae")
    assert page(lena, url(made, "/print"))


# --- process health -----------------------------------------------------------------------

def test_process_health_puts_the_count_beside_every_percentage(world):
    op = kit.open_as(world, "operator")
    assert "No review cycle yet." in page(op, "/shift/reviews/health")
    lena = rk.add_subject(world, "lena", "rae")
    hari = rk.add_subject(world, "hari", "rae")
    cycle = rk.add_cycle("Autumn check-in")
    rk.observe(lena, "On time.", -3, "reliable")
    first = rk.review_of(cycle, lena)
    rk.rate_all(first, level=4)
    rdb.mark_ai_shown(first, {"reliable": 4, "guest-care": None, "teamwork": 2})
    rdb.add_action(first, "Line check", "Checks signed", "5 of 5", rk.day(-1), "Rae")
    rk.finalise(first)
    assert rdb.calibrate(first, "teamwork", 3, False, "Notes.", "Operator") is None
    html = main(page(op, "/shift/reviews/health"))
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    for line in ("Reviews finished 1 of 2 (50%)",
                 "Finished on or before the due date 1 of 1 (100%)",
                 "3 ratings on finished reviews.",
                 "1 note across 3 levels given (about 0.3 each)",
                 "Levels given with no notes behind them 2 of 3 (67%)",
                 "Reviewer's own level the same as the AI suggestion 1 of 3 (33%)",
                 "AI said there was not enough evidence 1 of 3 (33%)",
                 "minutes before the review was finished 3 of 3 (100%)",
                 "Levels changed in calibration 1 of 3 (33%) · 0 up, 1 down",
                 "Agreed actions done by their check date 0 of 1 (0%)"):
        assert line in text, line
    assert not re.search(r"\d+%", re.sub(r"\d+ of \d+ \(\d+%\)", "", text))       # never a bare percentage
    assert "Autumn check-in" in text and hari
    # a second cycle gives a chooser; an unknown one is not there
    older = rk.add_cycle("Summer", start=-200, end=-100, due=-90)
    html = page(op, f"/shift/reviews/health?cycle={older}")
    assert 'aria-label="Choose a cycle"' in html and "Reviews finished 0 of 2 (0%)" in " ".join(
        re.sub(r"<[^>]+>", " ", html).split())
    assert rk.sent_away(op, "/shift/reviews/health?cycle=999")
    assert "No AI suggestion was shown on a finished review in this cycle." in html


# --- house rules ----------------------------------------------------------------------------

def test_every_review_page_keeps_the_strict_headers_and_runs_no_scripts(world, monkeypatch):
    made = full_review(world, monkeypatch)
    draft = rk.one_review(world, "hari", "rae", name="Second cycle")
    rk.observe(draft["subject"], "<script>alert(1)</script> <b>bold</b> & more", -1, "reliable")
    rk.rate_all(draft["review"])
    hub_csp = cfa_app.app.test_client().get("/hub/login").headers["Content-Security-Policy"]
    pages = {
        "operator": ["/shift/reviews", "/shift/reviews/leaders", f"/shift/reviews/leaders/{made['subject']}",
                     f"/shift/reviews/leaders/{draft['subject']}", f"/shift/reviews/cycles/{made['cycle']}",
                     url(made), url(draft), url(draft, "/finalise"), url(made, "/print"),
                     f"/shift/reviews/calibration/{made['cycle']}", "/shift/reviews/health",
                     "/shift/reviews/mine"],
        "rae": ["/shift/reviews", f"/shift/reviews/leaders/{draft['subject']}", url(made), url(draft),
                url(draft, "/finalise"), url(made, "/print"), "/shift/reviews/mine"],
        "lena": ["/shift/reviews/mine", mine(made), url(made, "/print")],
        "hari": ["/shift/reviews/mine", mine(draft)],
    }
    seen = 0
    for who, paths in pages.items():
        client = kit.open_as(world, who)
        for path in paths:
            resp = client.get(path)
            name = f"{who} {path}"
            assert resp.status_code == 200, name
            assert resp.headers["Cache-Control"] == "private, no-store", name
            assert resp.headers["X-Content-Type-Options"] == "nosniff", name
            assert resp.headers["Referrer-Policy"] == "same-origin", name
            assert resp.headers["X-Frame-Options"] == "DENY", name
            assert resp.headers["Content-Security-Policy"] == hub_csp, name
            html = resp.data.decode()
            assert "<script" not in html.lower(), name
            assert not re.search(r"<[^>]+\son[a-z]+\s*=", html), name
            assert "javascript:" not in html.lower(), name
            assert not re.search(r'(?:src|href)="https?://', html), name
            assert re.search(r'<section class="hero compact">\s*<p class="eyebrow">[^<]+</p>\s*'
                             r'<h1 class="headline">[^<]+</h1>\s*<p class="triage">', html), name
            assert '<nav class="tabbar"' in html, name
            seen += 1
    assert seen == 24
    # what a person typed is shown as text, never as markup
    html = page(kit.open_as(world, "rae"), url(draft))
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;bold&lt;/b&gt; &amp; more" in html
    # the templates themselves: no scripts, no handlers, nothing marked safe, no outside assets
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "templates", "people", "reviews")
    names = sorted(n for n in os.listdir(folder) if n.endswith(".html"))
    assert len(names) >= 12
    for name in names:
        text = open(os.path.join(folder, name), encoding="utf-8").read()
        assert "<script" not in text and "|safe" not in text and "| safe" not in text, name
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text) and "http://" not in text \
            and "https://" not in text, name


def test_every_open_is_written_in_the_activity_log(world):
    made = rk.shared_review(world)
    op, rae, lena = (kit.open_as(world, who) for who in ("operator", "rae", "lena"))
    before = len(people_db.audit_feed())
    page(rae, f"/shift/reviews/leaders/{made['subject']}")
    page(rae, url(made))
    page(op, url(made))
    page(op, f"/shift/reviews/calibration/{made['cycle']}")
    page(lena, mine(made))
    page(lena, url(made, "/print"))
    rows = people_db.audit_feed()[:len(people_db.audit_feed()) - before]
    assert [(r["who"], r["action"], r["object_type"], r["object_id"]) for r in reversed(rows)] == [
        ("Rae", "observation log opened", "leader under review", made["subject"]),
        ("Rae", "review opened", "review", made["review"]),
        ("Operator", "review opened", "review", made["review"]),
        ("Operator", "calibration opened", "review cycle", made["cycle"]),
        ("Lena", "own review opened", "review", made["review"]),
        ("Lena", "review print view opened", "review", made["review"])]
    assert all(r["at"] and r["detail"] == "" for r in rows)
    # a page that was refused leaves no "opened" line
    kit.open_as(world, "hari").get(url(made))
    assert len([r for r in people_db.audit_feed() if r["action"] == "review opened"]) == 2
    # and the Operator can read it all on the activity log page
    assert "review opened" in page(op, "/shift/people/audit")


def test_people_home_links_only_to_review_pages_that_exist(world, monkeypatch):
    """If the reviews feature did not load, nothing may point at its pages."""
    rk.one_review(world)
    monkeypatch.setattr(people, "_page_url", lambda path: None)
    for who in ("operator", "rae", "lena"):
        html = page(kit.open_as(world, who), "/shift/people")
        assert "/shift/reviews" not in html, who
    assert "My review" not in page(kit.open_as(world, "lena"), "/shift/more")


def test_menu_counts_never_raise_and_never_cross_people(world, monkeypatch, tmp_path):
    ids = world["ids"]
    made = rk.one_review(world, due=-1)
    assert people_reviews.nav_counts("operator", None, "Operator") == {"on_hold": 0, "past_due": 1, "drafts": 0}
    assert people_reviews.nav_counts("leader", ids["rae"], "Rae") == {"drafts": 1, "mine": 0}
    assert people_reviews.nav_counts("leader", ids["lena"], "Lena") == {"drafts": 0, "mine": 1}
    assert people_reviews.nav_counts("leader", ids["ada"], "Ada") == {"drafts": 0, "mine": 0}
    # a store that was never set up is made on the way, not a crash on every Team Hub page
    monkeypatch.setenv("PEOPLE_DB_PATH", str(tmp_path / "fresh" / "people.db"))
    os.makedirs(tmp_path / "fresh")
    assert people_reviews.nav_counts("leader", ids["lena"], "Lena") == {"drafts": 0, "mine": 0}
    assert made and json.dumps(people_reviews.nav_counts("operator", None, "Operator"))
