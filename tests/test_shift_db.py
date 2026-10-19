"""Storage-layer tests for the shift leading app (shift_db.py).

Each test gets a fresh temp database via the isolated_db fixture, which
re-points shift_db.DB_PATH before init_db() runs.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shift_db
import shift_cleaning

# Base templates + the station cleaning sheets, both seeded on first boot
SEEDED_LISTS = len(shift_db.SEED_TEMPLATES) + len(shift_cleaning.LISTS)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test_shift.db"))
    shift_db.init_db()
    yield


def test_init_seeds_templates_and_positions(isolated_db):
    templates = shift_db.all_templates()
    assert len(templates) == SEEDED_LISTS
    assert all(t["item_count"] > 0 for t in templates)
    positions = shift_db.active_positions()
    assert len(positions) == len(shift_db.SEED_POSITIONS)


def test_init_is_idempotent(isolated_db):
    shift_db.init_db()
    shift_db.init_db()
    assert len(shift_db.all_templates()) == SEEDED_LISTS


def test_runs_instantiate_once_per_date(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    shift_db.ensure_runs_for_date("2026-09-19")
    runs = shift_db.runs_for_date("2026-09-19")
    assert len(runs) == SEEDED_LISTS
    # Snapshot items exist and none are done yet
    run, items = shift_db.run_with_items(runs[0]["id"])
    assert run["run_date"] == "2026-09-19"
    assert items and all(not i["done"] for i in items)


def test_runs_ordered_by_daypart(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    runs = shift_db.runs_for_date("2026-09-19")
    # Daily lists first (all-day, then breakfast → dinner), then weekly, monthly
    freq = {f: i for i, f in enumerate(shift_db.FREQUENCIES)}
    order = {d: i for i, d in enumerate(["allday"] + shift_db.DAYPARTS)}
    keys = [(freq[r["frequency"]], order[r["daypart"]]) for r in runs]
    assert keys == sorted(keys)


def test_check_and_uncheck_item(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    run_id = shift_db.runs_for_date("2026-09-19")[0]["id"]
    _, items = shift_db.run_with_items(run_id)
    item = items[0]

    assert shift_db.set_item_done(item["id"], True, "Aliyah")
    _, items = shift_db.run_with_items(run_id)
    assert items[0]["done"] == 1
    assert items[0]["done_by"] == "Aliyah"
    assert items[0]["done_at"]

    assert shift_db.set_item_done(item["id"], False, "Aliyah")
    _, items = shift_db.run_with_items(run_id)
    assert items[0]["done"] == 0
    assert items[0]["done_by"] is None

    assert not shift_db.set_item_done(999999, True, "Nobody")


def test_template_edit_does_not_rewrite_history(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    template = shift_db.all_templates()[0]
    run_id = shift_db.runs_for_date("2026-09-19")[0]["id"]
    _, before = shift_db.run_with_items(run_id)

    shift_db.update_template(template["id"], "Renamed", "lunch", "All",
                             [("Only item", True)])

    _, after = shift_db.run_with_items(run_id)
    assert [i["label"] for i in after] == [i["label"] for i in before]

    # New dates pick up the edit
    shift_db.ensure_runs_for_date("2026-09-20")
    new_runs = [r for r in shift_db.runs_for_date("2026-09-20") if r["name"] == "Renamed"]
    assert new_runs and new_runs[0]["total"] == 1
    assert new_runs[0]["critical_remaining"] == 1


def test_deactivated_template_stops_instantiating(isolated_db):
    template = shift_db.all_templates()[0]
    shift_db.set_template_active(template["id"], False)
    shift_db.ensure_runs_for_date("2026-09-19")
    assert len(shift_db.runs_for_date("2026-09-19")) == SEEDED_LISTS - 1


def test_create_template(isolated_db):
    tid = shift_db.create_template("Cow Appreciation Setup", "afternoon", "All",
                                   [("Hang banner", False), ("Stock cow plush", True)])
    t, items = shift_db.template_with_items(tid)
    assert t["name"] == "Cow Appreciation Setup"
    assert [i["label"] for i in items] == ["Hang banner", "Stock cow plush"]
    assert [i["critical"] for i in items] == [0, 1]


def test_critical_remaining_counts(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    boh = next(r for r in shift_db.runs_for_date("2026-09-19") if r["name"] == "BOH Opening")
    assert boh["critical_remaining"] == 5
    _, items = shift_db.run_with_items(boh["id"])
    crit = next(i for i in items if i["critical"])
    shift_db.set_item_done(crit["id"], True, "Maya")
    boh = next(r for r in shift_db.runs_for_date("2026-09-19") if r["name"] == "BOH Opening")
    assert boh["critical_remaining"] == 4


def test_goal_lifecycle_and_progress(isolated_db):
    gid = shift_db.create_goal(
        title="Drive-thru under 4 minutes", why="Guest experience", metric="Avg DT time",
        unit="sec", target_value=240, direction="down", period="weekly",
        due_date="", created_by="Joshua",
    )
    goals = shift_db.goals_by_status("active")
    assert goals[0]["latest_value"] is None
    assert goals[0]["progress_pct"] is None

    shift_db.add_goal_update(gid, 300, "Lunch rush avg", "Joshua")
    g, updates = shift_db.goal_with_updates(gid)
    assert g["latest_value"] == 300
    assert g["progress_pct"] == 80  # 240/300
    assert len(updates) == 1

    shift_db.add_goal_update(gid, 230, "Beat it", "Joshua")
    g, _ = shift_db.goal_with_updates(gid)
    assert g["progress_pct"] == 100

    shift_db.set_goal_status(gid, "achieved")
    assert not shift_db.goals_by_status("active")
    assert shift_db.goals_by_status("achieved")[0]["id"] == gid


def test_goal_progress_direction_up(isolated_db):
    gid = shift_db.create_goal("CEM Taste score", "", "CEM Taste", "%", 70,
                               "up", "monthly", "", "Joshua")
    shift_db.add_goal_update(gid, 35, "", "Joshua")
    g, _ = shift_db.goal_with_updates(gid)
    assert g["progress_pct"] == 50


def test_goal_note_only_update_keeps_last_value(isolated_db):
    gid = shift_db.create_goal("Window times", "", "Avg window", "sec", 90,
                               "down", "weekly", "", "J")
    shift_db.add_goal_update(gid, 100, "", "J")
    shift_db.add_goal_update(gid, None, "Coached the window team", "J")
    g, updates = shift_db.goal_with_updates(gid)
    assert len(updates) == 2
    assert g["latest_value"] == 100


def test_lineup_assign_unassign_and_duplicates(isolated_db):
    pos = shift_db.active_positions()[0]
    shift_db.assign_position("2026-09-19", "lunch", pos["id"], "Marcus")
    shift_db.assign_position("2026-09-19", "lunch", pos["id"], "marcus")  # dup, case-insens.
    lineup = shift_db.lineup_for("2026-09-19", "lunch")
    assert len(lineup[pos["id"]]) == 1

    shift_db.unassign(lineup[pos["id"]][0]["id"])
    assert not shift_db.lineup_for("2026-09-19", "lunch")


def test_copy_lineup_from_previous_day(isolated_db):
    positions = shift_db.active_positions()
    shift_db.assign_position("2026-09-18", "lunch", positions[0]["id"], "Marcus")
    shift_db.assign_position("2026-09-18", "lunch", positions[1]["id"], "Sarah")
    shift_db.assign_position("2026-09-19", "lunch", positions[0]["id"], "Marcus")  # already set

    assert shift_db.previous_lineup_date("2026-09-19", "lunch") == "2026-09-18"
    copied = shift_db.copy_lineup("2026-09-18", "lunch", "2026-09-19", "lunch")
    assert copied == 1  # only Sarah was new
    lineup = shift_db.lineup_for("2026-09-19", "lunch")
    assert len(lineup) == 2


def test_copy_lineup_across_dayparts(isolated_db):
    positions = shift_db.active_positions()
    shift_db.assign_position("2026-09-19", "lunch", positions[0]["id"], "Marcus")
    copied = shift_db.copy_lineup("2026-09-19", "lunch", "2026-09-19", "dinner")
    assert copied == 1
    assert len(shift_db.lineup_for("2026-09-19", "dinner")) == 1


def test_roster_add_reactivate_dedupe(isolated_db):
    assert shift_db.add_member("  Sarah   Jones ")
    assert not shift_db.add_member("   ")
    names = [m["name"] for m in shift_db.roster()]
    assert names == ["Sarah Jones"]

    member = shift_db.roster()[0]
    shift_db.set_member_active(member["id"], False)
    assert not shift_db.roster()

    assert shift_db.add_member("sarah jones", role="leader")  # reactivates, case-insens.
    ros = shift_db.roster()
    assert len(ros) == 1 and ros[0]["role"] == "leader"


def test_notes_feed_and_filter(isolated_db):
    shift_db.add_note("2026-09-19", "lunch", "win", "Record lunch!", "Joshua")
    shift_db.add_note("2026-09-19", "dinner", "equipment", "Fryer 2 acting up", "Aliyah")
    assert len(shift_db.notes_feed()) == 2
    assert len(shift_db.notes_feed(category="win")) == 1
    assert len(shift_db.notes_for_date("2026-09-19")) == 2

    note_id = shift_db.notes_feed()[0]["id"]
    shift_db.delete_note(note_id)
    assert len(shift_db.notes_feed()) == 1


def test_announcements_expiry_and_pinning(isolated_db):
    shift_db.add_announcement("Old news", "J", False, "2026-01-01")
    shift_db.add_announcement("Evergreen", "J", False, "")
    shift_db.add_announcement("Pinned!", "J", True, "")
    active = shift_db.active_announcements(today="2026-09-19")
    assert [a["body"] for a in active] == ["Pinned!", "Evergreen"]

    shift_db.delete_announcement(active[0]["id"])
    assert [a["body"] for a in shift_db.active_announcements(today="2026-09-19")] == ["Evergreen"]


def test_minimal_valid_import_does_not_wipe_silently(isolated_db):
    """A '{"format": 1}' payload is a legal (empty) backup — restoring it
    should empty the tables, which is why the UI demands a confirmation."""
    assert shift_db.import_json('{"format": 1}') is None
    assert not shift_db.all_templates(include_inactive=True)


def test_ack_missing_announcement_is_graceful(isolated_db):
    assert shift_db.ack_announcement(999, "Maya") is False
    shift_db.add_announcement("Real", "J", False, "")
    a = shift_db.active_announcements(today="2026-09-19")[0]
    assert shift_db.ack_announcement(a["id"], "Maya") is True


def test_assign_unknown_position_is_graceful(isolated_db):
    assert shift_db.assign_position("2026-09-19", "lunch", 999999, "Maya") is False
    assert not shift_db.lineup_for("2026-09-19", "lunch")


def test_get_note_and_get_leader(isolated_db):
    shift_db.add_note("2026-09-19", "", "general", "Hello", "Maya")
    note = shift_db.notes_feed()[0]
    assert shift_db.get_note(note["id"])["body"] == "Hello"
    assert shift_db.get_note(12345) is None

    shift_db.add_leader("Maya", "1234", role="admin")
    leader = shift_db.leaders()[0]
    assert shift_db.get_leader(leader["id"])["role"] == "admin"
    assert shift_db.get_leader(12345) is None


def test_announcement_acks(isolated_db):
    shift_db.add_announcement("New sauce SOP", "Joshua", False, "")
    a = shift_db.active_announcements(today="2026-09-19", reader="Maya")[0]
    assert not a["acked"] and a["ack_count"] == 0
    assert shift_db.unacked_count("Maya", today="2026-09-19") == 1

    shift_db.ack_announcement(a["id"], "Maya")
    shift_db.ack_announcement(a["id"], "Maya")  # idempotent
    a = shift_db.active_announcements(today="2026-09-19", reader="Maya")[0]
    assert a["acked"] and a["ack_count"] == 1
    assert shift_db.unacked_count("Maya", today="2026-09-19") == 0
    assert shift_db.unacked_count("Marcus", today="2026-09-19") == 1

    readers = shift_db.announcement_readers(a["id"])
    assert [r["reader"] for r in readers] == ["Maya"]


def test_leader_lifecycle(isolated_db):
    assert shift_db.add_leader("", "1234") is not None
    assert shift_db.add_leader("Maya", "12") is not None
    assert shift_db.add_leader("Operator", "1234") is not None  # reserved
    assert shift_db.add_leader("Maya", "1234") is None

    row = shift_db.verify_leader("maya", "1234")
    assert row and row["role"] == "lead"
    assert shift_db.verify_leader("Maya", "9999") is None
    assert shift_db.verify_leader("Nobody", "1234") is None
    assert shift_db.leader_is_active(row["id"])

    shift_db.set_leader_active(row["id"], False)
    assert shift_db.verify_leader("Maya", "1234") is None
    assert not shift_db.leader_is_active(row["id"])

    # Re-adding reactivates with a new PIN and role
    assert shift_db.add_leader("Maya", "5678", role="admin") is None
    row = shift_db.verify_leader("Maya", "5678")
    assert row and row["role"] == "admin"

    assert shift_db.reset_leader_pin(row["id"], "abc") is not None
    assert shift_db.reset_leader_pin(row["id"], "4321") is None
    assert shift_db.verify_leader("Maya", "4321")


def test_export_import_roundtrip(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    run_id = shift_db.runs_for_date("2026-09-19")[0]["id"]
    _, items = shift_db.run_with_items(run_id)
    shift_db.set_item_done(items[0]["id"], True, "Maya")
    shift_db.add_note("2026-09-19", "lunch", "win", "Great day", "Maya")
    shift_db.add_member("Marcus")
    shift_db.add_leader("Maya", "1234")

    raw = shift_db.export_json()
    assert "pin_hash" not in raw

    # Wipe some data, then restore
    shift_db.delete_note(shift_db.notes_feed()[0]["id"])
    assert shift_db.import_json(raw) is None

    assert len(shift_db.notes_feed()) == 1
    _, items = shift_db.run_with_items(run_id)
    assert items[0]["done"] == 1 and items[0]["done_by"] == "Maya"
    # Leader accounts come back with LOCKED PINs (hashes never leave the DB)
    leader = shift_db.leaders()[0]
    assert leader["name"] == "Maya"
    assert shift_db.verify_leader("Maya", "1234") is None
    assert shift_db.reset_leader_pin(leader["id"], "1234") is None
    assert shift_db.verify_leader("Maya", "1234")


def _table_counts():
    return {
        "templates": len(shift_db.all_templates(include_inactive=True)),
        "positions": len(shift_db.active_positions()),
        "notes": len(shift_db.notes_feed()),
    }


def test_import_rejects_garbage_without_touching_data(isolated_db):
    shift_db.add_note("2026-09-19", "lunch", "win", "Survivor", "Maya")
    before = _table_counts()
    assert before["templates"] and before["positions"] and before["notes"]

    bad_payloads = [
        "not json",
        "{}",
        '{"format": 1, "shift_notes": "nope"}',            # malformed section
        '{"format": 1, "shift_notes": [{"evil_column": 1}]}',  # no valid columns
        '{"format": 1, "shift_notes": [{"body": ["nested"]}]}',  # non-scalar value
        # FK-inconsistent: run item pointing at a run that doesn't exist
        '{"format": 1, "checklist_run_items": [{"id": 1, "run_id": 999, '
        '"label": "x", "critical": 0, "sort": 0, "done": 0}]}',
        # FK-inconsistent: team-member 1:1 topic for a roster row that isn't there
        '{"format": 1, "oneonone_member_topics": [{"id": 1, "member_id": 999, '
        '"topic": "x", "created_at": "2026-10-01 09:00"}]}',
    ]
    for raw in bad_payloads:
        assert shift_db.import_json(raw) is not None, raw
        assert _table_counts() == before, f"data changed after rejected import: {raw}"


def test_course_seeded(isolated_db):
    import shift_course
    expected = sum(len(lessons) for _, lessons in shift_course.COURSE)
    assert shift_db.course_lesson_count() == expected
    shift_db.init_db()  # idempotent
    assert shift_db.course_lesson_count() == expected


def test_course_seeds_into_existing_database(isolated_db):
    # Simulate a pre-course production DB: drop the course tables + flag,
    # keep the base seed flag, then boot again.
    from contextlib import closing
    with closing(shift_db.connect()) as conn, conn:
        conn.execute("DELETE FROM lesson_progress")
        conn.execute("DELETE FROM course_lessons")
        conn.execute("DELETE FROM meta WHERE key='course_seeded'")
    shift_db.init_db()
    assert shift_db.course_lesson_count() > 0
    assert len(shift_db.all_templates()) == SEEDED_LISTS  # not re-seeded


def test_lesson_progress_lifecycle(isolated_db):
    shift_db.add_leader("Maya", "1234")
    leader = shift_db.leaders()[0]
    modules = shift_db.course_overview(leader["id"])
    assert modules[0]["module"] == "Mindset 101"
    lesson = modules[0]["lessons"][0]
    assert not lesson["done"]

    assert shift_db.set_lesson_done(lesson["id"], leader["id"], "Great session", "Operator")
    modules = shift_db.course_overview(leader["id"])
    row = modules[0]["lessons"][0]
    assert row["done"] and row["note"] == "Great session"
    assert row["recorded_by"] == "Operator"
    first_completed = row["completed_at"]

    # Updating the note keeps the original completion date
    assert shift_db.set_lesson_done(lesson["id"], leader["id"], "Revisit ch. 2", "Operator")
    row = shift_db.course_overview(leader["id"])[0]["lessons"][0]
    assert row["note"] == "Revisit ch. 2" and row["completed_at"] == first_completed

    summary = shift_db.leader_course_summary()
    assert summary[0]["done"] == 1

    shift_db.clear_lesson_done(lesson["id"], leader["id"])
    assert shift_db.leader_course_summary()[0]["done"] == 0

    # Unknown ids are graceful
    assert shift_db.set_lesson_done(999999, leader["id"], "", "Op") is False
    assert shift_db.set_lesson_done(lesson["id"], 999999, "", "Op") is False


def test_summary_ignores_deactivated_lessons(isolated_db):
    from contextlib import closing
    shift_db.add_leader("Maya", "1234")
    leader = shift_db.leaders()[0]
    lessons = shift_db.course_overview(leader["id"])[0]["lessons"]
    shift_db.set_lesson_done(lessons[0]["id"], leader["id"], "", "Op")
    shift_db.set_lesson_done(lessons[1]["id"], leader["id"], "", "Op")

    with closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE course_lessons SET active=0 WHERE id=?",
                     (lessons[1]["id"],))

    # Admin summary, the leader page, and the total all agree
    summary = shift_db.leader_course_summary()[0]
    assert summary["done"] == 1
    modules = shift_db.course_overview(leader["id"])
    assert sum(m["done"] for m in modules) == 1
    assert shift_db.course_lesson_count() == sum(m["total"] for m in modules)

    # A stale form can't add progress on a retired lesson
    assert shift_db.set_lesson_done(lessons[1]["id"], leader["id"], "", "Op") is False


def test_restore_then_reboot_never_duplicates_course(isolated_db):
    # Two-step restore: an old (pre-course) backup first, then a current one,
    # then a boot — the course must not double-seed.
    current = shift_db.export_json()
    import json as _json
    old = _json.loads(current)
    for key in ("course_lessons", "lesson_progress"):
        old.pop(key, None)
    assert shift_db.import_json(_json.dumps(old)) is None       # clears flag
    assert shift_db.import_json(current) is None                # restores lessons
    shift_db.init_db()                                          # boot
    import shift_course
    assert shift_db.course_lesson_count() == \
        sum(len(lessons) for _, lessons in shift_course.COURSE)


def test_seed_flag_recovers_without_duplicating(isolated_db):
    # Lessons present but flag missing (e.g. interrupted maintenance):
    # boot records the flag instead of seeding duplicates.
    from contextlib import closing
    with closing(shift_db.connect()) as conn, conn:
        conn.execute("DELETE FROM meta WHERE key='course_seeded'")
    before = shift_db.course_lesson_count()
    shift_db.init_db()
    assert shift_db.course_lesson_count() == before
    with closing(shift_db.connect()) as conn:
        assert conn.execute(
            "SELECT 1 FROM meta WHERE key='course_seeded'").fetchone()


def test_task_lifecycle(isolated_db):
    shift_db.add_leader("Maya", "1234")
    leader = shift_db.leaders()[0]

    assert shift_db.add_task(leader["id"], "  Deep clean  fryer 2 ", "Before Friday",
                             "2020-01-01", "Operator")
    assert shift_db.add_task(leader["id"], "Read SERVE ch. 3", "", "", "Operator")
    assert not shift_db.add_task(leader["id"], "   ", "", "", "Operator")
    assert not shift_db.add_task(999999, "Ghost task", "", "", "Operator")

    open_tasks, done_tasks = shift_db.tasks_for_leader(leader["id"])
    assert [t["title"] for t in open_tasks] == ["Deep clean fryer 2", "Read SERVE ch. 3"]
    assert open_tasks[0]["overdue"] is True     # due 2020, dated tasks sort first
    assert open_tasks[1]["overdue"] is False
    assert not done_tasks

    summary = shift_db.leader_task_summary()[0]
    assert summary["open"] == 2 and summary["overdue"] == 1

    task = open_tasks[0]
    assert shift_db.set_task_done(task["id"], True, "Maya")
    open_tasks, done_tasks = shift_db.tasks_for_leader(leader["id"])
    assert len(open_tasks) == 1 and len(done_tasks) == 1
    assert done_tasks[0]["completed_by"] == "Maya" and done_tasks[0]["completed_at"]
    assert shift_db.leader_task_summary()[0]["open"] == 1

    assert shift_db.set_task_done(task["id"], False, "Maya")  # reopen
    open_tasks, _ = shift_db.tasks_for_leader(leader["id"])
    assert len(open_tasks) == 2
    assert open_tasks[0]["completed_at"] is None

    assert not shift_db.set_task_done(999999, True, "X")
    shift_db.delete_task(open_tasks[0]["id"])
    assert shift_db.leader_task_summary()[0]["open"] == 1
    assert shift_db.get_task(999999) is None


def test_task_summary_zero_task_leader_is_caught_up(isolated_db):
    shift_db.add_leader("Zoe", "3333")     # never assigned anything
    shift_db.add_leader("Maya", "1234")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    shift_db.add_task(maya["id"], "One task", "", "", "Operator")
    task = shift_db.tasks_for_leader(maya["id"])[0][0]
    shift_db.set_task_done(task["id"], True, "Maya")

    summary = {s["name"]: s for s in shift_db.leader_task_summary()}
    assert summary["Zoe"]["open"] == 0 and summary["Zoe"]["overdue"] == 0
    assert summary["Maya"]["open"] == 0   # all completed is also caught up


def test_tasks_survive_export_import(isolated_db):
    shift_db.add_leader("Maya", "1234")
    leader = shift_db.leaders()[0]
    shift_db.add_task(leader["id"], "Survive the restore", "", "", "Operator")
    raw = shift_db.export_json()
    open_tasks, _ = shift_db.tasks_for_leader(leader["id"])
    shift_db.delete_task(open_tasks[0]["id"])
    assert shift_db.import_json(raw) is None
    open_tasks, _ = shift_db.tasks_for_leader(leader["id"])
    assert open_tasks[0]["title"] == "Survive the restore"


def test_course_progress_survives_export_import(isolated_db):
    shift_db.add_leader("Maya", "1234")
    leader = shift_db.leaders()[0]
    lesson = shift_db.course_overview(leader["id"])[0]["lessons"][0]
    shift_db.set_lesson_done(lesson["id"], leader["id"], "note", "Operator")

    raw = shift_db.export_json()
    shift_db.clear_lesson_done(lesson["id"], leader["id"])
    assert shift_db.import_json(raw) is None
    assert shift_db.leader_course_summary()[0]["done"] == 1


# --- station cleaning lists + scorecard ---------------------------------------

def _tid(name):
    return next(t["id"] for t in shift_db.all_templates(include_inactive=True)
                if t["name"] == name)


def test_cleaning_sheets_seeded_as_posted(isolated_db):
    by_name = {t["name"]: t for t in shift_db.all_templates()}
    expect = {  # name: (frequency, daypart, items) — counted off the wall sheets
        "Dining Room & Washrooms — Daily": ("daily", "allday", 17),
        "Dining Room & Washrooms — Weekly": ("weekly", "allday", 9),
        "Dining Room & Washrooms — Monthly audit": ("monthly", "allday", 5),
        "Drive Thru — Daily": ("daily", "allday", 17),
        "Drive Thru — Weekly": ("weekly", "allday", 8),
        "Drive Thru — Monthly audit": ("monthly", "allday", 4),
        "Food Safety Commitment — AM": ("daily", "breakfast", 17),
        "Food Safety Commitment — PM": ("daily", "dinner", 17),
        "Sanitizer & spindles — 10:30 / 12:30": ("daily", "lunch", 4),
        "Sanitizer & spindles — 2:30 / 4:30": ("daily", "afternoon", 4),
        "Sanitizer & spindles — 6:30 / 8:30": ("daily", "dinner", 4),
    }
    for name, (freq, daypart, count) in expect.items():
        t = by_name[name]
        assert (t["frequency"], t["daypart"], t["item_count"]) == (freq, daypart, count), name
    _, items = shift_db.template_with_items(by_name["Sanitizer & spindles — 6:30 / 8:30"]["id"])
    assert [i["label"] for i in items] == [
        "6:30 — sanitizer changed", "6:30 — spindles cleaned",
        "8:30 — sanitizer changed", "8:30 — spindles cleaned"]
    assert all(i["critical"] for i in items)
    # Once only, and never duplicates a hand-made list with the same name
    shift_db.init_db()
    assert len(shift_db.all_templates()) == SEEDED_LISTS


def test_cleaning_seed_upgrades_an_existing_database(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "pre_cleaning.db"))
    conn = sqlite3.connect(shift_db.DB_PATH)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('seeded', '2026-09-01 09:00');
        CREATE TABLE checklist_templates (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            daypart TEXT NOT NULL, area TEXT NOT NULL DEFAULT 'All',
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
        INSERT INTO checklist_templates (name, daypart) VALUES ('FOH Opening', 'breakfast');
        INSERT INTO checklist_templates (name, daypart) VALUES ('Drive Thru — Daily', 'dinner');
        CREATE TABLE checklist_template_items (id INTEGER PRIMARY KEY,
            template_id INTEGER NOT NULL, label TEXT NOT NULL,
            critical INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0);
        INSERT INTO checklist_template_items (template_id, label) VALUES (1, 'Doors open');
        CREATE TABLE checklist_runs (id INTEGER PRIMARY KEY, template_id INTEGER NOT NULL,
            run_date TEXT NOT NULL, UNIQUE (template_id, run_date));
        INSERT INTO checklist_runs (template_id, run_date) VALUES (1, '2026-09-30');
    """)
    conn.commit()
    conn.close()
    shift_db.init_db()
    shift_db.init_db()   # idempotent
    names = [t["name"] for t in shift_db.all_templates()]
    assert names.count("Drive Thru — Daily") == 1          # hand-made one kept
    assert len(names) == 2 + len(shift_cleaning.LISTS) - 1
    old = next(t for t in shift_db.all_templates() if t["name"] == "FOH Opening")
    assert old["frequency"] == "daily"
    runs = shift_db.runs_for_date("2026-09-30")
    assert runs[0]["frequency"] == "daily" and runs[0]["owner"] is None
    with shift_db.closing(shift_db.connect()) as conn:     # checks count from now
        assert conn.execute(
            "SELECT value FROM meta WHERE key='manager_check_since'").fetchone()


