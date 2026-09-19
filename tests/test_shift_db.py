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
    # Leaders are intentionally NOT restored (no hashes in export)
    assert shift_db.verify_leader("Maya", "1234")


def test_import_rejects_garbage(isolated_db):
    assert shift_db.import_json("not json") is not None
    assert shift_db.import_json("{}") is not None
    assert shift_db.import_json('{"format": 1, "shift_notes": "nope"}') is not None
    bad_row = '{"format": 1, "shift_notes": [{"evil_column": 1}]}'
    assert shift_db.import_json(bad_row) is not None


def test_recent_dates_shape(isolated_db):
    dates = shift_db.recent_dates(3)
    assert len(dates) == 3
    assert dates[0] == shift_db.today_local()
    assert dates[0] > dates[1] > dates[2]


def test_current_daypart_is_valid(isolated_db):
    assert shift_db.current_daypart() in shift_db.DAYPARTS
