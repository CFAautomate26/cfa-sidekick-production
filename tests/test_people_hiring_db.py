"""Hiring's data layer: the publish check, the exact copies kept of every
posting, applying, the quick questions, stages, scores, the 45-day notice
clock, the numbers on the dashboard and the purge. Everyone here is
invented."""

import json
from datetime import date, datetime

import pytest

import people_db
import people_hiring_db as hdb
import shift_db
from tests import people_testkit as kit

DESCRIPTION = ("Serve guests at the counter and in the dining room, keep your station clean and "
               "stocked, and help your teammates through the rush. You will be trained on every "
               "station you work. Shifts are mornings, afternoons and evenings, six days a week.")


@pytest.fixture()
def world(monkeypatch, tmp_path):
    w = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    return w


def ai_in_use(monkeypatch) -> None:
    """The Operator's switch is on and the server has a key: AI can run."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert people_db.set_setting("ai_hiring", "1", "Operator")


def clock(monkeypatch, stamp: str) -> None:
    """Set the store's clock: '2026-11-20 10:00'."""
    fixed = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=shift_db.STORE_TZ)
    monkeypatch.setattr(shift_db, "now_local", lambda: fixed)


def draft(**changes) -> int:
    fields = {"title": "Team member", "employment_type": "part-time", "description": DESCRIPTION,
              "pay_min": "17.95", "pay_max": "19.50", "vacancy": "existing"}
    fields.update(changes)
    posting_id, problems = hdb.create_posting(fields, "Operator")
    assert not problems, problems
    return posting_id


def criterion(posting_id: int, name: str, question: str = "?", **more) -> None:
    raw = {"name": name, "definition": f"What {name.lower()} looks like on a shift.",
           "anchors": ["Level one.", "Level two.", "Level three.", "Level four."],
           "question": f"Tell us about a time you showed {name.lower()}." if question == "?" else question}
    raw.update(more)
    assert hdb.add_criterion(posting_id, raw, "Operator") == []


def live(**changes) -> int:
    posting_id = draft(**changes)
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    assert hdb.publish(posting_id, "Operator") == []
    return posting_id


def live_with_ai(monkeypatch, **changes) -> int:
    """A live posting that has said "AI is used" since the day it went
    live: AI assist was switched on while it was a draft, which is the
    only time it can be."""
    ai_in_use(monkeypatch)
    posting_id = draft(**changes)
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    assert hdb.set_ai(posting_id, True, "Operator")
    assert hdb.publish(posting_id, "Operator") == []
    return posting_id


def person(n: int = 1, **changes) -> dict:
    form = {"first_name": f"Jordan{n}", "last_name": "Sample", "email": f"jordan{n}@example.com",
            "phone": "", "work_entitled": "yes", "adult": "yes", "heard_from": "school",
            "av_mon_morning": "1", "av_sat_evening": "1", "experience": "Volunteered at a food bank.",
            "consent": "1"}
    form.update(changes)
    return form


def applied(posting_id: int, n: int = 1, **changes) -> int:
    result = hdb.apply(posting_id, person(n, **changes))
    assert result["status"] == "created", result
    return result["id"]


def table(name: str) -> list[dict]:
    with people_db.connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY id")]


# --- the store ----------------------------------------------------------------

def test_hiring_tables_live_in_the_people_file_and_never_in_the_shift_backup(world):
    posting_id = live()
    applied(posting_id)
    with people_db.connect() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ours = {"hr_postings", "hr_criteria", "hr_snapshots", "hr_applications", "hr_answers",
            "hr_events", "hr_scores", "hr_interviews", "hr_notices", "hr_notes"}
    assert ours <= tables
    backup = shift_db.export_json()
    payload = json.loads(backup)
    for name in ours:
        assert name not in payload and name not in shift_db.EXPORT_TABLES, name
    assert "Jordan1" not in backup and "jordan1@example.com" not in backup
    assert set(json.loads(people_db.export_json("Operator"))["tables"]) >= ours


# --- the publish check ----------------------------------------------------------

def problems_for(**changes) -> list[str]:
    posting_id = draft(**changes)
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    return hdb.compliance_problems(hdb.get_posting(posting_id))


def test_a_complete_posting_passes_and_goes_live(world):
    posting_id = live()
    posting = hdb.get_posting(posting_id)
    assert posting["status"] == "open" and posting["published_by"] == "Operator"
    assert posting["slug"] == "team-member" and hdb.posting_by_slug("team-member")["id"] == posting_id
    assert hdb.get_posting(draft())["slug"] == "team-member-2"       # the address is never reused
    assert problems_for() == []


@pytest.mark.parametrize("changes, said", [
    ({"description": "Too short."}, "at least 200 characters"),
    ({"pay_min": "", "pay_max": ""}, "both pay figures"),
    ({"pay_max": ""}, "both pay figures"),
    ({"pay_min": "20.00", "pay_max": "18.00"}, "can't be more than the highest"),
    ({"pay_min": "17.94"}, "below Ontario's general minimum wage ($17.95"),
    ({"vacancy": ""}, "existing vacancy or for future openings"),
])
def test_each_missing_item_blocks_publishing(world, changes, said):
    posting_id = draft(**changes)
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    problems = hdb.compliance_problems(hdb.get_posting(posting_id))
    assert len(problems) == 1 and said in problems[0], problems
    assert hdb.publish(posting_id, "Operator") == problems
    assert hdb.get_posting(posting_id)["status"] == "draft" and not hdb.snapshots(posting_id)


def test_a_posting_needs_a_title_a_named_contact_and_two_to_six_questions(world):
    assert hdb.create_posting({"title": "  "}, "Operator") == (None, ["Give the posting a title."])
    assert hdb.create_posting({"title": "Cook", "pay_min": "lots"}, "Operator")[1] == [
        "The lowest pay needs a dollar amount like 17.95."]
    posting_id = draft()
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Teamwork", question="")                   # interview only: not a question
    assert any("at least 2 criteria" in p for p in hdb.compliance_problems(hdb.get_posting(posting_id)))
    for n in range(6):
        criterion(posting_id, f"Standard {n}")
    assert any("6 questions at most" in p for p in hdb.compliance_problems(hdb.get_posting(posting_id)))
    # the accommodation notice needs a named person and a way to reach them
    people_db.set_setting("hiring_contact_email", "", "Operator")
    assert any("hiring contact" in p for p in problems_for())
    people_db.set_setting("hiring_contact_phone", "519-555-0100", "Operator")
    assert problems_for() == []
    people_db.set_setting("hiring_contact_name", "", "Operator")
    assert any("named person" in p for p in problems_for())


def test_the_pay_range_can_be_fifty_thousand_dollars_a_year_wide_and_no_wider(world):
    assert problems_for(pay_min="17.95", pay_max="41.98") == []           # 24.03 x 2,080 = 49,982.40
    problems = problems_for(pay_min="17.95", pay_max="41.99")             # 24.04 x 2,080 = 50,003.20
    assert len(problems) == 1 and "$50,000 a year" in problems[0]


def test_pay_below_minimum_wage_needs_the_student_tick_and_the_student_rate(world):
    assert "general minimum wage" in problems_for(pay_min="16.90")[0]
    assert problems_for(pay_min="16.90", student_rate="1") == []
    assert "student minimum wage ($16.90" in problems_for(pay_min="16.89", student_rate="1")[0]
    # everyone who is not on the student rate must still be paid the general minimum
    assert "highest pay must still be at least" in problems_for(
        pay_min="16.90", pay_max="17.50", student_rate="1")[0]
    # the wage floors are settings, not constants
    people_db.set_setting("min_wage_general", "18.40", "Operator")
    assert "$18.40" in problems_for()[0]
    people_db.set_setting("min_wage_student", "17.30", "Operator")
    assert "$17.30" in problems_for(pay_min="16.90", student_rate="1")[0]


def test_the_student_line_is_printed_only_when_it_is_true(world):
    view = hdb.public_view(hdb.get_posting(draft(pay_min="16.90", student_rate="1")))
    assert view["pay"] == "$16.90 to $19.50 an hour" and view["student_line"] == hdb.STUDENT_MINIMUM_LINE
    assert "students under 18 who work 28 hours a week or less" in view["student_line"]
    assert "students only" not in hdb.document_text(view).lower()
    between = hdb.public_view(hdb.get_posting(draft(pay_min="17.20", student_rate="1")))
    assert between["student_line"] == hdb.STUDENT_RATE_LINE and "minimum wage" not in between["student_line"]
    ticked_for_nothing = hdb.public_view(hdb.get_posting(draft(pay_min="18.00", student_rate="1")))
    assert ticked_for_nothing["student_line"] == ""
    assert hdb.public_view(hdb.get_posting(draft(pay_min="18", pay_max="18")))["pay"] == "$18.00 an hour"