def test_periods():
    assert shift_db.period_start("daily", "2026-10-15") == "2026-10-15"
    assert shift_db.period_start("weekly", "2026-10-15") == "2026-10-12"   # Thu → Mon
    assert shift_db.period_start("weekly", "2026-10-12") == "2026-10-12"
    assert shift_db.period_start("weekly", "2026-10-18") == "2026-10-12"   # Sun
    assert shift_db.period_start("monthly", "2026-10-31") == "2026-10-01"
    assert shift_db.period_end("weekly", "2026-10-12") == "2026-10-18"
    assert shift_db.period_end("monthly", "2026-02-01") == "2026-02-28"
    assert shift_db.period_end("monthly", "2026-12-01") == "2026-12-31"
    assert shift_db.period_label("weekly", "2026-10-12") == "Week of Oct 12"
    assert shift_db.period_label("monthly", "2026-10-01") == "October 2026"
    assert shift_db.run_is_current("weekly", "2026-10-12", today="2026-10-18")
    assert not shift_db.run_is_current("weekly", "2026-10-12", today="2026-10-19")
    assert not shift_db.run_is_current("daily", "2026-10-18", today="2026-10-19")


def test_weekly_and_monthly_runs_cover_their_period(isolated_db):
    weekly = _tid("Drive Thru — Weekly")
    monthly = _tid("Drive Thru — Monthly audit")
    for day in ["2026-10-13", "2026-10-15", "2026-10-18"]:
        shift_db.ensure_runs_for_date(day)
    with shift_db.closing(shift_db.connect()) as conn:
        rows = conn.execute(
            "SELECT template_id, run_date, frequency FROM checklist_runs "
            "WHERE template_id IN (?, ?)", (weekly, monthly)).fetchall()
    assert sorted(tuple(r) for r in rows) == [
        (weekly, "2026-10-12", "weekly"), (monthly, "2026-10-01", "monthly")]
    on_thursday = {r["name"]: r for r in shift_db.runs_for_date("2026-10-15")}
    assert on_thursday["Drive Thru — Weekly"]["period_label"] == "Week of Oct 12"
    assert on_thursday["Drive Thru — Monthly audit"]["period_end"] == "2026-10-31"
    daily = [r for r in on_thursday.values() if r["frequency"] == "daily"]
    assert all(r["run_date"] == "2026-10-15" for r in daily)


