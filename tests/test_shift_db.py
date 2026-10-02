"""Storage-layer tests for the shift leading app (shift_db.py).

Each test gets a fresh temp database via the isolated_db fixture, which
re-points shift_db.DB_PATH before init_db() runs.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shift_db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test_shift.db"))
    shift_db.init_db()
    yield


def test_init_seeds_templates_and_positions(isolated_db):
    templates = shift_db.all_templates()
    assert len(templates) == len(shift_db.SEED_TEMPLATES)
    assert all(t["item_count"] > 0 for t in templates)
    positions = shift_db.active_positions()
    assert len(positions) == len(shift_db.SEED_POSITIONS)


def test_init_is_idempotent(isolated_db):
    shift_db.init_db()
    shift_db.init_db()
    assert len(shift_db.all_templates()) == len(shift_db.SEED_TEMPLATES)


def test_runs_instantiate_once_per_date(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    shift_db.ensure_runs_for_date("2026-09-19")
    runs = shift_db.runs_for_date("2026-09-19")
    assert len(runs) == len(shift_db.SEED_TEMPLATES)
    # Snapshot items exist and none are done yet
    run, items = shift_db.run_with_items(runs[0]["id"])
    assert run["run_date"] == "2026-09-19"
    assert items and all(not i["done"] for i in items)


def test_runs_ordered_by_daypart(isolated_db):
    shift_db.ensure_runs_for_date("2026-09-19")
    runs = shift_db.runs_for_date("2026-09-19")
    order = {d: i for i, d in enumerate(shift_db.DAYPARTS)}
    dayparts = [order[r["daypart"]] for r in runs]
    assert dayparts == sorted(dayparts)


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
    assert len(shift_db.runs_for_date("2026-09-19")) == len(shift_db.SEED_TEMPLATES) - 1


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
    assert len(shift_db.all_templates()) == len(shift_db.SEED_TEMPLATES)  # not re-seeded


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
    # The surviving topic predates the last meeting → carried
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
                                "Maya", groupme=True)
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


# --- shout-outs --------------------------------------------------------------

def test_shoutout_lifecycle(isolated_db):
    assert shift_db.add_shoutout("  ", "speed", "msg", "Maya") is None
    assert shift_db.add_shoutout("Avery", "speed", "  ", "Maya") is None
    sid = shift_db.add_shoutout(" Avery  P ", "not-a-tag", " Great save ",
                                "Maya", groupme=True)
    rec = shift_db.get_shoutout(sid)
    assert rec["member_name"] == "Avery P" and rec["message"] == "Great save"
    assert rec["value_tag"] is None                     # bad tag coerced
    assert rec["groupme_at"]

    quiet = shift_db.add_shoutout("Sam", "teamwork", "Covered a break", "Neha")
    assert shift_db.get_shoutout(quiet)["groupme_at"] is None

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