@pytest.mark.parametrize("text, term, key", [
    ("Canadian experience is an asset.", "canadian experience", "canadian-experience"),
    ("We want young, energetic people.", "young", "age"),
    ("Perfect for a recent grad.", "recent grad", "age"),
    ("Students only.", "students only", "age"),
    ("Must be a native English speaker.", "native english speaker", "language"),
    ("Hiring a hostess for evenings.", "hostess", "gendered"),
    ("You must be able-bodied.", "able-bodied", "physical"),
    ("Great tips on top of your wage.", "tips", "pay"),
    ("A yearly bonus is paid.", "bonus", "pay"),
])
def test_wording_flags_find_words_to_read_again(world, text, term, key):
    posting = hdb.get_posting(draft(description=DESCRIPTION + " " + text))
    flags = hdb.wording_flags(posting)
    assert {"term": term, "key": key} in [{"term": f["term"], "key": f["key"]} for f in flags], flags
    assert all(f["advice"] for f in flags)
    if key == "pay":
        assert "Posted pay means wages. Tips and bonuses can't be used to state it." in [
            f["advice"] for f in flags]


def test_clean_wording_raises_no_flags_and_flags_cover_the_criteria_too(world):
    posting_id = draft()
    assert hdb.wording_flags(hdb.get_posting(posting_id)) == []
    criterion(posting_id, "Mature judgement")
    assert [f["term"] for f in hdb.wording_flags(hdb.get_posting(posting_id))] == ["mature"]
    # whole words only: "outfit" and "tipping point" are not "fit" and "tip"
    other = draft(description=DESCRIPTION + " Your outfit is supplied. Benefits start at once.")
    assert hdb.wording_flags(hdb.get_posting(other)) == []


def test_flagged_words_never_block_alone_but_publishing_needs_the_tick(world):
    posting_id = draft(description=DESCRIPTION + " A strong team player.")
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    assert hdb.compliance_problems(hdb.get_posting(posting_id)) == []
    refused = hdb.publish(posting_id, "Operator")
    assert len(refused) == 1 and "tick the box" in refused[0]
    assert hdb.get_posting(posting_id)["status"] == "draft"
    assert hdb.publish(posting_id, "Operator", reviewed=True) == []
    assert hdb.get_posting(posting_id)["wording_reviewed"] == 1


# --- what a posting says, and the copies kept -------------------------------------

def test_a_posting_shows_every_required_statement_in_order(world):
    posting = hdb.get_posting(live(vacancy="future"))
    view = hdb.public_view(posting)
    text = hdb.document_text(view)
    parts = ["Team member", "Chick-fil-A Wharncliffe & Wonderland", "Part-time",
             "$17.95 to $19.50 an hour", DESCRIPTION, "What we look for", "Reliable",
             "This posting is not for an existing vacancy. We are collecting applications for future openings.",
             "welcomes applications from people with disabilities",
             "contact Morgan Example at hiring@example.com",
             "asking for an accommodation will not affect how your application is assessed",
             "Ontario law sets a minimum age of 14 to work in a restaurant's serving areas and 15 in its kitchen.",
             "APPLICATION FORM (blank)", "Are you legally entitled to work in Canada?",
             "Are you 18 or older?", "It is not used to assess your application.",
             "We do not sell your information or use it for marketing.",
             "for 24 months after the hiring decision", "for 3 years",
             "read this with a parent or guardian",
             "Tell us about a time you showed reliable."]
    at = -1
    for part in parts:
        found = text.find(part, at + 1)
        assert found > at, part
        at = found
    assert hdb.public_view(hdb.get_posting(live(title="Kitchen team")))["vacancy_statement"] == (
        "This posting is for an existing vacancy.")
    # no AI on this posting: nothing about AI is said at all, never "no AI is used"
    assert "artificial intelligence" not in text and " AI " not in text and "No AI" not in text


def test_the_ai_statement_is_shown_only_while_ai_assist_is_on(world, monkeypatch):
    posting_id = draft()                         # AI assist is switched on while a posting is a draft
    criterion(posting_id, "Reliable")
    criterion(posting_id, "Guest care")
    assert hdb.public_view(hdb.get_posting(posting_id))["ai_statement"] == ""
    assert hdb.set_ai(posting_id, True, "Operator") and not hdb.set_ai(posting_id, True, "Operator")
    # on for the posting is not enough: the statement is only made while AI can really run
    assert hdb.public_view(hdb.get_posting(posting_id))["ai_statement"] == ""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert hdb.public_view(hdb.get_posting(posting_id))["ai_statement"] == ""         # the switch is off
    people_db.set_setting("ai_hiring", "1", "Operator")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert hdb.public_view(hdb.get_posting(posting_id))["ai_statement"] == ""         # no key on the server
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    view = hdb.public_view(hdb.get_posting(posting_id))
    assert view["ai_statement"].startswith("We use artificial intelligence (AI) to help assess applications")
    # it promises what the tool enforces (a person makes every decision) and nothing it does not
    assert view["ai_statement"].endswith("A member of our leadership team makes every decision.")
    assert "reads every application" not in view["ai_statement"]
    assert any("may be sent to an AI service" in line and "may process them outside Canada" in line
               and "Your name and contact details are not sent with them." in line for line in view["privacy"])
    text = hdb.document_text(view)
    assert text.count(hdb.AI_STATEMENT) == 2                       # on the posting and on the form
    assert hdb.set_ai(posting_id, False, "Operator")
    assert not any("AI service" in line for line in hdb.public_view(hdb.get_posting(posting_id))["privacy"])


def test_an_exact_copy_is_kept_on_publish_on_every_change_and_on_close(world, monkeypatch):
    posting_id = live_with_ai(monkeypatch)
    copies = hdb.snapshots(posting_id)
    assert [(c["reason"], c["ai_statement"]) for c in copies] == [("published", 1)]
    first = hdb.get_snapshot(copies[0]["id"])["body"]
    assert DESCRIPTION in first and "APPLICATION FORM (blank)" in first
    # a change to the live posting
    assert hdb.save_posting(posting_id, {"title": "Team member", "description": DESCRIPTION + " Free meal on shift.",
                                         "pay_min": "18.00", "pay_max": "19.50", "vacancy": "existing"},
                            "Hari") == []
    # a change to its criteria
    criterion(posting_id, "Teamwork")
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.save_criterion(crit["id"], posting_id, {**crit, "definition": "Shows up ready."}, "Hari") == []
    # switching AI off takes the AI statement out of the new copy, and it can't be switched back on
    assert hdb.set_ai(posting_id, False, "Operator") and not hdb.set_ai(posting_id, True, "Operator")
    assert hdb.close_posting(posting_id, "Operator") and not hdb.close_posting(posting_id, "Operator")
    copies = hdb.snapshots(posting_id)
    assert [(c["reason"], c["ai_statement"]) for c in copies] == [
        ("published", 1), ("changed", 1), ("changed", 1), ("changed", 1), ("changed", 0), ("closed", 0)]
    assert hdb.get_snapshot(copies[0]["id"])["body"] == first      # earlier copies never change
    assert "Free meal on shift." in hdb.get_snapshot(copies[1]["id"])["body"]
    assert "$18.00 to $19.50 an hour" in hdb.get_snapshot(copies[1]["id"])["body"]
    assert hdb.AI_STATEMENT in hdb.get_snapshot(copies[3]["id"])["body"]
    assert hdb.AI_STATEMENT not in hdb.get_snapshot(copies[4]["id"])["body"]
    posting = hdb.get_posting(posting_id)
    assert posting["status"] == "closed" and posting["closed_at"] and posting["closed_by"] == "Operator"
    # a draft keeps no copies: nothing was shown to anyone
    other = draft()
    criterion(other, "Reliable")
    assert hdb.snapshots(other) == []