def test_owner_and_manager_check(isolated_db):
    shift_db.ensure_runs_for_date("2026-10-15")
    run = next(r for r in shift_db.runs_for_date("2026-10-15")
               if r["name"] == "Drive Thru — Daily")
    assert shift_db.set_run_owner(run["id"], "  Calla   Jonkman ")
    assert shift_db.set_run_verified(run["id"], True, "Riya")
    assert not shift_db.set_run_verified(run["id"], True, "Neha")   # first stamp wins
    fresh, _ = shift_db.run_with_items(run["id"])
    assert fresh["owner"] == "Calla Jonkman" and fresh["verified_by"] == "Riya"
    assert shift_db.set_run_verified(run["id"], False, "Riya")
    assert shift_db.run_with_items(run["id"])[0]["verified_at"] is None
    assert shift_db.set_run_owner(run["id"], "   ")
    assert shift_db.run_with_items(run["id"])[0]["owner"] is None


def _do(run_date, name, n=None, verify=False):
    """Check off the first n items (all if None) of `name`'s run on run_date."""
    shift_db.ensure_runs_for_date(run_date)
    run = next(r for r in shift_db.runs_for_date(run_date) if r["name"] == name)
    _, items = shift_db.run_with_items(run["id"])
    for i in items[: len(items) if n is None else n]:
        shift_db.set_item_done(i["id"], True, "Riya")
    if verify:
        shift_db.set_run_verified(run["id"], True, "Neha")
    return run


def test_scorecard_daily(isolated_db):
    name = "Sanitizer & spindles — 6:30 / 8:30"            # 4 items
    _do("2026-10-10", name)                                # Sat, first run: complete
    _do("2026-10-11", name, 0)                             # Sun: closed — opened, no count
    _do("2026-10-12", name, 2)                             # half
    # 10-13 never opened → counts as nothing done
    _do("2026-10-14", name, verify=True)
    _do("2026-10-15", name, 0)                             # opened, nothing done
    _do("2026-10-16", name)
    _do("2026-10-17", name)
    # 10-18 is a Sunday: closed, never opened, never counted
    _do("2026-10-19", name, 1)                             # today: in progress
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == name)
    assert card["current"]["done"] == 1 and card["current"]["total"] == 4
    hist = {h["start"]: h for h in card["history"]}
    assert min(hist) == "2026-10-10"                       # nothing before the first run
    assert "2026-10-11" not in hist and "2026-10-18" not in hist
    assert hist["2026-10-13"]["opened"] is False and hist["2026-10-13"]["done"] == 0
    assert hist["2026-10-12"]["pct"] == 50
    assert card["streak"] == 2                             # 16, 17 — Sunday doesn't break it
    r = card["recent"]                                     # last 7 open days: 10, 12..17
    assert r["periods"] == 7 and r["complete"] == 4        # 10, 14, 16, 17
    assert r["not_opened"] == 1 and r["verified"] == 1
    w = card["window"]                                     # back to the first run
    assert w["periods"] == 7
    assert w["pct"] == round((4 + 2 + 0 + 4 + 0 + 4 + 4) / 28 * 100)
    assert card["most_missed"][0]["label"].startswith("8:30")
    assert card["most_missed"][0]["missed"] == 2           # the half day + the zero day
    assert card["most_missed"][0]["of"] == 6               # opened, counted days


def test_scorecard_daily_window_is_open_days(isolated_db):
    """A perfect Monday–Saturday stretch reads 100%, not 86%."""
    from datetime import date, timedelta
    name = "Sanitizer & spindles — 6:30 / 8:30"
    d = date(2026, 9, 10)
    while d < date(2026, 10, 19):
        if d.weekday() != 6:
            _do(d.isoformat(), name)
        d += timedelta(days=1)
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == name)
    assert card["window"] == {"periods": 28, "pct": 100, "complete": 28,
                              "verified": 0, "verified_of": 28, "not_opened": 0}
    assert card["recent"]["pct"] == 100 and card["streak"] == 28


def test_scorecard_weekly_and_monthly(isolated_db):
    weekly = "Drive Thru — Weekly"                          # 8 items
    _do("2026-09-29", weekly)                              # week of Sep 28: done
    _do("2026-10-06", weekly, 4)                           # week of Oct 5: half
    # week of Oct 12: not opened
    _do("2026-10-19", weekly, 2)                           # this week, in progress
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == weekly)
    assert [h["start"] for h in card["history"]] == ["2026-10-12", "2026-10-05", "2026-09-28"]
    assert [h["complete"] for h in card["history"]] == [False, False, True]
    assert card["window"]["not_opened"] == 1 and card["streak"] == 0
    assert card["current"]["label"] == "Week of Oct 19"

    monthly = "Drive Thru — Monthly audit"                  # 4 items
    _do("2026-08-20", monthly)
    _do("2026-09-03", monthly, verify=True)
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == monthly)
    assert [h["start"] for h in card["history"]] == ["2026-09-01", "2026-08-01"]
    assert card["streak"] == 2 and card["window"]["verified"] == 1
    # October's run exists (opened above) but nothing is done yet
    assert card["current"]["done"] == 0 and card["current"]["label"] == "October 2026"


def test_scorecard_new_list_has_no_history(isolated_db):
    tid = shift_db.create_template("Patio sweep", "allday", "Patio",
                                   [("Sweep", False)], frequency="daily")
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["id"] == tid)
    assert card["history"] == [] and card["recent"] is None


def test_template_schedule_is_sanitized(isolated_db):
    tid = shift_db.create_template("Deep clean", "allday", "All", [("Scrub", False)],
                                   frequency="weekly", due_weekday=1)
    t, _ = shift_db.template_with_items(tid)
    assert (t["frequency"], t["due_weekday"]) == ("weekly", 1)
    shift_db.update_template(tid, "Deep clean", "allday", "All", [("Scrub", False)],
                             frequency="monthly", due_weekday=3)
    t, _ = shift_db.template_with_items(tid)
    assert (t["frequency"], t["due_weekday"]) == ("monthly", None)  # day only for weekly
    shift_db.update_template(tid, "Deep clean", "allday", "All", [("Scrub", False)],
                             frequency="hourly", due_weekday=9)
    t, _ = shift_db.template_with_items(tid)
    assert (t["frequency"], t["due_weekday"]) == ("daily", None)


def test_checklist_runs_survive_export_import(isolated_db):
    run = _do("2026-10-15", "Drive Thru — Daily", 3, verify=True)
    shift_db.set_run_owner(run["id"], "Calla")
    raw = shift_db.export_json()
    assert shift_db.import_json(raw) is None
    fresh, items = shift_db.run_with_items(run["id"])
    assert fresh["owner"] == "Calla" and fresh["verified_by"] == "Neha"
    assert sum(i["done"] for i in items) == 3
    payload = json.loads(raw)                             # a pre-release backup
    for t in payload["checklist_templates"]:
        del t["frequency"], t["due_weekday"]
    for r in payload["checklist_runs"]:
        del r["frequency"], r["owner"], r["verified_by"], r["verified_at"]
    del payload["checklist_schedule_log"]
    assert shift_db.import_json(json.dumps(payload)) is None
    assert all(t["frequency"] == "daily" for t in shift_db.all_templates())
    with shift_db.closing(shift_db.connect()) as conn:     # no Manager check back then
        assert conn.execute(
            "SELECT value FROM meta WHERE key='manager_check_since'").fetchone()


def test_scorecard_monthly_window_reaches_back_three_months(isolated_db):
    monthly = "Drive Thru — Monthly audit"
    for day in ("2026-10-05", "2026-11-05", "2026-12-05"):
        _do(day, monthly)
    for today in ("2027-01-09", "2027-01-15", "2027-01-31"):   # was cut off from ~10th
        card = next(c for c in shift_db.checklist_scorecard(today=today)
                    if c["name"] == monthly)
        assert [h["start"] for h in card["history"]] == [
            "2026-12-01", "2026-11-01", "2026-10-01"]
        assert card["window"]["pct"] == 100 and card["window"]["not_opened"] == 0


def test_frequency_change_takes_effect_this_period(isolated_db, monkeypatch):
    tid = shift_db.create_template("Deep clean", "allday", "All",
                                   [("Scrub", False), ("Mop", False)])
    for day in ("2026-10-01", "2026-10-19", "2026-10-20", "2026-10-21"):
        _do(day, "Deep clean")                             # daily runs, Mon 19 incl.
    monkeypatch.setattr(shift_db, "today_local", lambda: "2026-10-21")
    shift_db.update_template(tid, "Deep clean", "allday", "All",
                             [("Scrub", False), ("Mop", False)], frequency="weekly")
    shift_db.ensure_runs_for_date("2026-10-22")
    mine = [r for r in shift_db.runs_for_date("2026-10-22") if r["name"] == "Deep clean"]
    assert [(r["frequency"], r["run_date"], r["done"]) for r in mine] == [
        ("weekly", "2026-10-19", 0)]                        # not blocked by Monday's daily
    monday = [r for r in shift_db.runs_for_date("2026-10-19") if r["name"] == "Deep clean"]
    assert ("daily", 2) in [(r["frequency"], r["done"]) for r in monday]  # history kept
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-22")
                if c["id"] == tid)
    assert card["history"] == []                           # weekly tracking starts fresh
    assert card["current"]["run_id"] == mine[0]["id"]

    shift_db.update_template(tid, "Deep clean", "allday", "All",
                             [("Scrub", False), ("Mop", False)], frequency="monthly")
    shift_db.ensure_runs_for_date("2026-10-22")
    oct_runs = [r for r in shift_db.runs_for_date("2026-10-22")
                if r["name"] == "Deep clean" and r["frequency"] == "monthly"]
    assert [r["run_date"] for r in oct_runs] == ["2026-10-01"]  # not blocked by Oct 1's daily

    # The app is used daily while it's monthly, then it goes back to daily:
    # those days aren't "not opened" — but days nobody opened the app are.
    from datetime import date, timedelta
    d = date(2026, 10, 23)
    while d < date(2026, 11, 2):
        if d.weekday() != 6:
            shift_db.ensure_runs_for_date(d.isoformat())
        d += timedelta(days=1)
    monkeypatch.setattr(shift_db, "today_local", lambda: "2026-11-02")
    shift_db.update_template(tid, "Deep clean", "allday", "All",
                             [("Scrub", False), ("Mop", False)], frequency="daily")
    for day in ("2026-11-02", "2026-11-03"):
        _do(day, "Deep clean")
    card = next(c for c in shift_db.checklist_scorecard(today="2026-11-04")
                if c["id"] == tid)
    hist = {h["start"]: h for h in card["history"]}
    # Oct 21 ended monthly (switched mid-day), so it owed no daily list.
    assert not [s for s in hist if "2026-10-21" <= s <= "2026-10-31"]
    assert all(hist[s]["complete"] for s in ("2026-11-03", "2026-11-02",
                                            "2026-10-20", "2026-10-19"))
    assert hist["2026-10-13"]["opened"] is False           # nobody opened the app


def test_retired_stretch_is_not_counted(isolated_db):
    from datetime import date, timedelta
    name = "Sanitizer & spindles — 6:30 / 8:30"
    weekly = "Drive Thru — Weekly"
    ids = {t["name"]: t["id"] for t in shift_db.all_templates()}
    _do("2026-09-25", name)
    # Sat 09-26: nobody opened the app — a real miss, before the retirement
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        _do(day, name)
    _do("2026-09-29", weekly)
    shift_db.set_template_active(ids[name], False)        # retired Oct 1–14 …
    shift_db.set_template_active(ids[weekly], False)      # … weekly until Sun Oct 18
    d = date(2026, 10, 1)
    while d < date(2026, 10, 18):                          # the app is used meanwhile
        if d.weekday() != 6:
            if d == date(2026, 10, 15):
                shift_db.set_template_active(ids[name], True)
            shift_db.ensure_runs_for_date(d.isoformat())
            if d >= date(2026, 10, 15):
                _do(d.isoformat(), name)
        d += timedelta(days=1)
    shift_db.set_template_active(ids[weekly], True)
    cards = {c["name"]: c for c in shift_db.checklist_scorecard(today="2026-10-19")}
    card = cards[name]
    assert [h["start"] for h in card["history"]] == [
        "2026-10-17", "2026-10-16", "2026-10-15", "2026-09-30", "2026-09-29",
        "2026-09-28", "2026-09-26", "2026-09-25"]
    assert card["window"]["not_opened"] == 1 and card["streak"] == 6
    # Toggling again (or a frequency round trip) can't erase that real miss.
    shift_db.set_template_active(ids[name], False)
    shift_db.set_template_active(ids[name], True)
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == name)
    assert card["window"]["not_opened"] == 1
    # The weekly list skips the weeks it was off (restored on a closed Sunday);
    # the week of Sep 21 counts — the app was opened, nothing was checked.
    assert [(h["start"], h["done"]) for h in cards[weekly]["history"]] == [
        ("2026-09-28", 8), ("2026-09-21", 0)]


def test_retired_period_list_drops_off_until_worked(isolated_db):
    _do("2026-10-20", "Drive Thru — Weekly", 0)
    _do("2026-10-20", "Dining Room & Washrooms — Weekly", 1)
    for t in shift_db.all_templates():
        if t["name"] in ("Drive Thru — Weekly", "Dining Room & Washrooms — Weekly"):
            shift_db.set_template_active(t["id"], False)
    names = {r["name"]: r["scheduled"] for r in shift_db.runs_for_date("2026-10-24")
             if r["frequency"] == "weekly"}
    assert names == {"Dining Room & Washrooms — Weekly": False}  # empty one gone; work kept


def test_off_schedule_period_runs(isolated_db, monkeypatch):
    _do("2026-10-06", "Drive Thru — Weekly", 0)            # a missed past week
    _do("2026-10-20", "Drive Thru — Weekly", 0)            # this week, nothing yet
    tid = next(t["id"] for t in shift_db.all_templates()
               if t["name"] == "Drive Thru — Weekly")
    t, items = shift_db.template_with_items(tid)
    shift_db.update_template(tid, t["name"], t["daypart"], t["area"],
                             [(i["label"], bool(i["critical"])) for i in items],
                             frequency="daily")            # moved to daily mid-week
    shift_db.ensure_runs_for_date("2026-10-21")
    this_week = [(r["frequency"], r["scheduled"]) for r in shift_db.runs_for_date("2026-10-21")
                 if r["name"] == "Drive Thru — Weekly"]
    assert this_week == [("daily", True)]                  # stale weekly run gone
    past = [r for r in shift_db.runs_for_date("2026-10-07")
            if r["name"] == "Drive Thru — Weekly"]
    assert [(r["frequency"], r["done"]) for r in past] == [("weekly", 0)]  # record kept
    shift_db.set_template_active(tid, False)
    assert [r["id"] for r in shift_db.runs_for_date("2026-10-07")
            if r["name"] == "Drive Thru — Weekly"] == [past[0]["id"]]


def test_closed_sunday_starts_no_spent_period(isolated_db):
    weekly = lambda d: [r for r in shift_db.runs_for_date(d) if r["frequency"] == "weekly"]
    shift_db.ensure_runs_for_date("2026-10-11")            # Sunday: the week is spent
    assert weekly("2026-10-11") == []
    shift_db.ensure_runs_for_date("2027-01-31")            # Sunday the 31st
    assert not [r for r in shift_db.runs_for_date("2027-01-31")
                if r["frequency"] == "monthly"]
    shift_db.ensure_runs_for_date("2027-01-30")            # Saturday: still open
    assert len([r for r in shift_db.runs_for_date("2027-01-30")
                if r["frequency"] == "monthly"]) == 2
    # A list set up on a Sunday starts with the next week, not a 0% one.
    tid = shift_db.create_template("Patio", "allday", "Patio", [("Sweep", False)],
                                   frequency="weekly")
    shift_db.ensure_runs_for_date("2026-10-18")            # Sunday
    for day in ("2026-10-19", "2026-10-24"):
        _do(day, "Patio")
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-26")
                if c["id"] == tid)
    assert [(h["start"], h["complete"]) for h in card["history"]] == [("2026-10-19", True)]


def test_edits_never_touch_existing_runs(isolated_db):
    """A page left open must never check a different item: edits and
    retirements leave existing runs, their items and ids, exactly as is."""
    today = shift_db.today_local()                         # a current, untouched list
    shift_db.ensure_runs_for_date(today)
    run = next(r for r in shift_db.runs_for_date(today)
               if r["name"] == "Sanitizer & spindles — 6:30 / 8:30")
    before = [(i["id"], i["label"]) for i in shift_db.run_with_items(run["id"])[1]]
    tid = next(t["id"] for t in shift_db.all_templates() if t["name"] == run["name"])
    t, items = shift_db.template_with_items(tid)
    shift_db.update_template(tid, t["name"], t["daypart"], t["area"],
                             [("Sanitizer bucket re-made", True)]
                             + [(i["label"], bool(i["critical"])) for i in items])
    shift_db.set_template_active(tid, False)
    shift_db.set_template_active(tid, True)
    assert [(i["id"], i["label"]) for i in shift_db.run_with_items(run["id"])[1]] == before


def test_schedule_log_decides_what_a_list_owed(isolated_db, monkeypatch):
    from datetime import date, timedelta
    clock = {"day": "2026-09-01"}
    monkeypatch.setattr(shift_db, "today_local", lambda: clock["day"])
    ids = {t["name"]: t["id"] for t in shift_db.all_templates()}

    def use_app(first, last):                               # open days only
        d = date.fromisoformat(first)
        while d <= date.fromisoformat(last):
            if d.weekday() != 6:
                shift_db.ensure_runs_for_date(d.isoformat())
            d += timedelta(days=1)

    def reschedule(name, frequency, day):
        clock["day"] = day
        t, items = shift_db.template_with_items(ids[name])
        shift_db.update_template(t["id"], t["name"], t["daypart"], t["area"],
                                 [(i["label"], bool(i["critical"])) for i in items],
                                 frequency=frequency)

    # An ignored week stays a miss even if the list is retired on the Sunday.
    use_app("2026-10-05", "2026-10-10")
    clock["day"] = "2026-10-11"
    shift_db.set_template_active(ids["Drive Thru — Weekly"], False)
    shift_db.set_template_active(ids["Drive Thru — Weekly"], True)
    # Retired mid-week (with only an Owner set): that week isn't owed.
    use_app("2026-10-12", "2026-10-13")
    weekly = next(r for r in shift_db.runs_for_date("2026-10-13")
                  if r["name"] == "Dining Room & Washrooms — Weekly")
    shift_db.set_run_owner(weekly["id"], "Jordan")
    clock["day"] = "2026-10-14"
    shift_db.set_template_active(ids["Dining Room & Washrooms — Weekly"], False)
    use_app("2026-10-14", "2026-10-17")
    clock["day"] = "2026-10-19"
    shift_db.set_template_active(ids["Dining Room & Washrooms — Weekly"], True)
    use_app("2026-10-19", "2026-10-24")
    cards = {c["name"]: c for c in shift_db.checklist_scorecard(today="2026-10-26")}
    assert [(h["start"], h["done"]) for h in cards["Drive Thru — Weekly"]["history"]] == [
        ("2026-10-19", 0), ("2026-10-12", 0), ("2026-10-05", 0)]
    assert [h["start"] for h in
            cards["Dining Room & Washrooms — Weekly"]["history"]] == [
        "2026-10-19", "2026-10-05"]

    # A mistaken switch to monthly and straight back leaves a stub that
    # never counts, even once the list really goes monthly.
    reschedule("Drive Thru — Weekly", "monthly", "2026-10-27")
    use_app("2026-10-27", "2026-10-27")                     # the stub is created
    reschedule("Drive Thru — Weekly", "weekly", "2026-10-27")
    use_app("2026-10-28", "2026-11-28")
    reschedule("Drive Thru — Weekly", "monthly", "2026-12-01")
    for day in ("2026-12-01", "2026-12-15"):
        _do(day, "Drive Thru — Weekly")
    card = next(c for c in shift_db.checklist_scorecard(today="2027-01-04")
                if c["name"] == "Drive Thru — Weekly")
    assert [(h["start"], h["complete"]) for h in card["history"]] == [("2026-12-01", True)]


def test_manager_checks_count_from_when_the_button_arrived(isolated_db):
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('manager_check_since', "
                     "'2026-10-15')")
    name = "Sanitizer & spindles — 6:30 / 8:30"
    for day in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"):
        _do(day, name)
    for day in ("2026-10-16", "2026-10-17"):
        _do(day, name, verify=True)
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == name)
    assert (card["window"]["verified"], card["window"]["verified_of"]) == (2, 3)


def test_a_repeat_check_keeps_the_first_stamp(isolated_db, monkeypatch):
    from datetime import datetime
    run = _do("2026-10-19", "Drive Thru — Weekly", 0)
    _, items = shift_db.run_with_items(run["id"])
    monkeypatch.setattr(shift_db, "now_local", lambda: datetime(2026, 10, 19, 13, 0))
    assert shift_db.set_item_done(items[0]["id"], True, "Maya")
    monkeypatch.setattr(shift_db, "now_local", lambda: datetime(2026, 10, 21, 15, 0))
    assert shift_db.set_item_done(items[0]["id"], True, "Ben")   # stale screen
    item = shift_db.run_with_items(run["id"])[1][0]
    assert (item["done_by"], item["done_at"]) == ("Maya", "2026-10-19 13:00")
    assert not shift_db.set_item_done(999999, True, "Ben")


def test_weekly_runs_are_evidence_too(isolated_db):
    """Even with no daily lists at all, a week another weekly list ran
    proves the app was used: a retired weekly list skips it."""
    for t in shift_db.all_templates():
        if t["frequency"] == "daily":
            shift_db.set_template_active(t["id"], False)
    ids = {t["name"]: t["id"] for t in shift_db.all_templates()}
    _do("2026-09-28", "Drive Thru — Weekly")
    shift_db.set_template_active(ids["Drive Thru — Weekly"], False)
    _do("2026-10-05", "Dining Room & Washrooms — Weekly")   # app used that week
    shift_db.set_template_active(ids["Drive Thru — Weekly"], True)
    _do("2026-10-12", "Drive Thru — Weekly")
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == "Drive Thru — Weekly")
    assert [h["start"] for h in card["history"]] == ["2026-10-12", "2026-09-28"]


def test_closed_day_cannot_be_a_due_day(isolated_db):
    tid = shift_db.create_template("Patio", "allday", "Patio", [("Sweep", False)],
                                   frequency="weekly", due_weekday=6)   # Sunday
    assert shift_db.template_with_items(tid)[0]["due_weekday"] is None


def test_scorecard_tolerates_odd_frequency_values(isolated_db):
    name = "Sanitizer & spindles — 6:30 / 8:30"
    for day in ("2026-10-15", "2026-10-16"):
        _do(day, name)
    with shift_db.closing(shift_db.connect()) as conn, conn:   # e.g. a hand-edited backup
        conn.execute("UPDATE checklist_templates SET frequency='Daily' WHERE name=?",
                     (name,))
    card = next(c for c in shift_db.checklist_scorecard(today="2026-10-19")
                if c["name"] == name)
    assert card["frequency"] == "daily"
    assert [h["opened"] for h in card["history"]] == [False, True, True]  # 17th unopened


def test_rebuild_tolerates_orphans_it_did_not_make(isolated_db):
    import sqlite3
    run = _do("2026-10-15", "Drive Thru — Daily", 2)
    conn = sqlite3.connect(shift_db.DB_PATH)               # FKs off, like a manual fix
    old_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='checklist_runs'"
                           ).fetchone()[0].replace("UNIQUE (template_id, frequency, run_date)",
                                                   "UNIQUE (template_id, run_date)")
    conn.executescript(f"""
        BEGIN;
        {old_sql.replace('CREATE TABLE checklist_runs', 'CREATE TABLE runs_old')};
        INSERT INTO runs_old SELECT * FROM checklist_runs;
        DROP TABLE checklist_runs;
        ALTER TABLE runs_old RENAME TO checklist_runs;
        INSERT INTO checklist_run_items (run_id, label) VALUES (999999, 'orphan');
        COMMIT;
    """)
    conn.close()
    shift_db.init_db()                                     # boots instead of raising
    assert sum(i["done"] for i in shift_db.run_with_items(run["id"])[1]) == 2


def test_old_run_key_is_rebuilt_without_losing_items(isolated_db):
    import sqlite3
    run = _do("2026-10-15", "Drive Thru — Daily", 3, verify=True)
    conn = sqlite3.connect(shift_db.DB_PATH)               # put the old key back
    conn.execute("PRAGMA foreign_keys=OFF")
    old_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='checklist_runs'"
                           ).fetchone()[0].replace("UNIQUE (template_id, frequency, run_date)",
                                                   "UNIQUE (template_id, run_date)")
    conn.executescript(f"""
        BEGIN;
        {old_sql.replace('CREATE TABLE checklist_runs', 'CREATE TABLE runs_old')};
        INSERT INTO runs_old SELECT * FROM checklist_runs;
        DROP TABLE checklist_runs;
        ALTER TABLE runs_old RENAME TO checklist_runs;
        COMMIT;
    """)
    assert "UNIQUE (template_id, run_date)" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='checklist_runs'").fetchone()[0]
    conn.close()
    shift_db.init_db()
    shift_db.init_db()                                     # idempotent
    with shift_db.closing(shift_db.connect()) as c:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name='checklist_runs'"
                        ).fetchone()[0]
        assert "UNIQUE (template_id, frequency, run_date)" in sql
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='idx_checklist_runs_date'"
                         ).fetchone()
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    fresh, items = shift_db.run_with_items(run["id"])      # same id, items attached
    assert fresh["verified_by"] == "Neha" and sum(i["done"] for i in items) == 3
    with shift_db.closing(shift_db.connect()) as c, c:     # cascade still wired
        c.execute("DELETE FROM checklist_runs WHERE id=?", (run["id"],))
        assert not c.execute("SELECT 1 FROM checklist_run_items WHERE run_id=?",
                             (run["id"],)).fetchone()