def test_a_live_posting_cannot_be_changed_into_one_that_fails_the_check(world):
    posting_id = live()
    refused = hdb.save_posting(posting_id, {"title": "Team member", "description": "Short.",
                                            "pay_min": "17.95", "pay_max": "19.50", "vacancy": "existing"},
                               "Hari")
    assert refused[0].startswith("This posting is live") and "200 characters" in refused[1]
    assert hdb.get_posting(posting_id)["description"] == DESCRIPTION
    crit = hdb.get_posting(posting_id)["criteria"][0]
    assert hdb.remove_criterion(crit["id"], posting_id, "Hari")[0].startswith("This posting is live")
    assert len(hdb.get_posting(posting_id)["criteria"]) == 2
    # a new flagged word on a live posting needs the tick; without it nothing changes
    wordy = {"title": "Team member", "description": DESCRIPTION + " Must be energetic.",
             "pay_min": "17.95", "pay_max": "19.50", "vacancy": "existing"}
    refused = hdb.save_posting(posting_id, wordy, "Hari")
    assert len(refused) == 1 and "energetic" in refused[0]
    assert len(hdb.snapshots(posting_id)) == 1
    assert hdb.save_posting(posting_id, wordy, "Hari", reviewed=True) == []
    assert len(hdb.snapshots(posting_id)) == 2


def test_a_closed_posting_is_never_changed_or_deleted_but_can_be_copied(world):
    posting_id = live()
    hdb.close_posting(posting_id, "Operator")
    fields = {"title": "Changed", "description": DESCRIPTION, "pay_min": "17.95", "pay_max": "19.50",
              "vacancy": "existing"}
    assert "closed" in hdb.save_posting(posting_id, fields, "Hari")[0]
    assert "closed" in hdb.add_criterion(posting_id, {"name": "New", "definition": "New."}, "Hari")[0]
    assert not hdb.set_ai(posting_id, True, "Operator")
    assert hdb.publish(posting_id, "Operator") == ["Only a draft can be published."]
    assert hdb.get_posting(posting_id)["title"] == "Team member"
    copy_id = hdb.copy_posting(posting_id, "Hari")
    copy = hdb.get_posting(copy_id)
    assert copy["status"] == "draft" and copy["slug"] == "team-member-2" and len(copy["criteria"]) == 2
    assert not hasattr(hdb, "delete_posting") and not hasattr(hdb, "delete_snapshot")


def test_criteria_can_come_from_our_standards_once_each(world):
    posting_id = draft()
    dim = people_db.active_model()["dimensions"][0]
    raw = {"name": dim["name"], "definition": dim["definition"], "dimension_key": dim["key"],
           "question": dim["hiring"]["question"], "anchors": dim["hiring"]["anchors"]}
    assert hdb.add_criterion(posting_id, raw, "Hari") == []
    assert "already" in hdb.add_criterion(posting_id, raw, "Hari")[0]
    saved = hdb.get_posting(posting_id)["criteria"][0]
    assert saved["dimension_key"] == "reliable" and saved["anchors"] == dim["hiring"]["anchors"]
    assert hdb.add_criterion(posting_id, {"name": "", "definition": ""}, "Hari") == [
        "Give the criterion a name.", "Say what the criterion means, in a sentence about the job."]


# --- applying -------------------------------------------------------------------

def test_step_one_keeps_only_what_the_form_asks(world):
    posting_id = live()
    result = hdb.apply(posting_id, person(phone="(519) 555-0142", heard_from="friend", referrer="Alex Doe",
                                          date_of_birth="2009-01-01", gender="x", sin="123"))
    assert result["status"] == "created" and len(result["token"]) == 32
    row = table("hr_applications")[0]
    assert (row["first_name"], row["last_name"], row["stage"]) == ("Jordan1", "Sample", "new")
    assert json.loads(row["availability"]) == {"mon": ["morning"], "sat": ["evening"]}
    assert row["heard_from"] == "friend" and row["referrer"] == "Alex Doe" and row["keep_months"] == 24
    assert row["consent_at"] == row["created_at"] and row["ai_disclosed"] == 0
    assert "date_of_birth" not in row and "gender" not in row and "sin" not in row
    # the private link is never stored, only its keyed hash
    assert row["token_hash"] == people_db.token_hash(result["token"])
    assert result["token"] not in people_db.export_json("Operator")
    assert "jordan1@example.com" not in row["contact_key"] and "5195550142" not in row["contact_key"]
    events = table("hr_events")
    assert [(e["kind"], e["by"], e["to_stage"]) for e in events] == [("applied", "applicant", "new")]


@pytest.mark.parametrize("changes, said", [
    ({"first_name": ""}, "Enter your first name."),
    ({"last_name": "  "}, "Enter your last name."),
    ({"email": "", "phone": ""}, "Give us a phone number or an email so we can reach you."),
    ({"email": "not-an-email"}, "That email doesn't look right. Check it and try again."),
    ({"email": "a?cc=b@evil.example"}, "That email doesn't look right. Check it and try again."),
    ({"phone": "555-0100"}, "That phone number needs 10 to 15 digits."),
    ({"phone": "call me maybe 5195550100"}, "That phone number needs 10 to 15 digits."),
    ({"work_entitled": ""}, "Answer yes or no: are you legally entitled to work in Canada?"),
    ({"adult": "maybe"}, "Answer yes or no: are you 18 or older?"),
    ({"consent": ""}, "Tick the box to say you have read how we look after your information."),
])
def test_step_one_says_what_is_missing_and_keeps_nothing(world, changes, said):
    posting_id = live()
    result = hdb.apply(posting_id, person(**changes))
    assert result["status"] == "invalid" and result["problems"] == [said]
    assert table("hr_applications") == [] and table("hr_events") == []


def test_step_one_caps_and_cleans_every_field(world):
    posting_id = live()
    hdb.apply(posting_id, person(first_name="J" * 200, experience="x" * 5000 + "‮",
                                 heard_from="made-up", referrer="Someone"))
    row = table("hr_applications")[0]
    assert len(row["first_name"]) == 60 and len(row["experience"]) == 1000
    assert row["heard_from"] == "" and row["referrer"] == ""         # a referrer only with "a friend"