def test_pre_cleaning_backup_restore_seeds_the_lists_back(isolated_db):
    payload = json.loads(shift_db.export_json())
    payload["checklist_templates"] = [t for t in payload["checklist_templates"]
                                      if t["name"] in [n for n, *_ in shift_db.SEED_TEMPLATES]]
    keep = {t["id"] for t in payload["checklist_templates"]}
    payload["checklist_template_items"] = [i for i in payload["checklist_template_items"]
                                           if i["template_id"] in keep]
    for t in payload["checklist_templates"]:
        del t["frequency"], t["due_weekday"]
    del payload["checklist_schedule_log"]                  # didn't exist yet either
    assert shift_db.import_json(json.dumps(payload)) is None
    assert len(shift_db.all_templates()) == SEEDED_LISTS
    with shift_db.closing(shift_db.connect()) as conn:     # every list has a baseline
        assert conn.execute(
            "SELECT COUNT(DISTINCT template_id) FROM checklist_schedule_log").fetchone()[0] \
            == SEEDED_LISTS
    # A post-cleaning backup the Operator trimmed is restored as-is.
    payload = json.loads(shift_db.export_json())
    payload["checklist_templates"] = [t for t in payload["checklist_templates"]
                                      if t["name"] != "Drive Thru — Weekly"]
    kept = {t["id"] for t in payload["checklist_templates"]}
    for table in ("checklist_template_items", "checklist_schedule_log"):
        payload[table] = [r for r in payload[table] if r["template_id"] in kept]
    assert shift_db.import_json(json.dumps(payload)) is None
    shift_db.init_db()
    assert len(shift_db.all_templates()) == SEEDED_LISTS - 1


# --- 1:1 agendas -----------------------------------------------------------

def test_oneonone_topic_lifecycle(isolated_db):
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]

    assert not shift_db.add_oneonone_topic(maya["id"], "   ", "Maya")
    assert not shift_db.add_oneonone_topic(999999, "Ghost", "Maya")
    assert shift_db.add_oneonone_topic(maya["id"], "  Saturday   staffing ", "Maya")
    assert shift_db.add_oneonone_topic(maya["id"], "Catering van", "Operator")

    agenda, history = shift_db.oneonone_for_leader(maya["id"])
    assert [t["topic"] for t in agenda] == ["Saturday staffing", "Catering van"]
    assert not history and not agenda[0]["carried"]

    # Discuss one with a note: stamps who/when/date, grouped into history
    t1 = agenda[0]
    assert shift_db.set_topic_discussed(t1["id"], True, " hire two ", "Operator")
    agenda, history = shift_db.oneonone_for_leader(maya["id"])
    assert [t["topic"] for t in agenda] == ["Catering van"]
    assert history[0]["date"] == shift_db.today_local()
    assert history[0]["topics"][0]["outcome_note"] == "hire two"
    assert history[0]["topics"][0]["discussed_by"] == "Operator"
    # Added the same business day as the meeting → NOT carried (the badge
    # means "survived a previous 1:1", not "existed on meeting day")...
    assert not agenda[0]["carried"]
    # ...but a topic created before the last meeting day is carried.
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE oneonone_topics SET created_at=? WHERE id=?",
                     ("2020-01-01 10:00", agenda[0]["id"]))
    agenda, _ = shift_db.oneonone_for_leader(maya["id"])
    assert agenda[0]["carried"]

    # Guarded flip: a second discuss is a no-op keeping the first stamp
    assert not shift_db.set_topic_discussed(t1["id"], True, "other", "Maya")
    assert shift_db.get_topic(t1["id"])["discussed_by"] == "Operator"

    # Reopen clears all four stamp columns; reopen-again is a no-op
    assert shift_db.set_topic_discussed(t1["id"], False, "", "Maya")
    fresh = shift_db.get_topic(t1["id"])
    assert fresh["discussed_at"] is None and fresh["discussed_by"] is None \
        and fresh["discussed_on"] is None and fresh["outcome_note"] is None
    assert not shift_db.set_topic_discussed(t1["id"], False, "", "Maya")

    # Delete is SQL-guarded to undiscussed topics
    assert shift_db.set_topic_discussed(t1["id"], True, "", "Operator")
    assert not shift_db.delete_topic(t1["id"])          # history now
    t2 = shift_db.oneonone_for_leader(maya["id"])[0][0]
    assert shift_db.delete_topic(t2["id"])
    assert shift_db.get_topic(t2["id"]) is None


def test_oneonone_summary_and_badge(isolated_db):
    shift_db.add_leader("Maya", "4721")
    shift_db.add_leader("Devon", "8888")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    shift_db.add_oneonone_topic(maya["id"], "Topic A", "Maya")
    shift_db.add_oneonone_topic(maya["id"], "Topic B", "Operator")

    rows = {r["name"]: r for r in shift_db.oneonone_summary()}
    assert rows["Maya"]["open"] == 2 and rows["Maya"]["last_met"] is None
    assert rows["Devon"]["open"] == 0                   # LEFT-JOIN null row safe
    assert shift_db.open_topic_count(maya["id"]) == 2
    assert shift_db.open_topic_count(None) == 0         # Operator session


def test_goals_leader_tag_filters(isolated_db):
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]
    store = shift_db.create_goal("DT under 4", "", "DT", "sec", 240, "down",
                                 "weekly", "", "Operator")
    personal = shift_db.create_goal("Solo breakfast open", "", "", "", None,
                                    "up", "monthly", "", "Operator",
                                    leader_id=maya["id"])
    bogus = shift_db.create_goal("Ghost-tagged", "", "", "", None, "up",
                                 "weekly", "", "Operator", leader_id=999999)

    all_ids = {g["id"] for g in shift_db.goals_by_status("active")}
    assert all_ids == {store, personal, bogus}
    store_ids = {g["id"] for g in shift_db.goals_by_status("active", store_only=True)}
    assert store_ids == {store, bogus}                  # bogus tag stored as NULL
    maya_goals = shift_db.goals_by_status("active", leader_id=maya["id"])
    assert [g["id"] for g in maya_goals] == [personal]
    assert maya_goals[0]["leader_name"] == "Maya"
    g, _ = shift_db.goal_with_updates(personal)
    assert g["leader_name"] == "Maya"


def test_migration_adds_goals_leader_id(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "old.db"))
    conn = sqlite3.connect(shift_db.DB_PATH)
    conn.executescript("""
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, why TEXT,
            metric TEXT, unit TEXT, target_value REAL,
            direction TEXT NOT NULL DEFAULT 'up',
            period TEXT NOT NULL DEFAULT 'weekly', due_date TEXT,
            status TEXT NOT NULL DEFAULT 'active', created_by TEXT,
            created_at TEXT NOT NULL);
        INSERT INTO goals (title, created_at) VALUES ('Pre-upgrade', '2026-10-01 09:00');
    """)
    conn.commit()
    conn.close()
    shift_db.init_db()
    shift_db.init_db()   # idempotent
    goals = shift_db.goals_by_status("active")
    assert goals[0]["title"] == "Pre-upgrade" and goals[0]["leader_id"] is None


def test_oneonone_and_shoutouts_survive_export_import(isolated_db):
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]
    shift_db.add_oneonone_topic(maya["id"], "Carry me", "Maya")
    gid = shift_db.create_goal("Personal", "", "", "", None, "up", "weekly",
                               "", "Operator", leader_id=maya["id"])
    sid = shift_db.add_shoutout("Avery", "speed", "Flew through the rush",
                                "Maya", share=True)
    raw = shift_db.export_json()

    shift_db.delete_topic(shift_db.oneonone_for_leader(maya["id"])[0][0]["id"])
    shift_db.delete_shoutout(sid)
    shift_db.set_goal_status(gid, "archived")
    # The restore's DELETE FROM leaders must not abort on the tagged goal
    assert shift_db.import_json(raw) is None

    agenda, _ = shift_db.oneonone_for_leader(maya["id"])
    assert agenda[0]["topic"] == "Carry me"
    assert shift_db.shoutout_feed()[0]["member_name"] == "Avery"
    assert shift_db.goals_by_status("active",
                                    leader_id=maya["id"])[0]["id"] == gid


# --- team-member 1:1s (Operator-only) ---------------------------------------

def _member_id(name):
    return next(m["id"] for m in shift_db.roster(include_inactive=True)
                if m["name"] == name)


def test_add_leader_may_replace_guard(isolated_db):
    assert shift_db.add_leader("Maya", "4721") is None
    err = shift_db.add_leader(" maya ", "0000", role="admin", may_replace=False)
    assert err and "only the Operator" in err
    maya = shift_db.leaders()[0]
    assert maya["role"] == "lead" and shift_db.verify_leader("Maya", "4721")
    assert shift_db.add_leader("Devon", "8888", may_replace=False) is None
    assert shift_db.add_leader("Maya", "1357") is None    # Operator path
    assert shift_db.verify_leader("Maya", "1357")


def test_migration_adds_member_oneonone_table(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "prod.db"))
    shift_db.init_db()
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]
    shift_db.add_oneonone_topic(maya["id"], "Open topic", "Maya")
    shift_db.add_oneonone_topic(maya["id"], "Discussed topic", "Operator")
    t = shift_db.oneonone_for_leader(maya["id"])[0][1]
    shift_db.set_topic_discussed(t["id"], True, "kept", "Operator")
    # Simulate the pre-upgrade production DB: no member table, no index.
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("DROP TABLE oneonone_member_topics")
    with shift_db.closing(shift_db.connect()) as conn:
        before = [tuple(r) for r in conn.execute(
            "SELECT * FROM oneonone_topics ORDER BY id")]

    shift_db.init_db()
    shift_db.init_db()   # idempotent

    with shift_db.closing(shift_db.connect()) as conn:
        after = [tuple(r) for r in conn.execute(
            "SELECT * FROM oneonone_topics ORDER BY id")]
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master")}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert after == before                       # leader 1:1 data untouched
    assert {"oneonone_member_topics", "idx_oneonone_member_topics"} <= names
    shift_db.add_member("Avery")
    assert shift_db.add_member_topic(_member_id("Avery"), "Works", "Operator")


def test_member_topics_fk_is_cascade(isolated_db):
    # CASCADE is restore-critical: import_json clears team_members first.
    with shift_db.closing(shift_db.connect()) as conn:
        fks = [dict(r) for r in conn.execute(
            "PRAGMA foreign_key_list(oneonone_member_topics)")]
    by_col = {fk["from"]: fk for fk in fks}
    assert set(by_col) == {"member_id", "holder_leader_id"}
    assert by_col["member_id"]["table"] == "team_members"
    assert by_col["member_id"]["on_delete"] == "CASCADE"
    # SET NULL, not NO ACTION: the restore's DELETE FROM leaders must not
    # abort while leader-held threads exist.
    assert by_col["holder_leader_id"]["table"] == "leaders"
    assert by_col["holder_leader_id"]["on_delete"] == "SET NULL"


def test_member_oneonone_lifecycle(isolated_db):
    shift_db.add_member("Avery")
    av = _member_id("Avery")

    assert not shift_db.add_member_topic(av, "   ", "Operator")
    assert not shift_db.add_member_topic(999999, "Ghost", "Operator")
    assert shift_db.add_member_topic(av, "  Cross-train   breading ", "Operator")
    assert shift_db.add_member_topic(av, "Interested in leading", "Operator")

    agenda, history = shift_db.oneonone_for_member(av)
    assert [t["topic"] for t in agenda] == ["Cross-train breading",
                                            "Interested in leading"]
    assert not history and not agenda[0]["carried"]

    t1 = agenda[0]
    assert shift_db.set_member_topic_discussed(t1["id"], True, " shadow Sat ",
                                               "Operator")
    agenda, history = shift_db.oneonone_for_member(av)
    assert [t["topic"] for t in agenda] == ["Interested in leading"]
    assert history[0]["date"] == shift_db.today_local()
    assert history[0]["topics"][0]["outcome_note"] == "shadow Sat"
    assert not agenda[0]["carried"]                     # same business day
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE oneonone_member_topics SET created_at=? WHERE id=?",
                     ("2020-01-01 10:00", agenda[0]["id"]))
    assert shift_db.oneonone_for_member(av)[0][0]["carried"]

    # Guarded flip, reopen clears all four stamps, delete guarded
    assert not shift_db.set_member_topic_discussed(t1["id"], True, "x", "Operator")
    assert shift_db.set_member_topic_discussed(t1["id"], False, "", "Operator")
    fresh = shift_db.get_member_topic(t1["id"])
    assert fresh["discussed_at"] is None and fresh["discussed_by"] is None \
        and fresh["discussed_on"] is None and fresh["outcome_note"] is None
    assert shift_db.set_member_topic_discussed(t1["id"], True, "", "Operator")
    assert not shift_db.delete_member_topic(t1["id"])   # history now
    t2 = shift_db.oneonone_for_member(av)[0][0]
    assert shift_db.delete_member_topic(t2["id"])
    assert shift_db.get_member_topic(t2["id"]) is None


def test_member_threads_isolated_from_leader_threads(isolated_db):
    # Fresh DB: leader Maya and roster member Avery are BOTH id 1.
    shift_db.add_leader("Maya", "4721")
    shift_db.add_member("Avery")
    maya = shift_db.leaders()[0]
    av = _member_id("Avery")
    assert maya["id"] == av == 1

    shift_db.add_member_topic(av, "Operator-only note", "Operator")
    member_tid = shift_db.oneonone_for_member(av)[0][0]["id"]
    assert shift_db.oneonone_for_leader(maya["id"]) == ([], [])
    assert shift_db.open_topic_count(maya["id"]) == 0
    assert shift_db.oneonone_summary()[0]["open"] == 0
    # The leader-side helpers can never see or delete the member row
    assert shift_db.get_topic(member_tid) is None
    assert not shift_db.delete_topic(member_tid)
    assert not shift_db.set_topic_discussed(member_tid, True, "", "Maya")
    assert shift_db.get_member_topic(member_tid)["discussed_at"] is None

    shift_db.add_oneonone_topic(maya["id"], "Shared topic", "Maya")
    shift_db.add_oneonone_topic(maya["id"], "Second shared", "Maya")
    leader_tid = shift_db.oneonone_for_leader(maya["id"])[0][1]["id"]
    assert leader_tid != member_tid
    assert shift_db.get_member_topic(leader_tid) is None


def test_member_oneonone_index_grouping(isolated_db):
    for n in ["Avery", "Blake", "Casey", "Drew", "maya", "Riley"]:
        shift_db.add_member(n)
    shift_db.add_leader("Maya", "4721")
    shift_db.add_leader("Riley", "5151")
    riley_login = next(l for l in shift_db.leaders() if l["name"] == "Riley")
    shift_db.set_leader_active(riley_login["id"], False)

    shift_db.add_member_topic(_member_id("Blake"), "Open one", "Operator")
    shift_db.add_member_topic(_member_id("Avery"), "Done one", "Operator")
    t = shift_db.oneonone_for_member(_member_id("Avery"))[0][0]
    shift_db.set_member_topic_discussed(t["id"], True, "", "Operator")
    shift_db.add_member_topic(_member_id("Casey"), "Former's topic", "Operator")
    shift_db.set_member_active(_member_id("Casey"), False)
    shift_db.set_member_active(_member_id("Drew"), False)   # no history

    idx = shift_db.member_oneonone_index()
    assert [r["name"] for r in idx["threads"]] == ["Blake", "Avery"]  # open first
    assert idx["threads"][0]["open"] == 1
    everyone = [r["name"] for r in idx["everyone"]]
    # Roster 'maya' matches an active leader login → lives under Leaders;
    # Riley's login is deactivated → an ordinary team member again.
    assert everyone == ["Avery", "Blake", "Riley"]
    assert all(r["initial"] == r["name"][0].upper() for r in idx["everyone"])
    assert [r["name"] for r in idx["former"]] == ["Casey"]   # Drew absent
    riley = next(r for r in idx["everyone"] if r["name"] == "Riley")
    assert riley["leader_id"] is None

    # A pre-promotion member thread stays listed, tagged with the login
    shift_db.add_member_topic(_member_id("maya"), "Before promotion", "Operator")
    row = next(r for r in shift_db.member_oneonone_index()["threads"]
               if r["name"] == "maya")
    assert row["leader_id"] == next(l["id"] for l in shift_db.leaders()
                                    if l["name"] == "Maya")


def test_oneonone_people(isolated_db):
    for n in ["Avery", "maya", "Old Timer"]:
        shift_db.add_member(n)
    shift_db.set_member_active(_member_id("Old Timer"), False)
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]

    people = shift_db.oneonone_people()
    assert [(p["kind"], p["id"], p["name"]) for p in people] == [
        ("member", _member_id("Avery"), "Avery"),
        ("leader", maya["id"], "Maya")]                  # one Maya, no Old Timer

    # Deactivating the login turns the roster 'maya' back into a team member
    shift_db.set_leader_active(maya["id"], False)
    assert [(p["kind"], p["name"]) for p in shift_db.oneonone_people()] == [
        ("member", "Avery"), ("member", "maya")]


def test_member_oneonone_survives_export_import(isolated_db):
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]
    shift_db.add_oneonone_topic(maya["id"], "Leader topic", "Maya")
    shift_db.add_member("Avery")
    av = _member_id("Avery")
    shift_db.add_member_topic(av, "Open member topic", "Operator")
    shift_db.add_member_topic(av, "Discussed member topic", "Operator")
    t = shift_db.oneonone_for_member(av)[0][1]
    shift_db.set_member_topic_discussed(t["id"], True, "great talk", "Operator")
    raw = shift_db.export_json()
    assert "Open member topic" in raw

    shift_db.delete_member_topic(shift_db.oneonone_for_member(av)[0][0]["id"])
    # DELETE FROM team_members must cascade cleanly, not abort the restore
    assert shift_db.import_json(raw) is None
    agenda, history = shift_db.oneonone_for_member(av)
    assert [x["topic"] for x in agenda] == ["Open member topic"]
    assert history[0]["topics"][0]["outcome_note"] == "great talk"
    assert history[0]["topics"][0]["member_id"] == av
    assert shift_db.oneonone_for_leader(maya["id"])[0][0]["topic"] == "Leader topic"


def test_old_backup_without_member_section_restores(isolated_db):
    shift_db.add_leader("Maya", "4721")
    maya = shift_db.leaders()[0]
    shift_db.add_oneonone_topic(maya["id"], "Leader topic", "Maya")
    shift_db.add_member("Avery")
    payload = json.loads(shift_db.export_json())
    del payload["oneonone_member_topics"]               # pre-release backup
    shift_db.add_member_topic(_member_id("Avery"), "After backup", "Operator")
    assert shift_db.import_json(json.dumps(payload)) is None
    assert shift_db.oneonone_for_leader(maya["id"])[0][0]["topic"] == "Leader topic"
    assert shift_db.oneonone_for_member(_member_id("Avery")) == ([], [])


def test_migration_adds_member_topic_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "pre_holder.db"))
    shift_db.init_db()
    shift_db.add_member("Avery")
    av = _member_id("Avery")
    shift_db.add_member_topic(av, "Operator's existing note", "Operator")
    # Simulate the production table from before leader-held threads
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.executescript("""
            DROP INDEX IF EXISTS idx_oneonone_member_holder;
            CREATE TABLE omt_old AS SELECT id, member_id, topic, added_by,
                created_at, discussed_at, discussed_by, discussed_on, outcome_note
                FROM oneonone_member_topics;
            DROP TABLE oneonone_member_topics;
            ALTER TABLE omt_old RENAME TO oneonone_member_topics;
        """)
    shift_db.init_db()
    shift_db.init_db()   # idempotent
    agenda, _ = shift_db.oneonone_for_member(av)          # still the Operator's
    assert [t["topic"] for t in agenda] == ["Operator's existing note"]
    assert agenda[0]["holder_leader_id"] is None
    with shift_db.closing(shift_db.connect()) as conn:
        idx = [r["name"] for r in conn.execute(
            "PRAGMA index_list(oneonone_member_topics)")]
    assert "idx_oneonone_member_holder" in idx


def test_member_threads_isolated_per_holder(isolated_db):
    shift_db.add_leader("Maya", "4721")
    shift_db.add_leader("Devon", "8888")
    maya, devon = (next(l for l in shift_db.leaders() if l["name"] == n)
                   for n in ("Maya", "Devon"))
    shift_db.add_member("Avery")
    av = _member_id("Avery")
    assert shift_db.add_member_topic(av, "Op topic", "Operator")
    assert shift_db.add_member_topic(av, "Maya topic", "Maya", holder_id=maya["id"])
    assert not shift_db.add_member_topic(av, "Ghost", "X", holder_id=999999)

    assert [t["topic"] for t in shift_db.oneonone_for_member(av)[0]] == ["Op topic"]
    assert [t["topic"] for t in shift_db.oneonone_for_member(av, maya["id"])[0]] == ["Maya topic"]
    assert shift_db.oneonone_for_member(av, devon["id"]) == ([], [])

    op_idx = shift_db.member_oneonone_index()
    maya_idx = shift_db.member_oneonone_index(holder_id=maya["id"])
    devon_idx = shift_db.member_oneonone_index(holder_id=devon["id"])
    assert op_idx["threads"][0]["open"] == 1
    assert maya_idx["threads"][0]["open"] == 1
    assert devon_idx["threads"] == [] and len(devon_idx["everyone"]) == 1

    assert shift_db.open_member_topic_count(maya["id"]) == 1
    assert shift_db.open_member_topic_count(devon["id"]) == 0
    assert shift_db.open_member_topic_count(None) == 0
    overview = shift_db.leader_member_threads()
    assert [(t["member_name"], t["holder_name"], t["open"]) for t in overview] == [
        ("Avery", "Maya", 1)]                               # Operator's own excluded

    # Restore with leader-held threads: DELETE FROM leaders must not abort
    raw = shift_db.export_json()
    assert shift_db.import_json(raw) is None
    assert [t["topic"] for t in shift_db.oneonone_for_member(av, maya["id"])[0]] == ["Maya topic"]
    payload = json.loads(raw)
    for row in payload["oneonone_member_topics"]:
        del row["holder_leader_id"]                         # pre-release backup
    assert shift_db.import_json(json.dumps(payload)) is None
    assert len(shift_db.oneonone_for_member(av)[0]) == 2    # all back to the Operator


# --- roster pull from Slack #general -----------------------------------------

def _slack_person(uid, name, display=""):
    return {"slack_id": uid, "name": name, "display": display}


def test_migration_adds_team_members_slack_id(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "old_roster.db"))
    conn = sqlite3.connect(shift_db.DB_PATH)
    conn.executescript("""
        CREATE TABLE team_members (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            role TEXT NOT NULL DEFAULT 'team',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL);
        INSERT INTO team_members (name, created_at) VALUES ('Calla', '2026-09-01 09:00');
        CREATE TABLE leaders (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            pin_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'lead',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL);
        INSERT INTO leaders (name, pin_hash, created_at) VALUES ('Riya', 'x', '2026-09-01 09:00');
    """)
    conn.commit()
    conn.close()
    shift_db.init_db()
    shift_db.init_db()   # idempotent
    rows = shift_db.roster()
    assert [(r["name"], r["slack_id"]) for r in rows] == [("Calla", None)]
    with shift_db.closing(shift_db.connect()) as c:
        idx = [r["name"] for r in c.execute("PRAGMA index_list(team_members)")]
    assert "idx_team_members_slack" in idx
    riya = shift_db.leaders()[0]
    assert riya["name"] == "Riya" and riya["slack_id"] is None
    with shift_db.closing(shift_db.connect()) as c:
        idx = [r["name"] for r in c.execute("PRAGMA index_list(leaders)")]
    assert "idx_leaders_slack" in idx


def test_sync_roster_from_slack(isolated_db):
    shift_db.add_leader("Riya", "1111")
    shift_db.add_leader("Neha", "2222")
    shift_db.add_member("Calla")                    # typed into a lineup once
    shift_db.add_member("Shelly Montiyagala")
    shift_db.add_member("Old Hand")
    shift_db.set_member_active(_member_id("Old Hand"), False)
    people = [
        _slack_person("U1", "Riya Ronny Thomas", "Riya"),   # leader (display)
        _slack_person("U2", "Neha Ambookkan", "NeHa"),      # leader (first name)
        _slack_person("U3", "Calla Jonkman"),               # links to "Calla"
        _slack_person("U4", "Shelly Montiyagala"),          # exact name link
        _slack_person("U5", "Old Hand"),                    # removed: stays removed
        _slack_person("U6", "Keira Van Dinther", "Keira"),  # new
        _slack_person("U7", "Tushar"),                      # new, one word
    ]
    out = shift_db.sync_roster_from_slack([dict(p) for p in people])
    assert sorted(out["leaders"]) == ["Neha Ambookkan", "Riya Ronny Thomas"]
    assert sorted(out["added"]) == ["Keira Van Dinther", "Tushar"]
    assert sorted(out["linked"]) == ["Calla → Calla Jonkman", "Shelly Montiyagala"]
    assert out["removed_kept"] == ["Old Hand"]
    assert not out["ambiguous"] and not out["duplicates"]

    active = {r["name"]: r["slack_id"] for r in shift_db.roster()}
    assert active == {"Calla": "U3", "Keira Van Dinther": "U6",
                      "Shelly Montiyagala": "U4", "Tushar": "U7"}   # no renames
    gone = next(r for r in shift_db.roster(include_inactive=True)
                if r["name"] == "Old Hand")
    assert gone["active"] == 0 and gone["slack_id"] == "U5"
    assert shift_db.slack_roster_synced_at()

    # Second pull: nothing new, nothing reactivated, nothing duplicated
    again = shift_db.sync_roster_from_slack([dict(p) for p in people])
    assert again["added"] == [] and again["linked"] == []
    assert sorted(again["already"]) == ["Calla", "Keira Van Dinther",
                                        "Shelly Montiyagala", "Tushar"]
    assert again["removed_kept"] == ["Old Hand"]
    assert len(shift_db.roster(include_inactive=True)) == 5

    # The picker lists each person once: leaders under logins, team by row
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert ("member", "Riya Ronny Thomas") not in picker
    assert picker.count(("leader", "Riya")) == 1


def test_sync_roster_ambiguity_rules(isolated_db):
    shift_db.add_leader("Sam", "1111")
    shift_db.add_member("Jordan")
    out = shift_db.sync_roster_from_slack([
        _slack_person("U1", "Sam Kennedy"), _slack_person("U2", "Sam Torres"),
        _slack_person("U3", "Jordan Bischop"), _slack_person("U4", "Jordan Lee"),
        _slack_person("U5", "Alex Kim"), _slack_person("U6", "alex kim"),
    ])
    # Two Sams claim leader "Sam": neither is assumed to be the login
    assert sorted(out["ambiguous"]) == ["Sam Kennedy", "Sam Torres"]
    assert out["leaders"] == []
    # Two Jordans: the one-word "Jordan" row is left alone, both added
    assert "Jordan Bischop" in out["added"] and "Jordan Lee" in out["added"]
    assert next(r for r in shift_db.roster() if r["name"] == "Jordan")["slack_id"] is None
    # Same full name twice: the second is reported, never a crash
    assert out["duplicates"] == ["alex kim"]