def test_the_same_details_again_within_thirty_days_are_kept_and_flagged_for_a_leader(world, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    posting_id, other = live(), live(title="Kitchen team")
    first = applied(posting_id, phone="519-555-0142")
    # never refused and never told apart: a different answer here would tell a stranger who has applied
    by_email = hdb.apply(posting_id, person(email="JORDAN1@example.com"))
    by_phone = hdb.apply(posting_id, person(2, phone="+1 (519) 555 0142"))
    assert by_email["status"] == by_phone["status"] == "created" and by_email["token"] != by_phone["token"]
    elsewhere = hdb.apply(other, person())                            # another posting
    someone_else = hdb.apply(posting_id, person(3))                   # another person
    clock(monkeypatch, "2026-12-02 09:01")                            # thirty-one days on
    much_later = hdb.apply(posting_id, person())
    flags = {r["id"]: r["duplicate_of"] for r in table("hr_applications")}
    assert flags == {first: None, by_email["id"]: first, by_phone["id"]: first, elsewhere["id"]: None,
                     someone_else["id"]: None, much_later["id"]: None}
    assert [e["detail"] for e in table("hr_events") if e["kind"] == "applied"] == [
        "", "possible duplicate", "possible duplicate", "", "", ""]
    # each side of the pair points at the others, by id only
    assert hdb.candidate(first)["same_contact"] == [by_email["id"], by_phone["id"]]
    assert hdb.candidate(by_phone["id"])["same_contact"] == [first, by_email["id"]]
    assert hdb.candidate(someone_else["id"])["same_contact"] == []


def test_nobody_can_apply_to_a_posting_that_is_not_open(world):
    posting_id = draft()
    assert hdb.apply(posting_id, person())["status"] == "closed"
    posting_id = live()
    hdb.close_posting(posting_id, "Operator")
    assert hdb.apply(posting_id, person())["status"] == "closed"
    assert table("hr_applications") == []


def test_ai_disclosed_is_recorded_only_when_the_applicant_was_shown_the_statement(world, monkeypatch):
    ai_in_use(monkeypatch)
    posting_id = live()
    without = hdb.apply(posting_id, person(1), ai_shown=True)["id"]   # the posting has no AI
    assert not hdb.set_ai(posting_id, True, "Operator")               # and can't start once it is live
    still_without = hdb.apply(posting_id, person(2), ai_shown=True)["id"]
    with_ai = live_with_ai(monkeypatch, title="Kitchen team")
    stale_form = hdb.apply(with_ai, person(3), ai_shown=False)["id"]  # a form that did not show the statement
    shown = hdb.apply(with_ai, person(4), ai_shown=True)["id"]
    people_db.set_setting("ai_hiring", "0", "Operator")              # the Operator turns AI off again
    switched_off = hdb.apply(with_ai, person(5), ai_shown=True)["id"]
    people_db.set_setting("ai_hiring", "1", "Operator")
    monkeypatch.delenv("OPENAI_API_KEY")                              # or the server loses its key
    no_key = hdb.apply(with_ai, person(6), ai_shown=True)["id"]
    flags = {r["id"]: r["ai_disclosed"] for r in table("hr_applications")}
    assert flags == {without: 0, still_without: 0, stale_form: 0, shown: 1, switched_off: 0, no_key: 0}


# --- the private link -------------------------------------------------------------

def test_the_private_page_takes_answers_once_and_then_shows_status(world, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    posting_id = live()
    token = hdb.apply(posting_id, person())["token"]
    mine = hdb.application_by_token(token)
    assert mine["title"] == "Team member" and mine["created_at"] == "2026-11-01 09:00"
    assert mine["can_answer"] and len(mine["questions"]) == 2 and mine["decision"] is None
    assert mine["contact"] == "Morgan Example at hiring@example.com"
    assert set(mine) == {"id", "posting_id", "created_at", "answers_until", "answered_at", "title",
                         "questions", "can_answer", "decision", "contact", "employer"}
    first, second = (q["id"] for q in mine["questions"])
    assert hdb.save_answers(mine["id"], {first: "   ", 999: "not a question"}) == "empty"
    assert hdb.save_answers(mine["id"], {first: "I opened the shop every Saturday.", second: "y" * 3000,
                                         999: "not a question"}) == "saved"
    answers = {a["criterion_id"]: a["body"] for a in table("hr_answers")}
    assert answers[first] == "I opened the shop every Saturday." and len(answers[second]) == 1500
    assert set(answers) == {first, second}
    assert hdb.save_answers(mine["id"], {first: "Changed my mind."}) == "closed"      # once only
    after = hdb.application_by_token(token)
    assert not after["can_answer"] and after["answered_at"] == "2026-11-01 09:00"
    assert [e["kind"] for e in table("hr_events")] == ["applied", "answered"]
    for wrong in ("", "nope", "0" * 32, token[:-1] + ("0" if token[-1] != "0" else "1"), token.upper()):
        assert hdb.application_by_token(wrong) is None


def test_the_questions_close_after_the_window(world, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    people_db.set_setting("quick_link_days", "3", "Operator")
    posting_id = live()
    token = hdb.apply(posting_id, person())["token"]
    mine = hdb.application_by_token(token)
    assert mine["answers_until"] == "2026-11-04 09:00"
    clock(monkeypatch, "2026-11-04 09:01")
    late = hdb.application_by_token(token)
    assert late is not None and not late["can_answer"]
    assert hdb.save_answers(late["id"], {late["questions"][0]["id"]: "Too late."}) == "closed"
    assert table("hr_answers") == []


def test_status_on_the_private_page_follows_the_latest_notice(world, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    posting_id = live()
    token = hdb.apply(posting_id, person())["token"]
    app_id = hdb.application_by_token(token)["id"]
    assert hdb.log_notice(app_id, "no-decision-yet", "email", "No decision yet.", "2026-11-01", "Hari") is None
    assert hdb.application_by_token(token)["decision"] == "pending"
    clock(monkeypatch, "2026-11-09 09:00")
    assert hdb.log_notice(app_id, "not-moving-forward", "phone", "We will not be moving forward.",
                          "2026-11-08", "Hari") is None
    # never interviewed: told only that their own application is not moving forward
    assert hdb.application_by_token(token)["decision"] == "closed"
    # "a hiring decision has been made" is for someone who was interviewed, or offered the job
    assert hdb.add_interview(app_id, "2026-11-05", "", "phone", "Hari", "Hari") is None
    assert hdb.set_interview_outcome(hdb.interviews(app_id)[0]["id"], app_id, "attended", "Hari") is None
    assert hdb.application_by_token(token)["decision"] == "made"
    other = hdb.apply(posting_id, person(2))
    assert hdb.log_notice(other["id"], "offer", "phone", "We would like to offer you the job.",
                          "2026-11-09", "Hari") is None
    assert hdb.application_by_token(other["token"])["decision"] == "made"


# --- stages -----------------------------------------------------------------------

def test_an_application_moves_only_when_a_person_moves_it(world):
    posting_id = live()
    app_id = applied(posting_id, work_entitled="no")                  # a "no" never moves or rejects anyone
    assert hdb.candidate(app_id)["stage"] == "new"
    assert hdb.allowed_moves("new") == ["screening", "interview", "offer", "not-selected", "withdrawn"]
    assert hdb.allowed_moves("offer") == ["hired", "not-selected", "withdrawn"]
    assert hdb.allowed_moves("hired") == [] and hdb.allowed_moves("not-selected") == []
    assert hdb.allowed_moves("withdrawn", operator=True) == ["screening"]
    assert hdb.move_stage(app_id, "new", "hired", "Hari", checks_done=True) == (
        "That move isn't available from this stage.")
    assert hdb.move_stage(app_id, "screening", "interview", "Hari") == (
        "Someone else changed this application. Check where it is now.")
    assert hdb.move_stage(app_id, "new", "screening", "Hari") is None
    assert hdb.move_stage(app_id, "screening", "new", "Hari") == "That move isn't available from this stage."
    assert hdb.move_stage(app_id, "screening", "offer", "Hari") is None
    assert "offer checks" in hdb.move_stage(app_id, "offer", "hired", "Hari")
    assert hdb.move_stage(app_id, "offer", "hired", "Hari", checks_done=True) is None
    stages = [(e["from_stage"], e["to_stage"], e["by"], e["detail"]) for e in table("hr_events")
              if e["kind"] == "stage"]
    assert stages == [("new", "screening", "Hari", ""), ("screening", "offer", "Hari", ""),
                      ("offer", "hired", "Hari", "offer checks confirmed")]
    assert hdb.candidate(app_id)["stage"] == "hired"


def test_not_selected_needs_a_job_related_reason_and_only_the_operator_reopens(world):
    posting_id = live()
    app_id = applied(posting_id)
    assert len(hdb.NOT_SELECTED_REASONS) == 6
    for reason in ("", "Bad vibe", "Too young"):
        assert hdb.move_stage(app_id, "new", "not-selected", "Hari", reason=reason) == (
            "Choose the job-related reason from the list.")
    assert hdb.move_stage(app_id, "new", "not-selected", "Hari", reason="Position filled") is None
    last = table("hr_events")[-1]
    assert (last["kind"], last["to_stage"], last["detail"]) == ("stage", "not-selected", "Position filled")
    assert hdb.move_stage(app_id, "not-selected", "screening", "Hari") == (
        "That move isn't available from this stage.")
    assert hdb.move_stage(app_id, "not-selected", "screening", "Operator", operator=True) is None
    assert table("hr_events")[-1]["detail"] == "reopened"
    assert hdb.move_stage(app_id, "screening", "withdrawn", "Hari") is None


def test_notes_contact_and_hold_each_leave_one_event(world):
    posting_id = live()
    app_id = applied(posting_id)
    assert hdb.add_note(app_id, "Called back within the hour.", "Hari") and not hdb.add_note(app_id, " ", "Hari")
    assert hdb.mark_contacted(app_id, "phone", "Hari") and not hdb.mark_contacted(app_id, "pigeon", "Hari")
    assert hdb.set_hold(app_id, True, "Hari") and not hdb.set_hold(app_id, True, "Hari")
    assert hdb.set_hold(app_id, False, "Operator")
    kinds = [(e["kind"], e["detail"]) for e in table("hr_events")][1:]
    assert kinds == [("note", ""), ("contacted", "phone"), ("hold", "on"), ("hold", "off")]
    assert hdb.notes(app_id)[0]["body"] == "Called back within the hour."
    # the note itself is never copied into the event log
    assert "Called back" not in json.dumps(table("hr_events"))


# --- what a scoring screen is given -------------------------------------------------

def test_the_candidate_record_for_scoring_carries_no_contact_or_legal_answers(world):
    posting_id = live()
    app_id = applied(posting_id, phone="519-555-0142", work_entitled="no", adult="no", heard_from="friend",
                     referrer="Alex Doe")
    record = hdb.candidate(app_id)
    for key in ("email", "phone", "work_entitled", "adult", "heard_from", "referrer", "contact_key",
                "token_hash"):
        assert key not in record, key
    flat = json.dumps(record)
    for private in ("jordan1@example.com", "519-555-0142", "Alex Doe", "friend"):
        assert private not in flat, private
    assert record["name"] == "Jordan1 Sample" and record["availability"] == {"mon": ["morning"], "sat": ["evening"]}
    contact = hdb.candidate_contact(app_id)
    assert contact == {"id": app_id, "email": "jordan1@example.com", "phone": "519-555-0142",
                       "work_entitled": "no", "adult": "no", "heard_from": "friend", "referrer": "Alex Doe"}
    assert hdb.candidate(999) is None and hdb.candidate_contact(999) is None


def test_scores_on_written_answers_are_each_leaders_own(world):
    posting_id = live()
    app_id = applied(posting_id)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari, ada = world["ids"]["hari"], world["ids"]["ada"]
    hdb.save_answers(app_id, {first: "I opened the stand every Saturday.", second: "I helped a lost guest."})
    assert hdb.save_answer_scores(app_id, "Hari", hari, {first: ("3", "Gave a clear example."),
                                                         second: ("", ""), 999: ("4", "x")}) == 1
    assert hdb.save_answer_scores(app_id, "Ada", ada, {first: ("2", ""), second: ("4", "")}) == 2
    assert hdb.save_answer_scores(app_id, "Hari", hari, {first: ("9", "")}) == 0      # not a level: cleared
    assert hdb.save_answer_scores(app_id, "Hari", hari, {first: ("3", "Gave a clear example.")}) == 1
    mine = hdb.scores_for(app_id, "Hari", hari)
    assert mine["mine"] == {first: {"level": 3, "note": "Gave a clear example."}}
    # Hari sees Ada's level only for the answer Hari has scored too
    others = [c for c in mine["cards"] if c["scorer"] == "Ada"]
    assert len(others) == 1 and others[0]["levels"] == {first: 2}
    # a renumbered login is not the same scorer
    assert hdb.scores_for(app_id, "Hari", hari + 50)["mine"] == {}
    everything = hdb.scores_for(app_id, "Operator", None, operator=True)
    assert {c["scorer"]: c["levels"] for c in everything["cards"]} == {
        "Ada": {first: 2, second: 4}, "Hari": {first: 3}}
    # nothing is added up: a card is levels by criterion and nothing else
    assert all(set(c) == {"source", "interview_id", "scorer", "levels", "notes"} for c in everything["cards"])


# --- interviews and scorecards ------------------------------------------------------

def interviewed(app_id: int, on: str, by: str = "Hari") -> int:
    assert hdb.add_interview(app_id, on, "", "in person", by, by) is None
    interview_id = hdb.interviews(app_id)[-1]["id"]
    assert hdb.set_interview_outcome(interview_id, app_id, "attended", by) is None
    return interview_id


def test_interviews_are_logged_with_a_date_and_an_outcome(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    posting_id = live()
    app_id = applied(posting_id)
    assert hdb.add_interview(app_id, "", "", "in person", "Hari", "Hari") == "Enter the interview date."
    # (rewritten after the browser run, finding B8: a time written the way people write it is
    # understood, see test_people_verify.py; only something that is not a time is refused)
    assert hdb.add_interview(app_id, "2026-11-12", "after lunch", "phone", "Hari", "Hari") == (
        "Enter the time like 3:30 pm or 15:30, or leave it empty.")
    assert hdb.add_interview(app_id, "2026-11-12", "15:30", "carrier pigeon", "Hari", "Hari") == (
        "Choose how the interview is held.")
    assert hdb.add_interview(app_id, "2026-11-12", "15:30", "video", "Hari", "Hari") is None
    interview = hdb.interviews(app_id)[0]
    assert (interview["mode"], interview["outcome"], interview["created_by"]) == ("video", "scheduled", "Hari")
    # it has not happened yet, so it cannot be attended or missed
    assert "future" in hdb.set_interview_outcome(interview["id"], app_id, "attended", "Hari")
    assert "future" in hdb.set_interview_outcome(interview["id"], app_id, "no-show", "Hari")
    assert hdb.clocks_for(app_id) == []
    clock(monkeypatch, "2026-11-12 16:00")
    assert hdb.set_interview_outcome(interview["id"], app_id, "no-show", "Hari") is None
    assert hdb.clocks_for(app_id) == []                               # a no-show starts no clock
    assert hdb.set_interview_outcome(interview["id"], app_id, "attended", "Hari") is None
    assert hdb.set_interview_outcome(interview["id"], app_id, "attended", "Hari") == "That was already recorded."
    assert [c["deadline"] for c in hdb.clocks_for(app_id)] == ["2026-12-27"]
    assert hdb.set_interview_outcome(interview["id"], 999, "cancelled", "Hari") == (
        "That interview isn't there any more.")
    kinds = [(e["kind"], e["detail"]) for e in table("hr_events")][1:]
    assert kinds == [("interview", "video"), ("outcome", "no-show"), ("outcome", "attended")]
    # once the application is decided no new interview can be planned, but one that was really
    # held and never logged still goes on record (it starts its own 45-day clock)
    hdb.move_stage(app_id, "new", "withdrawn", "Hari")
    assert "only an interview that has already been held" in hdb.add_interview(
        app_id, "2026-11-13", "", "phone", "Hari", "Hari")
    assert hdb.add_interview(app_id, "2026-11-11", "", "phone", "Hari", "Hari") is None
    assert len(hdb.interviews(app_id)) == 2


def test_scorecards_stay_hidden_until_you_submit_your_own(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 12:00")
    posting_id = live()
    app_id = applied(posting_id)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari, ada = world["ids"]["hari"], world["ids"]["ada"]
    assert hdb.add_interview(app_id, "2026-11-10", "", "in person", "Hari and Ada", "Hari") is None
    interview_id = hdb.interviews(app_id)[0]["id"]
    assert "attended" in hdb.save_scorecard(interview_id, app_id, "Hari", hari, {first: ("3", "")}, True)
    hdb.set_interview_outcome(interview_id, app_id, "attended", "Hari")
    assert hdb.save_scorecard(interview_id, app_id, "Ada", ada, {first: ("4", "Specific."), second: ("2", "")},
                              True) is None
    # Hari has only a draft so far: Ada's scorecard is held back
    assert hdb.save_scorecard(interview_id, app_id, "Hari", hari, {first: ("3", "Draft.")}, False) is None
    seen = hdb.scores_for(app_id, "Hari", hari)
    assert seen["hidden"] == 1 and seen["cards"] == []
    assert seen["my_cards"][interview_id] == {"submitted": False, "levels": {first: 3}, "notes": {first: "Draft."}}
    assert "at least one criterion" in hdb.save_scorecard(interview_id, app_id, "Hari", hari, {}, True)
    assert hdb.save_scorecard(interview_id, app_id, "Hari", hari, {first: ("3", ""), second: ("3", "")},
                              True) is None
    seen = hdb.scores_for(app_id, "Hari", hari)
    assert seen["hidden"] == 0 and seen["my_cards"][interview_id]["submitted"]
    assert {c["scorer"]: c["levels"] for c in seen["cards"]} == {"Ada": {first: 4, second: 2},
                                                                "Hari": {first: 3, second: 3}}
    # submitted is final
    assert "already submitted" in hdb.save_scorecard(interview_id, app_id, "Hari", hari, {first: ("1", "")}, True)
    # a leader who was not in the room and has submitted nothing sees none; the Operator sees all
    assert hdb.scores_for(app_id, "Lena", world["ids"]["lena"]) == {
        "mine": {}, "cards": [], "my_cards": {}, "hidden": 2}
    assert len(hdb.scores_for(app_id, "Operator", None, operator=True)["cards"]) == 2
    # and the interview can no longer be un-attended
    assert "stays as attended" in hdb.set_interview_outcome(interview_id, app_id, "cancelled", "Hari")
    assert [e["detail"] for e in table("hr_events") if e["kind"] == "scorecard"] == ["submitted", "submitted"]


# --- the 45-day clock ---------------------------------------------------------------

D = date.fromisoformat


def clocks(interviews, notices, today):
    return [(c["interviewed"], c["deadline"], c["given_on"], c["status"], c["days_left"])
            for c in hdb.notice_obligations(interviews, notices, D(today))]


def test_one_interview_starts_one_clock_of_forty_five_days():
    assert clocks([], [], "2026-11-20") == []
    assert clocks(["2026-11-01"], [], "2026-11-20") == [("2026-11-01", "2026-12-16", None, "due", 26)]
    assert clocks(["2026-11-01"], [], "2026-12-16") == [("2026-11-01", "2026-12-16", None, "due", 0)]
    assert clocks(["2026-11-01"], [], "2026-12-17") == [("2026-11-01", "2026-12-16", None, "overdue", -1)]
    assert clocks(["2026-11-01"], ["2026-12-16"], "2027-01-01")[0][2:4] == ("2026-12-16", "on-time")
    assert clocks(["2026-11-01"], ["2026-12-17"], "2027-01-01")[0][2:4] == ("2026-12-17", "late")
    # a "no decision yet" notice on the day of the interview counts
    assert clocks(["2026-11-01"], ["2026-11-01"], "2026-11-20")[0][3] == "on-time"


def test_a_second_interview_held_in_time_moves_the_deadline():
    two = clocks(["2026-11-01", "2026-11-20"], [], "2026-12-01")
    assert two == [("2026-11-20", "2027-01-04", None, "due", 34)]
    # held on the very last day still counts
    assert clocks(["2026-11-01", "2026-12-16"], [], "2026-12-20")[0][:2] == ("2026-12-16", "2027-01-30")
    # a notice given between the two does not cover the later interview
    assert clocks(["2026-11-01", "2026-11-20"], ["2026-11-10"], "2026-12-01")[0][3] == "due"
    assert clocks(["2026-11-01", "2026-11-20"], ["2026-11-10", "2026-11-25"], "2026-12-01")[0][2:4] == (
        "2026-11-25", "on-time")


def test_interviews_more_than_forty_five_days_apart_each_start_a_clock():
    apart = clocks(["2026-09-01", "2026-12-01"], [], "2026-12-05")
    assert apart == [("2026-09-01", "2026-10-16", None, "overdue", -50),
                     ("2026-12-01", "2027-01-15", None, "due", 41)]
    # one notice after the second interview: late for the first clock, on time for the second
    told = clocks(["2026-09-01", "2026-12-01"], ["2026-12-03"], "2026-12-05")
    assert [c[3] for c in told] == ["late", "on-time"]
    each = clocks(["2026-09-01", "2026-12-01"], ["2026-09-20", "2026-12-03"], "2026-12-05")
    assert [(c[2], c[3]) for c in each] == [("2026-09-20", "on-time"), ("2026-12-03", "on-time")]
    # a later interview held after the deadline passed does not rescue the first clock
    assert clocks(["2026-09-01", "2026-10-17"], [], "2026-10-18")[0][3] == "overdue"


def test_the_notice_board_lists_who_is_owed_a_notice_soonest_first(world, monkeypatch):
    clock(monkeypatch, "2026-09-01 10:00")
    posting_id = live()
    early, later, told, never = (applied(posting_id, n) for n in (1, 2, 3, 4))
    interviewed(early, "2026-09-01")
    interviewed(told, "2026-09-01")
    assert hdb.log_notice(told, "not-moving-forward", "email", "We will not be moving forward.",
                          "2026-09-01", "Hari") is None
    hdb.move_stage(early, "new", "withdrawn", "Hari")                 # a withdrawal is still owed a notice
    clock(monkeypatch, "2026-10-10 10:00")
    interviewed(later, "2026-10-09")
    assert hdb.counts() == {"new": 3, "notices_due": 1, "notices_overdue": 0}      # early: 6 days left
    clock(monkeypatch, "2026-10-20 10:00")
    board = hdb.notice_board()
    assert [(o["application_id"], o["deadline"], o["overdue"]) for o in board["owed"]] == [
        (early, "2026-10-16", True), (later, "2026-11-23", False)]
    assert board["owed"][0]["name"] == "Jordan1 Sample" and board["owed"][0]["posting_title"] == "Team member"
    assert [(n["given_to"], n["kind"], n["method"], n["given_on"], n["logged_by"]) for n in board["log"]] == [
        ("Jordan3 Sample", "not-moving-forward", "email", "2026-09-01", "Hari")]
    assert hdb.counts() == {"new": 3, "notices_due": 0, "notices_overdue": 1}
    assert never not in [o["application_id"] for o in board["owed"]]
    # logging the notice clears it, and is one event
    assert hdb.log_notice(early, "no-decision-yet", "phone", "No decision has been made yet.",
                          "2026-10-20", "Hari") is None
    assert [o["application_id"] for o in hdb.notice_board()["owed"]] == [later]
    assert table("hr_events")[-1]["kind"] == "notice" and table("hr_events")[-1]["detail"] == "no-decision-yet"


def test_a_notice_needs_what_how_when_and_the_words(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 10:00")
    app_id = applied(live())
    said = "A hiring decision has not yet been made for this posting."
    assert hdb.log_notice(app_id, "maybe", "email", said, "2026-11-10", "Hari") == (
        "Choose what the applicant was told.")
    assert hdb.log_notice(app_id, "offer", "fax", said, "2026-11-10", "Hari") == "Choose how they were told."
    assert hdb.log_notice(app_id, "offer", "email", said, "soon", "Hari") == "Enter the date they were told."
    assert "future" in hdb.log_notice(app_id, "offer", "email", said, "2026-11-11", "Hari")
    assert "before they applied" in hdb.log_notice(app_id, "offer", "email", said, "2026-11-09", "Hari")
    assert hdb.log_notice(app_id, "offer", "email", "  ", "2026-11-10", "Hari") == "Write what was said or sent."
    assert table("hr_notices") == []
    assert hdb.log_notice(app_id, "no-decision-yet", "in person", said, "2026-11-10", "Hari") is None
    notice = hdb.notices(app_id)[0]
    assert (notice["kind"], notice["method"], notice["said"], notice["given_on"], notice["logged_by"],
            notice["given_to"]) == ("no-decision-yet", "in person", said, "2026-11-10", "Hari", "Jordan1 Sample")


def test_offers_and_replies_are_recorded_and_never_move_anyone(world):
    app_id = applied(live())
    assert "offer stage first" in hdb.log_offer(app_id, "Hari")
    assert hdb.log_offer_reply(app_id, "accepted", "Hari") == "There is no offer waiting for an answer."
    hdb.move_stage(app_id, "new", "offer", "Hari")
    assert hdb.log_offer(app_id, "Hari") is None
    assert hdb.log_offer(app_id, "Hari") == "An offer is already waiting for an answer."
    assert hdb.offer_state(app_id) == {"open": True, "last_reply": None}
    assert hdb.log_offer_reply(app_id, "perhaps", "Hari") == "Choose accepted or declined."
    assert hdb.log_offer_reply(app_id, "accepted", "Hari") is None
    assert hdb.offer_state(app_id) == {"open": False, "last_reply": "accepted"}
    assert hdb.candidate(app_id)["stage"] == "offer"                 # a person moves it to hired


# --- the dashboard ------------------------------------------------------------------

def test_metrics_come_from_a_small_fixed_set_of_events(world, monkeypatch):
    clock(monkeypatch, "2026-10-01 08:00")
    posting_id = live()
    other = live(title="Kitchen team")
    sources = ["school", "school", "friend", "job-site", "walk-in", "social"]
    apps = [applied(posting_id, n, heard_from=source) for n, source in enumerate(sources, 1)]
    outside = applied(other, 7)
    for app_id in apps[:4]:                                            # four of six answer
        hdb.save_answers(app_id, {hdb.get_posting(posting_id)["criteria"][0]["id"]: "An answer."})
    clock(monkeypatch, "2026-10-01 12:00")                             # 4 hours: first contact
    for app_id in apps[:5]:
        hdb.mark_contacted(app_id, "phone", "Hari")
    clock(monkeypatch, "2026-10-03 08:00")                             # 2 days in "new"
    for app_id in apps[:5]:
        assert hdb.move_stage(app_id, "new", "screening", "Hari") is None
    hdb.move_stage(apps[5], "new", "not-selected", "Hari", reason="Could not reach the applicant")
    clock(monkeypatch, "2026-10-06 08:00")                             # 3 days in screening
    for app_id in apps[:5]:
        assert hdb.move_stage(app_id, "screening", "interview", "Hari") is None
        hdb.add_interview(app_id, "2026-10-08", "", "in person", "Hari", "Hari")
    clock(monkeypatch, "2026-10-08 18:00")
    talks = [hdb.interviews(app_id)[0]["id"] for app_id in apps[:5]]
    for app_id, talk in list(zip(apps, talks))[:4]:
        hdb.set_interview_outcome(talk, app_id, "attended", "Hari")
    hdb.set_interview_outcome(talks[4], apps[4], "no-show", "Hari")
    clock(monkeypatch, "2026-10-10 08:00")                             # 4 days in interview
    for app_id in apps[:3]:
        assert hdb.move_stage(app_id, "interview", "offer", "Hari") is None
        hdb.log_offer(app_id, "Hari")
        hdb.log_notice(app_id, "offer", "phone", "We are pleased to offer you the position.",
                       "2026-10-10", "Hari")
    hdb.log_offer_reply(apps[0], "accepted", "Hari")
    hdb.log_offer_reply(apps[1], "accepted", "Hari")
    hdb.log_offer_reply(apps[2], "declined", "Hari")
    clock(monkeypatch, "2026-10-11 08:00")                             # 10 days from applying
    for app_id in apps[:2]:
        assert hdb.move_stage(app_id, "offer", "hired", "Hari", checks_done=True) is None
    clock(monkeypatch, "2026-10-20 08:00")
    m = hdb.metrics(posting_id, 30)
    assert m["applications"] == 6 and m["hired"] == 2 and m["days"] == 30
    assert [(s["label"], s["count"]) for s in m["funnel"]] == [
        ("Applications", 6), ("Reached screening", 5), ("Reached interview", 5), ("Reached offer", 3),
        ("Hired", 2)]
    assert [s["share"] for s in m["funnel"]] == [
        None, {"count": 5, "of": 6, "pct": 83}, {"count": 5, "of": 5, "pct": 100},
        {"count": 3, "of": 5, "pct": 60}, {"count": 2, "of": 3, "pct": None}]      # under five: no percentage
    assert m["answered"] == {"count": 4, "of": 6, "pct": 67}
    assert m["days_to_hire"] == {"n": 2, "median": None}               # two hires: not enough data yet
    in_stage = {s["stage"]: (s["n"], s["median"]) for s in m["days_in_stage"]}
    assert in_stage == {"new": (6, 2.0), "screening": (5, 3.0), "interview": (3, None), "offer": (2, None)}
    assert m["hours_to_contact"] == {"n": 5, "median": 4.0}
    assert m["sources"] == [
        {"key": "walk-in", "label": "Walked in or scanned a code in the restaurant", "applied": 1, "hired": 0},
        {"key": "friend", "label": "A friend or family member who works here", "applied": 1, "hired": 0},
        {"key": "job-site", "label": "Indeed or another job site", "applied": 1, "hired": 0},
        {"key": "social", "label": "Social media", "applied": 1, "hired": 0},
        {"key": "school", "label": "School", "applied": 2, "hired": 2}]
    assert m["no_show"] == {"count": 1, "of": 5, "pct": 20}
    assert m["offers"] == {"count": 2, "of": 3, "pct": None}
    assert m["notices"] == {"on-time": 3, "late": 0, "due": 1, "overdue": 0}
    # every posting together, and the period filter by application date
    assert hdb.metrics(None, 30)["applications"] == 7 and hdb.metrics(other, 30)["applications"] == 1
    clock(monkeypatch, "2026-11-15 08:00")
    assert hdb.metrics(posting_id, 30)["applications"] == 0 and hdb.metrics(posting_id, 90)["applications"] == 6
    assert hdb.metrics(posting_id, 7)["days"] == 90                    # only 30, 90 or 365
    assert hdb.metrics(posting_id, 90)["notices"] == {"on-time": 3, "late": 0, "due": 1, "overdue": 0}
    clock(monkeypatch, "2026-11-23 08:00")                             # 46 days after the interview
    assert hdb.metrics(posting_id, 90)["notices"]["overdue"] == 1
    assert outside not in apps


# --- retention ----------------------------------------------------------------------

def test_the_purge_removes_personal_details_and_keeps_the_record(world, monkeypatch):
    clock(monkeypatch, "2024-01-10 09:00")
    posting_id = live()
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari = world["ids"]["hari"]
    result = hdb.apply(posting_id, person(1, phone="519-555-0142", heard_from="friend", referrer="Alex Doe"))
    gone, token = result["id"], result["token"]
    held, hired, recent, undecided = (applied(posting_id, n) for n in (2, 3, 4, 5))
    hdb.save_answers(gone, {first: "I always showed up early.", second: "I helped a lost guest."})
    hdb.save_answer_scores(gone, "Hari", hari, {first: ("3", "Specific example about showing up.")})
    hdb.add_note(gone, "Polite on the phone.", "Hari")
    interview_id = interviewed(gone, "2024-01-10")
    hdb.save_scorecard(interview_id, gone, "Hari", hari, {first: ("2", "Vague in person.")}, True)
    assert hdb.log_notice(gone, "no-decision-yet", "email", "Hi Jordan1, no decision yet.", "2024-01-10",
                          "Hari") is None
    with people_db.connect() as conn:                                  # what an AI request leaves behind
        conn.execute(
            "INSERT INTO ai_jobs (kind, target_id, status, requested_by, requested_at, input_json, "
            "raw_output, output_json) VALUES ('hiring_score', ?, 'done', 'Hari', '2024-01-10 09:00', ?, ?, ?)",
            (gone, '{"answers": ["I always showed up early."]}', '{"quote": "showed up early"}',
             '{"quote": "showed up early"}'))
        conn.commit()
    clock(monkeypatch, "2024-03-01 09:00")
    assert hdb.log_notice(gone, "not-moving-forward", "email", "Hi Jordan1, we will not be moving forward.",
                          "2024-03-01", "Hari") is None
    for app_id in (gone, held):
        assert hdb.move_stage(app_id, "new", "not-selected", "Hari", reason="Position filled") is None
    hdb.set_hold(held, True, "Hari")
    hdb.move_stage(hired, "new", "offer", "Hari")
    hdb.move_stage(hired, "offer", "hired", "Hari", checks_done=True)
    hdb.close_posting(posting_id, "Operator")
    copies_before = table("hr_snapshots")

    clock(monkeypatch, "2026-02-28 09:00")                             # one day short of 24 months
    hdb.move_stage(recent, "new", "withdrawn", "Hari")
    assert hdb.retention()["due"] == [] and hdb.purge("Operator") == 0
    clock(monkeypatch, "2026-03-01 09:00")
    page = hdb.retention()
    assert [(d["id"], d["name"], d["decided_on"], d["keep_months"]) for d in page["due"]] == [
        (gone, "Jordan1 Sample", "2024-03-01", 24)]
    assert [h["id"] for h in page["held"]] == [held] and page["undecided"] == 1 and page["purged"] == 0
    assert hdb.purge("Operator") == 1 and hdb.purge("Operator") == 0

    rows = {r["id"]: r for r in table("hr_applications")}
    row = rows[gone]
    assert (row["first_name"], row["last_name"], row["email"], row["phone"], row["contact_key"],
            row["availability"], row["referrer"], row["experience"], row["token_hash"],
            row["work_entitled"], row["adult"]) == ("Removed", "", "", "", "", "{}", "", "", "", "", "")
    assert row["purged_at"] == "2026-03-01 09:00" and row["purged_by"] == "Operator"
    assert (row["stage"], row["heard_from"]) == ("not-selected", "friend")     # the bare record stays
    assert not [a for a in table("hr_answers") if a["application_id"] == gone]
    assert not [n for n in table("hr_notes") if n["application_id"] == gone]
    scores = [s for s in table("hr_scores") if s["application_id"] == gone]
    assert sorted((s["source"], s["level"], s["note"]) for s in scores if s["level"]) == [
        ("answers", 3, ""), ("interview", 2, "")]                                # levels stay, notes go
    job = [j for j in table("ai_jobs") if j["target_id"] == gone][0]
    assert job["input_json"] is None and job["raw_output"] is None and job["output_json"] is None
    events = [e for e in table("hr_events") if e["application_id"] == gone]
    assert events[-1]["kind"] == "purged" and events[-1]["by"] == "Operator"
    assert [e["to_stage"] for e in events if e["kind"] == "stage"] == ["not-selected"]
    # notices: under three years old are kept whole, with the name; the older one loses its words
    kept = {n["given_on"]: (n["said"], n["given_to"]) for n in hdb.notices(gone)}
    assert kept == {"2024-03-01": ("Hi Jordan1, we will not be moving forward.", "Jordan1 Sample"),
                    "2024-01-10": ("Hi Jordan1, no decision yet.", "Jordan1 Sample")}
    assert hdb.application_by_token(token) is None                     # the private link is dead
    # everyone else is untouched: on hold, hired, decided too recently, not decided at all
    for app_id in (held, hired, recent, undecided):
        assert rows[app_id]["first_name"].startswith("Jordan") and rows[app_id]["purged_at"] is None
    assert table("hr_snapshots") == copies_before and len(table("hr_postings")) == 1
    everything = json.dumps([table(name) for name in ("hr_applications", "hr_answers", "hr_notes", "hr_scores",
                                                      "hr_events", "ai_jobs")])
    for private in ("jordan1@example.com", "519-555-0142", "Alex Doe", "showed up", "Polite on the phone"):
        assert private not in everything, private

    # a later run blanks the words of a notice once it is more than three years old: the whole of
    # the third anniversary of the day it was given is still inside the three years
    clock(monkeypatch, "2027-01-10 09:00")
    assert hdb.purge_all("Operator") == (0, 0)
    assert {n["given_on"]: n["given_to"] for n in hdb.notices(gone)}["2024-01-10"] == "Jordan1 Sample"
    clock(monkeypatch, "2027-01-11 09:00")
    assert hdb.purge_all("Operator") == (0, 1) and hdb.purge_all("Operator") == (0, 0)
    kept = {n["given_on"]: (n["said"], n["given_to"]) for n in hdb.notices(gone)}
    assert kept["2024-01-10"] == ("", "") and kept["2024-03-01"][1] == "Jordan1 Sample"
    assert len(hdb.notices(gone)) == 2                                 # the rows themselves stay
    # the hold comes off: now it is due
    hdb.set_hold(held, False, "Operator")
    assert [d["id"] for d in hdb.retention()["due"]] == [held]        # "recent" has a year to go
    assert hdb.purge("Operator") == 1 and hdb.retention()["purged"] == 2
    assert table("hr_snapshots") == copies_before


def test_each_applicant_is_kept_for_the_months_they_were_told(world, monkeypatch):
    clock(monkeypatch, "2025-01-10 09:00")
    posting_id = live()
    told_24 = applied(posting_id, 1)
    people_db.set_setting("retention_months", "12", "Operator")
    told_12 = applied(posting_id, 2)
    assert any("for 12 months after" in line for line in hdb.public_view(hdb.get_posting(posting_id))["privacy"])
    for app_id in (told_24, told_12):
        hdb.move_stage(app_id, "new", "withdrawn", "Hari")
    clock(monkeypatch, "2026-01-10 09:00")
    assert [d["id"] for d in hdb.retention()["due"]] == [told_12]
    people_db.set_setting("retention_months", "36", "Operator")       # changing it later changes no promise
    assert [d["id"] for d in hdb.retention()["due"]] == [told_12]
    clock(monkeypatch, "2027-01-10 09:00")
    assert [d["id"] for d in hdb.retention()["due"]] == [told_24, told_12]


def test_the_records_export_holds_every_copy_and_every_notice(world, monkeypatch):
    clock(monkeypatch, "2026-11-10 10:00")
    posting_id = live()
    draft(title="Never published")
    app_id = applied(posting_id)
    hdb.log_notice(app_id, "no-decision-yet", "email", "No decision has been made yet.", "2026-11-10", "Hari")
    hdb.close_posting(posting_id, "Operator")
    records = json.loads(hdb.records_export())
    assert records["kind"] == "hiring-records" and len(records["postings"]) == 1
    posting = records["postings"][0]
    assert posting["closed_at"] == "2026-11-10 10:00" and posting["published_at"] == "2026-11-10 10:00"
    assert [c["reason"] for c in posting["copies"]] == ["published", "closed"]
    assert "APPLICATION FORM (blank)" in posting["copies"][0]["body"]
    assert records["decision_notices"] == [{
        "application_id": app_id, "posting": "Team member", "given_to": "Jordan1 Sample",
        "kind": "no-decision-yet", "method": "email", "given_on": "2026-11-10",
        "said": "No decision has been made yet.", "logged_at": "2026-11-10 10:00", "logged_by": "Hari"}]


# --- settings a live posting prints, and corrections ------------------------------------

def test_a_settings_change_that_alters_a_live_posting_is_kept_as_a_copy_too(world):
    posting_id = live()
    draft_id = draft(title="Still a draft")
    assert not hdb.keep_current_copy(posting_id) and len(hdb.snapshots(posting_id)) == 1
    people_db.set_setting("hiring_contact_name", "Riley Example", "Operator")
    assert hdb.keep_current_copy(posting_id) and not hdb.keep_current_copy(posting_id)
    copies = hdb.snapshots(posting_id)
    assert [(c["reason"], c["taken_by"]) for c in copies] == [
        ("published", "Operator"), ("changed", "A change in People settings")]
    assert "contact Morgan Example" in hdb.get_snapshot(copies[0]["id"])["body"]
    assert "contact Riley Example" in hdb.get_snapshot(copies[1]["id"])["body"]
    people_db.set_setting("retention_months", "36", "Operator")
    assert hdb.keep_current_copy(posting_id)
    assert "for 36 months after" in hdb.get_snapshot(hdb.snapshots(posting_id)[-1]["id"])["body"]
    people_db.set_setting("lock_minutes", "45", "Operator")           # nothing the posting prints
    assert not hdb.keep_current_copy(posting_id)
    assert not hdb.keep_current_copy(draft_id) and not hdb.keep_current_copy(999)
    hdb.close_posting(posting_id, "Operator")
    people_db.set_setting("hiring_contact_name", "Someone Else", "Operator")
    assert not hdb.keep_current_copy(posting_id)                       # closed: nothing is shown any more


def test_a_live_posting_that_stops_passing_the_check_is_called_out(world):
    posting_id = live()
    live(title="Kitchen team", pay_min="19.00", pay_max="21.00")
    assert hdb.needs_attention() == []
    people_db.set_setting("min_wage_general", "18.40", "Operator")    # the floor went up on October 1
    attention = hdb.needs_attention()
    assert [a["id"] for a in attention] == [posting_id] and attention[0]["title"] == "Team member"
    assert "below Ontario's general minimum wage ($18.40" in attention[0]["problems"][0]
    people_db.set_setting("hiring_contact_name", "", "Operator")
    assert len(hdb.needs_attention()) == 2


def test_an_applicants_details_can_be_corrected_when_they_ask(world, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    posting_id = live()
    app_id = applied(posting_id)
    fixed = {"first_name": "Jordan", "last_name": "Sample-Lee", "email": "", "phone": "519 555 0199"}
    assert hdb.correct_details(app_id, {**fixed, "phone": "123"}, "Hari") == [
        "That phone number needs 10 to 15 digits."]
    assert hdb.correct_details(app_id, {**fixed, "phone": ""}, "Hari") == [
        "Give us a phone number or an email so we can reach you."]
    assert hdb.candidate(app_id)["name"] == "Jordan1 Sample"
    assert hdb.correct_details(app_id, fixed, "Hari") == []
    assert hdb.candidate(app_id)["name"] == "Jordan Sample-Lee"
    assert hdb.candidate_contact(app_id)["phone"] == "519 555 0199" and hdb.candidate_contact(app_id)["email"] == ""
    last = table("hr_events")[-1]
    assert (last["kind"], last["by"], last["detail"]) == ("corrected", "Hari", "name or contact details")
    # the duplicate check follows the corrected details, and nothing else about the application changed
    same_phone = hdb.apply(posting_id, person(2, email="", phone="(519) 555-0199"))
    assert same_phone["status"] == "created" and hdb.candidate(same_phone["id"])["duplicate_of"] == app_id
    old_email = hdb.apply(posting_id, person(1))
    assert old_email["status"] == "created" and hdb.candidate(old_email["id"])["duplicate_of"] is None
    row = table("hr_applications")[0]
    assert (row["work_entitled"], row["adult"], row["stage"], row["heard_from"]) == ("yes", "yes", "new", "school")
    assert hdb.correct_details(999, fixed, "Hari") == ["That application isn't there any more."]


def test_a_score_is_only_kept_for_an_answer_that_was_written(world):
    posting_id = live()
    app_id = applied(posting_id)
    first, second = (c["id"] for c in hdb.get_posting(posting_id)["criteria"])
    hari = world["ids"]["hari"]
    assert hdb.save_answer_scores(app_id, "Hari", hari, {first: ("3", "")}) == 0      # nothing written yet
    hdb.save_answers(app_id, {first: "An answer."})
    assert hdb.save_answer_scores(app_id, "Hari", hari, {first: ("3", ""), second: ("4", "")}) == 1
    assert set(hdb.scores_for(app_id, "Hari", hari)["mine"]) == {first}