def test_sync_never_pins_a_new_hire_to_a_removed_row(isolated_db):
    # A former "Sam" (one word, removed, private history) and, later, a
    # different Sam joins Slack: he must be added, not hidden on the old row.
    shift_db.add_member("Sam")
    old_sam = _member_id("Sam")
    shift_db.add_member_topic(old_sam, "Old private note", "Operator")
    shift_db.set_member_active(old_sam, False)
    shift_db.add_member("Mason")                          # removed one-word twin
    shift_db.set_member_active(_member_id("Mason"), False)
    out = shift_db.sync_roster_from_slack([
        _slack_person("UNEW", "Sam Jones"), _slack_person("UMAS", "Mason")])
    assert out["added"] == ["Sam Jones"]
    assert out["removed_kept"] == ["Mason"]               # reported, not linked
    rows = {r["name"]: r for r in shift_db.roster(include_inactive=True)}
    assert rows["Sam"]["slack_id"] is None and rows["Sam"]["active"] == 0
    assert rows["Mason"]["slack_id"] is None
    assert rows["Sam Jones"]["active"] == 1
    # Restoring the removed Mason later lets the next pull link him
    shift_db.set_member_active(rows["Mason"]["id"], True)
    again = shift_db.sync_roster_from_slack([_slack_person("UMAS", "Mason")])
    assert again["linked"] == ["Mason"]


def test_sync_never_links_a_leaders_lineup_row(isolated_db):
    # Leader "Sammy" (Slack: Samantha Lee, display Sammy) typed herself
    # into a lineup as "Sammy"; team member Sammy Jones must not take it.
    shift_db.add_leader("Sammy", "1111")
    shift_db.add_member("Sammy")
    out = shift_db.sync_roster_from_slack([
        _slack_person("ULEAD", "Samantha Lee", "Sammy"),
        _slack_person("UTM", "Sammy Jones")])
    assert out["leaders"] == ["Samantha Lee"]             # exact display wins
    assert out["added"] == ["Sammy Jones"] and not out["ambiguous"]
    # Her own lineup row is linked to HER account (so it stays hidden),
    # never to Sammy Jones
    assert next(r for r in shift_db.roster() if r["name"] == "Sammy")["slack_id"] == "ULEAD"
    assert shift_db.leaders()[0]["slack_id"] == "ULEAD"
    team = [p["name"] for p in shift_db.oneonone_people() if p["kind"] == "member"]
    assert team == ["Sammy Jones"]                        # leader listed once


def test_sync_exact_display_beats_first_name_collision(isolated_db):
    shift_db.add_leader("Riya", "1111")
    out = shift_db.sync_roster_from_slack([
        _slack_person("URIYA", "Riya Ronny Thomas", "Riya"),
        _slack_person("USING", "Riya Singh")])
    assert out["leaders"] == ["Riya Ronny Thomas"] and not out["ambiguous"]
    assert out["added"] == ["Riya Singh"]
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert picker == [("leader", "Riya"), ("member", "Riya Singh")]


def test_sync_promotion_hides_the_roster_twin(isolated_db):
    # Pulled as a team member first; later gets a leader login "Aron"
    shift_db.sync_roster_from_slack([_slack_person("UARON", "Aron Mathew")])
    aron_row = _member_id("Aron Mathew")
    shift_db.add_member_topic(aron_row, "Pre-promotion note", "Operator")
    shift_db.add_leader("Aron", "1111")
    out = shift_db.sync_roster_from_slack([_slack_person("UARON", "Aron Mathew")])
    assert out["leaders"] == ["Aron Mathew"]
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert picker == [("leader", "Aron")]                 # listed once
    member = shift_db.get_member(aron_row)
    assert shift_db.active_leader_for_member(member)["name"] == "Aron"
    thread = next(r for r in shift_db.member_oneonone_index()["threads"]
                  if r["id"] == aron_row)
    assert thread["leader_id"]                            # "now a leader" tag
    # The remembered Slack account survives a backup restore
    raw = shift_db.export_json()
    assert shift_db.import_json(raw) is None
    assert next(l for l in shift_db.leaders() if l["name"] == "Aron")["slack_id"] == "UARON"


def test_sync_ambiguous_leader_claimants_are_listed_and_reported(isolated_db):
    shift_db.add_leader("Chloe", "1111")
    out = shift_db.sync_roster_from_slack([
        _slack_person("U1", "Chloe Hill"), _slack_person("U2", "Chloe Nguyen")])
    assert sorted(out["ambiguous"]) == ["Chloe Hill", "Chloe Nguyen"]
    assert sorted(out["added"]) == ["Chloe Hill", "Chloe Nguyen"]
    # The fix the flash suggests: Chloe sets her Slack display name
    out = shift_db.sync_roster_from_slack([
        _slack_person("U1", "Chloe Hill", "Chloe"), _slack_person("U2", "Chloe Nguyen")])
    assert out["leaders"] == ["Chloe Hill"] and not out["ambiguous"]
    team = [p["name"] for p in shift_db.oneonone_people() if p["kind"] == "member"]
    assert team == ["Chloe Nguyen"]


def test_promotion_after_seed_lists_the_leader_once(isolated_db):
    import shift_roster_seed
    shift_db.add_leader("Riya", "1111")
    shift_db.apply_roster_snapshot(shift_roster_seed.PEOPLE)
    assert ("member", "Keira Van Dinther") in [
        (p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    shift_db.add_leader("Keira", "2222")                  # promoted later
    assert shift_db.relink_leaders_from_roster() == ["Keira → Keira Van Dinther"]
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert ("leader", "Keira") in picker
    assert ("member", "Keira Van Dinther") not in picker
    keira_row = shift_db.get_member(_member_id("Keira Van Dinther"))
    assert shift_db.active_leader_for_member(keira_row)["name"] == "Keira"
    assert shift_db.relink_leaders_from_roster() == []    # idempotent


def test_wrong_first_name_guess_heals_on_exact_match(isolated_db):
    shift_db.add_leader("Pru", "1111")
    people = [_slack_person("UPRU", "Prudence MacLennan"),
              _slack_person("UNEW", "Pru Smith")]
    out = shift_db.sync_roster_from_slack([dict(p) for p in people])
    assert out["leader_guesses"] == ["Pru → Pru Smith"]   # visible in the flash
    assert out["added"] == ["Prudence MacLennan"]
    # Prudence sets her Slack display name to her login, as the flash says
    people[0]["display"] = "Pru"
    out = shift_db.sync_roster_from_slack([dict(p) for p in people])
    assert out["leaders"] == ["Prudence MacLennan"]
    assert out["added"] == ["Pru Smith"]                  # no longer hidden
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert picker == [("leader", "Pru"), ("member", "Pru Smith")]  # Prudence hidden


def test_leader_roster_row_under_slack_name_is_hidden(isolated_db):
    shift_db.add_leader("Neha", "1111")
    shift_db.add_member("Neha Ambookkan")                 # typed in full once
    out = shift_db.sync_roster_from_slack([_slack_person("UNEHA", "Neha Ambookkan", "NeHa")])
    assert out["leaders"] == ["Neha Ambookkan"]
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert picker == [("leader", "Neha")]                 # listed once


def test_deactivated_leader_links_their_lineup_row(isolated_db):
    shift_db.add_leader("Keira", "1111")
    keira = shift_db.leaders()[0]
    shift_db.set_leader_active(keira["id"], False)        # stepped back to team
    shift_db.add_member("Keira")                          # her lineup row
    out = shift_db.sync_roster_from_slack([_slack_person("UK", "Keira Van Dinther", "Keira")])
    assert out["linked"] == ["Keira → Keira Van Dinther"] and not out["added"]
    team = [p["name"] for p in shift_db.oneonone_people() if p["kind"] == "member"]
    assert team == ["Keira"]                              # once, as a team member
    # Back as a leader: listed once, under Leaders
    shift_db.set_leader_active(keira["id"], True)
    picker = [(p["kind"], p["name"]) for p in shift_db.oneonone_people()]
    assert picker == [("leader", "Keira")]


def test_unmatched_leaders_reported_and_multiword_logins(isolated_db):
    shift_db.add_leader("Riya Thomas", "1111")            # multi-word login
    shift_db.add_leader("Pru", "2222")                    # not in Slack at all
    out = shift_db.sync_roster_from_slack([_slack_person("URIYA", "Riya Ronny Thomas")])
    assert out["leaders"] == ["Riya Ronny Thomas"]        # first word of the login
    assert out["unmatched_leaders"] == ["Pru"]


def test_claim_slack_roster_autosync(isolated_db):
    assert not shift_db.claim_slack_roster_autosync()     # never pulled
    shift_db.sync_roster_from_slack([_slack_person("U1", "Avery Sharpe")])
    assert not shift_db.claim_slack_roster_autosync()     # just pulled
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE meta SET value='2020-01-01 09:00' "
                     "WHERE key='slack_roster_synced_at'")
    assert shift_db.claim_slack_roster_autosync()         # due → slot taken
    assert not shift_db.claim_slack_roster_autosync()     # attempted just now


def test_slack_ids_survive_export_import(isolated_db):
    shift_db.sync_roster_from_slack([_slack_person("U9", "Casee Konecny")])
    raw = shift_db.export_json()
    payload = json.loads(raw)
    assert shift_db.import_json(raw) is None
    assert shift_db.roster()[0]["slack_id"] == "U9"
    for row in payload["team_members"]:
        del row["slack_id"]                                 # pre-release backup
    assert shift_db.import_json(json.dumps(payload)) is None
    assert shift_db.roster()[0]["slack_id"] is None


def test_roster_snapshot_seed(isolated_db):
    import shift_roster_seed
    for i, n in enumerate(["Aron", "Avery", "Chloe", "Grace", "Jubina", "Neha",
                           "Pru", "Riya", "Samira", "Sammy", "Sarath"]):
        shift_db.add_leader(n, f"{4000 + i}")
    shift_db.add_member("Calla")                          # typed in a lineup
    out = shift_db.apply_roster_snapshot(shift_roster_seed.PEOPLE)
    assert len(out["leaders"]) == 11 and not out["ambiguous"]
    assert len(out["added"]) == 26 and out["linked"] == ["Calla → Calla Jonkman"]
    team = [p for p in shift_db.oneonone_people() if p["kind"] == "member"]
    assert len(team) == 27                                # the whole team, once
    assert not any("Huesser" in p["name"] for p in team)  # Operator left out
    # Seed, not a live pull: no "updated" stamp, no daily auto-refresh
    assert shift_db.slack_roster_synced_at() is None
    assert not shift_db.claim_slack_roster_autosync()
    # Once only — removing someone afterwards sticks
    shift_db.set_member_active(_member_id("Mason"), False)
    assert shift_db.apply_roster_snapshot(shift_roster_seed.PEOPLE) is None
    assert not next(r for r in shift_db.roster(include_inactive=True)
                    if r["name"] == "Mason")["active"]


def test_roster_snapshot_skipped_after_live_pull(isolated_db):
    import shift_roster_seed
    shift_db.sync_roster_from_slack([_slack_person("U1", "Avery Sharpe")])
    assert shift_db.apply_roster_snapshot(shift_roster_seed.PEOPLE) is None
    assert [r["name"] for r in shift_db.roster()] == ["Avery Sharpe"]


def test_roster_snapshot_data_is_names_only():
    import shift_roster_seed
    ids = [p["slack_id"] for p in shift_roster_seed.PEOPLE]
    assert len(ids) == len(set(ids))
    for p in shift_roster_seed.PEOPLE:
        assert set(p) == {"slack_id", "name", "display"}
        assert "@" not in p["name"] + p["display"]          # never emails
        assert p["name"] == p["name"].strip() and p["name"]


# --- shout-outs --------------------------------------------------------------

def test_shoutout_lifecycle(isolated_db):
    assert shift_db.add_shoutout("  ", "speed", "msg", "Maya") is None
    assert shift_db.add_shoutout("Avery", "speed", "  ", "Maya") is None
    sid = shift_db.add_shoutout(" Avery  P ", "not-a-tag", " Great save ",
                                "Maya", share=True)
    rec = shift_db.get_shoutout(sid)
    assert rec["member_name"] == "Avery P" and rec["message"] == "Great save"
    assert rec["value_tag"] is None                     # bad tag coerced
    assert rec["shared_at"]

    quiet = shift_db.add_shoutout("Sam", "teamwork", "Covered a break", "Neha")
    assert shift_db.get_shoutout(quiet)["shared_at"] is None

    assert [s["id"] for s in shift_db.shoutout_feed()] == [quiet, sid]
    assert [s["id"] for s in shift_db.shoutout_feed(value_tag="teamwork")] == [quiet]
    assert len(shift_db.recent_shoutouts(limit=1)) == 1

    counts = shift_db.shoutout_counts()
    assert {"name": "Avery P", "count": 1} in counts["receivers"]
    assert {"name": "Maya", "count": 1} in counts["givers"]

    shift_db.delete_shoutout(sid)
    assert shift_db.get_shoutout(sid) is None


def test_recent_shoutouts_window(isolated_db):
    from datetime import date, timedelta
    old = shift_db.add_shoutout("Old", None, "ancient praise", "Maya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE shoutouts SET shout_date=? WHERE id=?",
                     ((date.fromisoformat(shift_db.today_local())
                       - timedelta(days=7)).isoformat(), old))
    fresh = shift_db.add_shoutout("Fresh", None, "today praise", "Maya")
    recent = shift_db.recent_shoutouts(days=7)
    assert [s["id"] for s in recent] == [fresh]


# --- guest recovery --------------------------------------------------------

def test_recovery_lifecycle(isolated_db):
    # Name is whitespace-collapsed; unknown enums coerce to "other"
    rid = shift_db.add_recovery("  Jordan   Lee ", "(519) 555-0142", "",
                                "bogus-issue", "bogus-remedy",
                                " cold fries ", True, "Riya")
    assert rid
    assert shift_db.add_recovery("   ", "", "", "order-error", "refund",
                                 "", False, "Riya") is None

    rec = shift_db.get_recovery(rid)
    assert rec["guest_name"] == "Jordan Lee"
    assert rec["issue"] == "other" and rec["remedy"] == "other"
    assert rec["details"] == "cold fries"
    assert rec["follow_up"] == 1 and rec["logged_by"] == "Riya"

    open_recs, awaiting, resolved = shift_db.recovery_feed()
    assert [r["id"] for r in open_recs] == [rid] and not resolved
    assert open_recs[0]["age_days"] == 0 and not open_recs[0]["stale"]
    assert open_recs[0]["phone_digits"] == "5195550142"
    assert shift_db.open_recovery_count() == 1

    # Resolve stamps who/when/note; reopen clears all three
    assert shift_db.set_recovery_resolved(rid, True, " called, card mailed ", "Neha")
    rec = shift_db.get_recovery(rid)
    assert rec["resolved_at"] and rec["resolved_by"] == "Neha"
    assert rec["resolution_note"] == "called, card mailed"
    open_recs, awaiting, resolved = shift_db.recovery_feed()
    assert not open_recs and resolved[0]["id"] == rid

    assert shift_db.set_recovery_resolved(rid, False, "", "Neha")
    rec = shift_db.get_recovery(rid)
    assert rec["resolved_at"] is None and rec["resolved_by"] is None \
        and rec["resolution_note"] is None
    assert not shift_db.set_recovery_resolved(999999, True, "", "Neha")

    shift_db.delete_recovery(rid)
    assert shift_db.get_recovery(rid) is None


def test_recovery_open_queue_oldest_first_and_stale(isolated_db):
    rid_old = shift_db.add_recovery("Old Guest", "", "", "wait-time",
                                    "free-entree-card", "", False, "Riya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE guest_recoveries SET recovery_date=? WHERE id=?",
                     ("2020-01-01", rid_old))
    rid_new = shift_db.add_recovery("New Guest", "", "", "service",
                                    "refund", "", False, "Riya")
    open_recs, _, _ = shift_db.recovery_feed()
    assert [r["id"] for r in open_recs] == [rid_old, rid_new]
    assert open_recs[0]["stale"] and open_recs[0]["age_days"] >= 2
    # phone-less rows render the "can't call back" path
    assert open_recs[0]["phone_digits"] == ""


def test_recovery_double_resolve_keeps_first_stamp(isolated_db):
    rid = shift_db.add_recovery("Jordan", "", "", "service", "refund",
                                "", False, "Riya")
    assert shift_db.set_recovery_resolved(rid, True, "called, cards mailed", "Maya")
    # Second resolve from a stale page is a no-op, not an overwrite
    assert not shift_db.set_recovery_resolved(rid, True, "", "Devon")
    rec = shift_db.get_recovery(rid)
    assert rec["resolved_by"] == "Maya"
    assert rec["resolution_note"] == "called, cards mailed"
    # Reopening an already-open row is likewise a no-op
    assert shift_db.set_recovery_resolved(rid, False, "", "Devon")
    assert not shift_db.set_recovery_resolved(rid, False, "", "Maya")


def test_recovery_contact_lifecycle(isolated_db):
    rid = shift_db.add_recovery("Jordan", "", "", "order-error",
                                "free-entree-card", "", True, "Riya")
    assert shift_db.set_recovery_contacted(rid, True, " coming Sat ", "Maya")
    open_recs, awaiting, resolved = shift_db.recovery_feed()
    assert not open_recs and not resolved
    assert awaiting[0]["id"] == rid
    assert awaiting[0]["contacted_by"] == "Maya"
    assert awaiting[0]["contact_note"] == "coming Sat"

    # Double-contact from a stale page is a no-op keeping the first stamp
    assert not shift_db.set_recovery_contacted(rid, True, "", "Devon")
    assert shift_db.get_recovery(rid)["contacted_by"] == "Maya"

    # Resolving from awaiting keeps the contact stamp for the record
    assert shift_db.set_recovery_resolved(rid, True, "picked up", "Neha")
    rec = shift_db.get_recovery(rid)
    assert rec["contacted_by"] == "Maya" and rec["resolved_by"] == "Neha"

    # Contacting a resolved row is refused
    assert not shift_db.set_recovery_contacted(rid, True, "", "Devon")

    # Reopening a contacted+resolved row returns it to the awaiting bucket
    assert shift_db.set_recovery_resolved(rid, False, "", "Neha")
    assert shift_db.recovery_feed()[1][0]["id"] == rid

    # Undo contact puts it back in the open queue; undo-on-open is a no-op
    assert shift_db.set_recovery_contacted(rid, False, "", "Maya")
    rec = shift_db.get_recovery(rid)
    assert rec["contacted_at"] is None and rec["contact_note"] is None
    assert shift_db.recovery_feed()[0][0]["id"] == rid
    assert not shift_db.set_recovery_contacted(rid, False, "", "Maya")


def test_recovery_contacted_now_at_log_time(isolated_db):
    rid = shift_db.add_recovery("Sam", "", "", "food-quality", "remade-now",
                                "", False, "Riya", contacted_now=True)
    open_recs, awaiting, _ = shift_db.recovery_feed()
    assert not open_recs and awaiting[0]["id"] == rid
    assert awaiting[0]["contacted_by"] == "Riya"
    # resolved_now wins over contacted_now
    rid2 = shift_db.add_recovery("Lee", "", "", "service", "refund", "",
                                 False, "Riya", resolved_now=True,
                                 contacted_now=True)
    rec = shift_db.get_recovery(rid2)
    assert rec["resolved_at"] and rec["contacted_at"] is None


def test_migration_adds_contact_columns(tmp_path, monkeypatch):
    # An existing production DB from before the contacted state gains the
    # new columns on boot, keeping its rows.
    import sqlite3
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "old.db"))
    conn = sqlite3.connect(shift_db.DB_PATH)
    conn.executescript("""
        CREATE TABLE guest_recoveries (
            id INTEGER PRIMARY KEY, recovery_date TEXT NOT NULL,
            guest_name TEXT NOT NULL, guest_phone TEXT, guest_email TEXT,
            issue TEXT NOT NULL DEFAULT 'other',
            remedy TEXT NOT NULL DEFAULT 'other', details TEXT,
            follow_up INTEGER NOT NULL DEFAULT 0, logged_by TEXT,
            created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
            resolution_note TEXT);
        INSERT INTO guest_recoveries (recovery_date, guest_name, created_at)
            VALUES ('2026-10-01', 'Pre-upgrade guest', '2026-10-01 09:00');
    """)
    conn.commit()
    conn.close()
    shift_db.init_db()
    shift_db.init_db()   # idempotent — second boot must not double-ALTER
    open_recs, awaiting, _ = shift_db.recovery_feed()
    assert open_recs[0]["guest_name"] == "Pre-upgrade guest"
    assert open_recs[0]["contacted_at"] is None and not awaiting


def test_recovery_phone_plus_prefix(isolated_db):
    rid = shift_db.add_recovery("Intl Guest", "+1 519-555-0142", "",
                                "service", "refund", "", False, "Riya")
    rec = shift_db.recovery_feed()[0][0]
    assert rec["id"] == rid and rec["phone_digits"] == "+15195550142"


def test_recovery_stale_flips_at_exactly_two_days(isolated_db):
    from datetime import date, timedelta
    rid = shift_db.add_recovery("G", "", "", "service", "refund", "",
                                False, "Riya")
    today = date.fromisoformat(shift_db.today_local())
    for days_ago, expect_stale in [(1, False), (2, True)]:
        with shift_db.closing(shift_db.connect()) as conn, conn:
            conn.execute("UPDATE guest_recoveries SET recovery_date=? WHERE id=?",
                         ((today - timedelta(days=days_ago)).isoformat(), rid))
        rec = shift_db.recovery_feed()[0][0]
        assert rec["stale"] is expect_stale, f"{days_ago}d open"
        assert rec["age_days"] == days_ago


def test_recovery_corrupt_date_is_graceful(isolated_db):
    rid = shift_db.add_recovery("G", "", "", "service", "refund", "",
                                False, "Riya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE guest_recoveries SET recovery_date=? WHERE id=?",
                     ("01/02/2020", rid))
    rec = shift_db.recovery_feed()[0][0]   # no ValueError
    assert rec["age_days"] == 0 and not rec["stale"]


def test_recovery_contact_purge_after_90_days(isolated_db):
    old = shift_db.add_recovery("Old", "519-555-0001", "old@x.ca", "service",
                                "refund", "", False, "Riya", resolved_now=True)
    fresh = shift_db.add_recovery("Fresh", "519-555-0002", "", "service",
                                  "refund", "", False, "Riya", resolved_now=True)
    still_open = shift_db.add_recovery("Open", "519-555-0003", "", "service",
                                       "refund", "", False, "Riya")
    # A guest who was contacted but never came back must also age out —
    # the awaiting state can't park PII forever.
    no_show = shift_db.add_recovery("NoShow", "519-555-0004", "", "service",
                                    "refund", "", False, "Riya")
    shift_db.set_recovery_contacted(no_show, True, "said next week", "Riya")
    waiting_fresh = shift_db.add_recovery("Waiting", "519-555-0005", "",
                                          "service", "refund", "", False, "Riya")
    shift_db.set_recovery_contacted(waiting_fresh, True, "Sat", "Riya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE guest_recoveries SET resolved_at=? WHERE id=?",
                     ("2020-01-01 09:00", old))
        conn.execute("UPDATE guest_recoveries SET contacted_at=? WHERE id=?",
                     ("2020-01-01 09:00", no_show))
    assert shift_db.purge_old_recovery_contacts() == 2
    purged = shift_db.get_recovery(old)
    assert purged["guest_phone"] is None and purged["guest_email"] is None
    assert purged["guest_name"] == "Old"              # history row survives
    gone = shift_db.get_recovery(no_show)
    assert gone["guest_phone"] is None and gone["contact_note"] is None
    assert shift_db.get_recovery(fresh)["guest_phone"] == "519-555-0002"
    assert shift_db.get_recovery(still_open)["guest_phone"] == "519-555-0003"
    assert shift_db.get_recovery(waiting_fresh)["contact_note"] == "Sat"
    assert shift_db.purge_old_recovery_contacts() == 0  # idempotent


def test_active_admin_count(isolated_db):
    assert shift_db.active_admin_count() == 0
    shift_db.add_leader("Dana", "5555", role="admin")
    shift_db.add_leader("Maya", "4721", role="lead")
    assert shift_db.active_admin_count() == 1
    dana = next(l for l in shift_db.leaders() if l["name"] == "Dana")
    shift_db.set_leader_active(dana["id"], False)
    assert shift_db.active_admin_count() == 0


def test_recovery_resolved_now_skips_open_queue(isolated_db):
    rid = shift_db.add_recovery("Sam", "", "sam@x.ca", "food-quality",
                                "remade-now", "", False, "Riya",
                                resolved_now=True)
    open_recs, awaiting, resolved = shift_db.recovery_feed()
    assert not open_recs
    assert resolved[0]["id"] == rid and resolved[0]["resolved_by"] == "Riya"
    assert shift_db.open_recovery_count() == 0


def test_recovery_issue_counts_windowed(isolated_db):
    for _ in range(2):
        shift_db.add_recovery("G", "", "", "order-error", "refund", "",
                              False, "Riya")
    shift_db.add_recovery("G", "", "", "wait-time", "refund", "", False, "Riya")
    old = shift_db.add_recovery("G", "", "", "catering", "refund", "",
                                False, "Riya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE guest_recoveries SET recovery_date=? WHERE id=?",
                     ("2020-01-01", old))
    counts = shift_db.recovery_issue_counts(days=28)
    assert counts[0] == {"issue": "order-error", "count": 2}
    assert {"issue": "wait-time", "count": 1} in counts
    assert all(c["issue"] != "catering" for c in counts)

    # "Last 28 days" means exactly 28 dates including today: a row 27 days
    # back is in, 28 days back is out.
    from datetime import date, timedelta
    today = date.fromisoformat(shift_db.today_local())
    for days_back, issue in [(27, "cleanliness"), (28, "spill-accident")]:
        rid = shift_db.add_recovery("B", "", "", issue, "refund", "",
                                    False, "Riya")
        with shift_db.closing(shift_db.connect()) as conn, conn:
            conn.execute("UPDATE guest_recoveries SET recovery_date=? WHERE id=?",
                         ((today - timedelta(days=days_back)).isoformat(), rid))
    counts = shift_db.recovery_issue_counts(days=28)
    assert {"issue": "cleanliness", "count": 1} in counts
    assert all(c["issue"] != "spill-accident" for c in counts)


def test_recoveries_survive_export_import(isolated_db):
    rid = shift_db.add_recovery("Jordan", "519-555-0142", "", "order-error",
                                "free-entree-card", "order #88", True, "Riya")
    raw = shift_db.export_json()
    shift_db.delete_recovery(rid)
    assert shift_db.import_json(raw) is None
    open_recs, _, _ = shift_db.recovery_feed()
    assert open_recs[0]["guest_name"] == "Jordan"
    assert open_recs[0]["follow_up"] == 1

    # A pre-recovery backup (no guest_recoveries section) restores cleanly
    # to an empty table.
    import json as _json
    payload = _json.loads(raw)
    del payload["guest_recoveries"]
    assert shift_db.import_json(_json.dumps(payload)) is None
    assert shift_db.open_recovery_count() == 0


def test_recent_dates_shape(isolated_db):
    dates = shift_db.recent_dates(3)
    assert len(dates) == 3
    assert dates[0] == shift_db.today_local()
    assert dates[0] > dates[1] > dates[2]


def test_current_daypart_is_valid(isolated_db):
    assert shift_db.current_daypart() in shift_db.DAYPARTS
