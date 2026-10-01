"""Route/auth tests for the shift leading app (shift_app.py via app.py)."""

import io
import json

import pytest

import app as cfa_app
import shift_app
import shift_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test.db"))
    shift_db.init_db()
    shift_app._pin_fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    cfa_app.app.config["TESTING"] = True
    with cfa_app.app.test_client() as c:
        yield c


def login_operator(client):
    return client.post("/shift/login", data={"name": "Operator", "pin": "9999"})


def make_leader(client, name="Maya", pin="4721", role="lead"):
    login_operator(client)
    client.post("/shift/admin/leaders/add", data={"name": name, "pin": pin, "role": role})
    client.post("/shift/logout")


def login_leader(client, name="Maya", pin="4721"):
    return client.post("/shift/login", data={"name": name, "pin": pin})


# --- boot / isolation ------------------------------------------------------

def test_bots_unaffected(client):
    assert client.get("/").status_code == 200
    resp = client.post("/groupme_callback", json={"text": "hi", "sender_type": "bot"})
    assert resp.status_code == 200


def test_requires_login(client):
    for path in ["/shift/", "/shift/checklists", "/shift/lineup", "/shift/goals",
                 "/shift/notes", "/shift/roster", "/shift/history", "/shift/more",
                 "/shift/recovery"]:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/shift/login" in resp.headers["Location"]


def test_not_configured_page(client, monkeypatch):
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "")
    resp = client.get("/shift/")
    assert resp.status_code == 200
    assert b"SHIFT_ADMIN_PIN" in resp.data


def test_manifest_and_icon_public(client):
    assert client.get("/shift/manifest.webmanifest").status_code == 200
    assert client.get("/shift/icon.svg").status_code == 200


# --- auth ------------------------------------------------------------------

def test_operator_login_logout(client):
    resp = login_operator(client)
    assert resp.status_code == 302
    resp = client.get("/shift/")
    assert resp.status_code == 200
    assert b"Operator" in resp.data
    client.post("/shift/logout")
    assert client.get("/shift/").status_code == 302


def test_wrong_pin_rejected(client):
    resp = client.post("/shift/login", data={"name": "Operator", "pin": "0000"})
    assert resp.status_code == 401


def test_lockout_after_five_fails(client):
    for _ in range(5):
        client.post("/shift/login", data={"name": "Operator", "pin": "0000"})
    resp = client.post("/shift/login", data={"name": "Operator", "pin": "9999"})
    assert resp.status_code == 401
    assert b"Try again in 10 minutes" in resp.data


def test_leader_login_and_deactivation(client):
    make_leader(client)
    assert login_leader(client).status_code == 302
    assert b"Maya" in client.get("/shift/").data

    # Deactivation kills the session on the next request
    leader_id = shift_db.leaders()[0]["id"]
    shift_db.set_leader_active(leader_id, False)
    resp = client.get("/shift/")
    assert resp.status_code == 302


def test_demoted_admin_loses_admin_on_next_request(client):
    make_leader(client, name="Dana", pin="5555", role="admin")
    login_leader(client, name="Dana", pin="5555")
    assert client.get("/shift/admin").status_code == 200

    # Re-adding with role=lead demotes; the live session must follow suit.
    leader_id = shift_db.leaders()[0]["id"]
    shift_db.add_leader("Dana", "5555", role="lead")
    resp = client.get("/shift/admin")
    assert resp.status_code == 302
    assert shift_db.get_leader(leader_id)["role"] == "lead"


def test_promote_leader_to_admin(client):
    make_leader(client)  # Maya, role lead
    leader_id = shift_db.leaders()[0]["id"]

    # The Operator promotes her through the admin route
    login_operator(client)
    client.post(f"/shift/admin/leaders/{leader_id}/role", data={"role": "admin"})
    assert shift_db.get_leader(leader_id)["role"] == "admin"
    client.post("/shift/logout")

    # Her very next request carries admin (no re-login needed): sign in,
    # demote via db, confirm loss; then promote via db, confirm gain.
    login_leader(client)
    assert client.get("/shift/admin").status_code == 200
    shift_db.set_leader_role(leader_id, "lead")
    assert client.get("/shift/admin").status_code == 302
    shift_db.set_leader_role(leader_id, "admin")
    assert client.get("/shift/admin").status_code == 200

    # Bogus roles are rejected
    assert not shift_db.set_leader_role(leader_id, "owner")
    assert shift_db.get_leader(leader_id)["role"] == "admin"


def test_lead_cannot_change_roles(client):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    login_leader(client, name="Devon", pin="8888")
    resp = client.post(f"/shift/admin/leaders/{maya['id']}/role",
                       data={"role": "admin"})
    assert resp.status_code == 302
    assert shift_db.get_leader(maya["id"])["role"] == "lead"


def test_ack_deleted_announcement_route_is_graceful(client):
    make_leader(client)
    login_leader(client)
    resp = client.post("/shift/announcements/999/ack")
    assert resp.status_code == 302  # flash + redirect, not a 500


def test_assign_stale_position_route_is_graceful(client):
    login_operator(client)
    resp = client.post("/shift/lineup/assign", data={
        "date": shift_db.today_local(), "daypart": "lunch",
        "position_id": "999999", "member_name": "Maya",
    })
    assert resp.status_code == 302  # flash + redirect, not a 500
    assert not shift_db.lineup_for(shift_db.today_local(), "lunch")


def test_invalid_optional_dates_are_blanked(client):
    login_operator(client)
    client.post("/shift/goals", data={"title": "G", "due_date": "garbage"})
    assert shift_db.goals_by_status("active")[0]["due_date"] is None
    client.post("/shift/announcements", data={"body": "A", "expires_on": "nonsense"})
    assert shift_db.active_announcements()[0]["expires_on"] is None


def test_login_keeps_next_after_wrong_pin(client):
    resp = client.post("/shift/login?next=/shift/goals",
                       data={"name": "Operator", "pin": "0000"})
    assert b'name="next" value="/shift/goals"' in resp.data
    resp = client.post("/shift/login",
                       data={"name": "Operator", "pin": "9999",
                             "next": "/shift/goals"})
    assert resp.headers["Location"] == "/shift/goals"


def test_import_route_survives_fk_inconsistent_backup(client):
    login_operator(client)
    bad = (b'{"format": 1, "checklist_run_items": [{"id": 1, "run_id": 999, '
           b'"label": "x", "critical": 0, "sort": 0, "done": 0}]}')
    resp = client.post("/shift/admin/import", data={
        "confirm": "1", "backup": (io.BytesIO(bad), "backup.json"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302  # flashed error, not a 500
    assert shift_db.all_templates()  # nothing was wiped


def test_admin_pages_blocked_for_leads(client):
    make_leader(client)
    login_leader(client)
    resp = client.get("/shift/admin", follow_redirects=False)
    assert resp.status_code == 302
    resp = client.post("/shift/announcements", data={"body": "nope"})
    assert resp.status_code == 302
    assert not shift_db.active_announcements()


def test_login_redirect_next_is_sanitized(client):
    resp = client.post("/shift/login?next=https://evil.example",
                       data={"name": "Operator", "pin": "9999"})
    assert resp.headers["Location"].startswith("/shift")
    client.post("/shift/logout")
    resp = client.post("/shift/login?next=//evil.example",
                       data={"name": "Operator", "pin": "9999"})
    assert resp.headers["Location"].startswith("/shift")


# --- checklists ------------------------------------------------------------

def test_today_instantiates_and_lists_runs(client):
    login_operator(client)
    resp = client.get("/shift/")
    assert resp.status_code == 200
    runs = shift_db.runs_for_date(shift_db.today_local())
    assert len(runs) == len(shift_db.SEED_TEMPLATES)


def test_toggle_item_form_flow(client):
    login_operator(client)
    client.get("/shift/")
    run = shift_db.runs_for_date(shift_db.today_local())[0]
    _, items = shift_db.run_with_items(run["id"])
    resp = client.post(f"/shift/item/{items[0]['id']}/toggle", data={"done": "1"})
    assert resp.status_code == 302
    _, items = shift_db.run_with_items(run["id"])
    assert items[0]["done"] == 1 and items[0]["done_by"] == "Operator"


def test_toggle_item_ajax_flow(client):
    login_operator(client)
    client.get("/shift/")
    run = shift_db.runs_for_date(shift_db.today_local())[0]
    _, items = shift_db.run_with_items(run["id"])
    resp = client.post(f"/shift/item/{items[0]['id']}/toggle",
                       data={"done": "1", "ajax": "1"})
    assert resp.status_code == 200
    out = resp.get_json()
    assert out["ok"] and out["done"] and out["done_by"] == "Operator"
    assert out["done_count"] == 1

    resp = client.post("/shift/item/999999/toggle", data={"done": "1", "ajax": "1"})
    assert resp.status_code == 404


def test_run_detail_renders(client):
    login_operator(client)
    client.get("/shift/")
    run = shift_db.runs_for_date(shift_db.today_local())[0]
    resp = client.get(f"/shift/checklists/run/{run['id']}")
    assert resp.status_code == 200
    assert b"critical" in resp.data.lower()


# --- lineup ----------------------------------------------------------------

def test_lineup_assign_unassign(client):
    login_operator(client)
    pos = shift_db.active_positions()[0]
    today = shift_db.today_local()
    client.post("/shift/lineup/assign", data={
        "date": today, "daypart": "lunch", "position_id": pos["id"],
        "member_name": "Marcus",
    })
    lineup = shift_db.lineup_for(today, "lunch")
    assert lineup[pos["id"]][0]["member_name"] == "Marcus"
    # Typed names join the roster for autosuggest
    assert any(m["name"] == "Marcus" for m in shift_db.roster())

    aid = lineup[pos["id"]][0]["id"]
    client.post(f"/shift/lineup/unassign/{aid}", data={"date": today, "daypart": "lunch"})
    assert not shift_db.lineup_for(today, "lunch")


def test_lineup_copy_route(client):
    login_operator(client)
    pos = shift_db.active_positions()[0]
    today = shift_db.today_local()
    shift_db.assign_position("2020-01-01", "lunch", pos["id"], "Sarah")
    client.post("/shift/lineup/copy", data={
        "from_date": "2020-01-01", "from_daypart": "lunch",
        "to_date": today, "to_daypart": "lunch",
    })
    assert shift_db.lineup_for(today, "lunch")


def test_lineup_page_renders(client):
    login_operator(client)
    assert client.get("/shift/lineup").status_code == 200
    assert client.get("/shift/lineup?daypart=nonsense&date=garbage").status_code == 200


# --- goals -----------------------------------------------------------------

def test_goal_create_update_status(client):
    login_operator(client)
    resp = client.post("/shift/goals", data={
        "title": "DT under 4 min", "metric": "Avg DT", "unit": "sec",
        "target_value": "240", "direction": "down", "period": "weekly",
    })
    assert resp.status_code == 302
    goal = shift_db.goals_by_status("active")[0]

    client.post(f"/shift/goals/{goal['id']}/update", data={"value": "300", "note": "lunch"})
    g, updates = shift_db.goal_with_updates(goal["id"])
    assert g["latest_value"] == 300 and len(updates) == 1

    client.post(f"/shift/goals/{goal['id']}/status", data={"status": "achieved"})
    assert shift_db.goals_by_status("achieved")

    assert client.get(f"/shift/goals/{goal['id']}").status_code == 200
    assert client.get("/shift/goals?status=achieved").status_code == 200


def test_goal_update_requires_content(client):
    login_operator(client)
    client.post("/shift/goals", data={"title": "Tidy walk-in", "period": "one-time"})
    goal = shift_db.goals_by_status("active")[0]
    client.post(f"/shift/goals/{goal['id']}/update", data={"value": "", "note": ""})
    _, updates = shift_db.goal_with_updates(goal["id"])
    assert not updates


# --- notes -----------------------------------------------------------------

def test_note_create_and_delete_permissions(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/notes", data={"body": "Fryer 2 flaky", "category": "equipment",
                                      "daypart": "dinner"})
    note = shift_db.notes_feed()[0]
    assert note["author"] == "Maya" and note["category"] == "equipment"
    client.post("/shift/logout")

    # A different lead can't delete Maya's note
    make_leader(client, name="Devon", pin="8888")
    login_leader(client, name="Devon", pin="8888")
    client.post(f"/shift/notes/{note['id']}/delete")
    assert shift_db.notes_feed()
    client.post("/shift/logout")

    # Admin can
    login_operator(client)
    client.post(f"/shift/notes/{note['id']}/delete")
    assert not shift_db.notes_feed()


# --- announcements ---------------------------------------------------------

def test_announcement_flow(client):
    login_operator(client)
    client.post("/shift/announcements", data={"body": "New SOP", "pinned": "1"})
    client.post("/shift/logout")

    make_leader(client)
    login_leader(client)
    resp = client.get("/shift/")
    assert b"New SOP" in resp.data  # unacked shows on Today

    a = shift_db.active_announcements()[0]
    client.post(f"/shift/announcements/{a['id']}/ack")
    resp = client.get("/shift/")
    assert b"New SOP" not in resp.data  # acked disappears from Today
    assert shift_db.announcement_readers(a["id"])[0]["reader"] == "Maya"
    assert client.get("/shift/announcements").status_code == 200


# --- roster ----------------------------------------------------------------

def test_roster_routes(client):
    login_operator(client)
    client.post("/shift/roster/add", data={"name": "Sarah"})
    member = shift_db.roster()[0]
    assert member["name"] == "Sarah"
    client.post(f"/shift/roster/{member['id']}/toggle", data={"active": "0"})
    assert not shift_db.roster()
    assert client.get("/shift/roster").status_code == 200


# --- history ---------------------------------------------------------------

def test_history_renders(client):
    login_operator(client)
    client.get("/shift/")  # instantiate today
    today = shift_db.today_local()
    run = shift_db.runs_for_date(today)[0]
    _, items = shift_db.run_with_items(run["id"])
    shift_db.set_item_done(items[0]["id"], True, "Operator")
    resp = client.get(f"/shift/history?date={today}")
    assert resp.status_code == 200
    assert b"Operator" in resp.data


# --- admin: leaders + templates -------------------------------------------

def test_admin_leader_management(client):
    login_operator(client)
    client.post("/shift/admin/leaders/add", data={"name": "Maya", "pin": "4721"})
    assert shift_db.verify_leader("Maya", "4721")

    leader_id = shift_db.leaders()[0]["id"]
    client.post(f"/shift/admin/leaders/{leader_id}/reset-pin", data={"pin": "1357"})
    assert shift_db.verify_leader("Maya", "1357")

    client.post(f"/shift/admin/leaders/{leader_id}/toggle", data={"active": "0"})
    assert not shift_db.verify_leader("Maya", "1357")
    assert client.get("/shift/admin").status_code == 200


def test_admin_template_editor(client):
    login_operator(client)
    resp = client.post("/shift/admin/templates/new", data={
        "name": "Cow Day Setup", "daypart": "afternoon", "area": "All",
        "items": "Hang banner\n! Verify fridge temps\n\n  Stock plush  ",
    })
    assert resp.status_code == 302
    t = next(t for t in shift_db.all_templates() if t["name"] == "Cow Day Setup")
    _, items = shift_db.template_with_items(t["id"])
    assert [(i["label"], i["critical"]) for i in items] == [
        ("Hang banner", 0), ("Verify fridge temps", 1), ("Stock plush", 0),
    ]

    # Edit round-trips the "!" syntax
    resp = client.get(f"/shift/admin/templates/{t['id']}")
    assert b"! Verify fridge temps" in resp.data
    client.post(f"/shift/admin/templates/{t['id']}", data={
        "name": "Cow Day Setup", "daypart": "afternoon", "area": "All",
        "items": "Just one thing",
    })
    _, items = shift_db.template_with_items(t["id"])
    assert len(items) == 1

    client.post(f"/shift/admin/templates/{t['id']}/toggle", data={"active": "0"})
    assert not next(x for x in shift_db.all_templates(include_inactive=True)
                    if x["id"] == t["id"])["active"]


# --- leadership development ------------------------------------------------

def test_development_admin_sees_all_leaders(client):
    make_leader(client)
    login_operator(client)
    resp = client.get("/shift/development")
    assert resp.status_code == 200
    assert b"Maya" in resp.data

    leader = shift_db.leaders()[0]
    resp = client.get(f"/shift/development/{leader['id']}")
    assert resp.status_code == 200
    assert b"Mindset 101" in resp.data


def test_development_lead_sees_only_self(client):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    devon = next(l for l in shift_db.leaders() if l["name"] == "Devon")

    login_leader(client)  # Maya
    resp = client.get("/shift/development", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/development/{maya['id']}")
    assert client.get(f"/shift/development/{maya['id']}").status_code == 200

    resp = client.get(f"/shift/development/{devon['id']}", follow_redirects=False)
    assert resp.status_code == 302  # bounced, not shown

    lesson = shift_db.course_overview(devon["id"])[0]["lessons"][0]
    client.post(f"/shift/development/{devon['id']}/lesson/{lesson['id']}",
                data={"action": "complete", "note": "sneaky"})
    assert shift_db.leader_course_summary()[0]["done"] == 0
    assert shift_db.leader_course_summary()[1]["done"] == 0


def test_development_mark_complete_flow(client):
    make_leader(client)
    login_operator(client)
    leader = shift_db.leaders()[0]
    lesson = shift_db.course_overview(leader["id"])[0]["lessons"][0]

    client.post(f"/shift/development/{leader['id']}/lesson/{lesson['id']}",
                data={"action": "complete", "note": "Solid grasp of the pyramid"})
    row = shift_db.course_overview(leader["id"])[0]["lessons"][0]
    assert row["done"] and row["recorded_by"] == "Operator"

    resp = client.get(f"/shift/development/{leader['id']}")
    assert b"Solid grasp of the pyramid" in resp.data

    client.post(f"/shift/development/{leader['id']}/lesson/{lesson['id']}",
                data={"action": "uncomplete"})
    assert not shift_db.course_overview(leader["id"])[0]["lessons"][0]["done"]

    # Bogus lesson id → flash + redirect, never a 500
    resp = client.post(f"/shift/development/{leader['id']}/lesson/999999",
                       data={"action": "complete"})
    assert resp.status_code == 302


# --- leader to-dos ---------------------------------------------------------

def test_todo_assign_is_admin_only(client):
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_leader(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Self-assigned"})
    assert not shift_db.tasks_for_leader(leader["id"])[0]  # nothing created


def test_todo_full_flow(client):
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)

    client.post(f"/shift/todo/{leader['id']}/assign", data={
        "title": "Deep clean fryer 2", "details": "Before Friday",
        "due_date": "2030-01-01",
    })
    open_tasks, _ = shift_db.tasks_for_leader(leader["id"])
    assert open_tasks[0]["assigned_by"] == "Operator"

    resp = client.get("/shift/todo")
    assert resp.status_code == 200 and b"Maya" in resp.data
    resp = client.get(f"/shift/todo/{leader['id']}")
    assert b"Deep clean fryer 2" in resp.data
    client.post("/shift/logout")

    # Maya sees it on Today, completes it from her page
    login_leader(client)
    assert b"Deep clean fryer 2" in client.get("/shift/").data
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    _, done_tasks = shift_db.tasks_for_leader(leader["id"])
    assert done_tasks[0]["completed_by"] == "Maya"
    assert b"Deep clean fryer 2" not in client.get("/shift/").data


def test_todo_other_leader_blocked(client):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    devon = next(l for l in shift_db.leaders() if l["name"] == "Devon")
    login_operator(client)
    client.post(f"/shift/todo/{devon['id']}/assign", data={"title": "Devon's task"})
    client.post("/shift/logout")

    login_leader(client)  # Maya
    resp = client.get("/shift/todo", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/todo/{maya['id']}")
    assert client.get(f"/shift/todo/{devon['id']}", follow_redirects=False).status_code == 302

    task = shift_db.tasks_for_leader(devon["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert not shift_db.tasks_for_leader(devon["id"])[1]  # still open

    client.post(f"/shift/todo/task/{task['id']}/delete")
    assert shift_db.get_task(task["id"])  # lead can't delete

    # Bogus ids are graceful
    assert client.post("/shift/todo/task/999999/toggle",
                       data={"done": "1"}).status_code == 302


# --- guest recovery ---------------------------------------------------------

def test_recovery_create_resolve_flow(client):
    make_leader(client)
    login_leader(client)

    resp = client.post("/shift/recovery", data={
        "guest_name": "Jordan Lee", "guest_phone": "519-555-0142",
        "issue": "not-a-real-issue", "remedy": "free-entree-card",
        "details": "Order #88 missing entrée", "follow_up": "1",
    })
    assert resp.status_code == 302
    assert "#rec-" in resp.headers["Location"]

    page = client.get("/shift/recovery").data
    assert b"Jordan Lee" in page and b"519-555-0142" in page
    rec = shift_db.recovery_feed()[0][0]
    assert rec["issue"] == "other"            # coerced, not stored raw
    assert rec["logged_by"] == "Maya"

    # Shows on the Today dashboard until resolved
    assert b"Jordan Lee" in client.get("/shift/").data

    resp = client.post(f"/shift/recovery/{rec['id']}/resolve",
                       data={"resolved": "1", "note": "called, cards mailed"})
    assert resp.headers["Location"].endswith(f"#rec-{rec['id']}")
    done = shift_db.get_recovery(rec["id"])
    assert done["resolved_by"] == "Maya" and done["resolution_note"]
    assert b"Jordan Lee" not in client.get("/shift/").data

    # Reopen puts it back in the queue
    client.post(f"/shift/recovery/{rec['id']}/resolve", data={"resolved": "0"})
    assert shift_db.open_recovery_count() == 1


def test_recovery_resolved_now_route(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/recovery", data={
        "guest_name": "Sam", "issue": "food-quality", "remedy": "remade-now",
        "resolved_now": "1",
    })
    assert shift_db.open_recovery_count() == 0
    assert shift_db.recovery_feed()[1][0]["guest_name"] == "Sam"


def test_recovery_empty_name_flashes(client):
    login_operator(client)
    resp = client.post("/shift/recovery", data={"guest_name": "   "},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"needs the guest" in resp.data
    assert shift_db.open_recovery_count() == 0


def test_recovery_delete_is_admin_only(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/recovery", data={"guest_name": "Jordan",
                                         "issue": "service", "remedy": "refund"})
    rec = shift_db.recovery_feed()[0][0]
    resp = client.post(f"/shift/recovery/{rec['id']}/delete")
    assert resp.status_code == 302
    assert shift_db.get_recovery(rec["id"])   # lead can't delete
    client.post("/shift/logout")

    login_operator(client)
    client.post(f"/shift/recovery/{rec['id']}/delete")
    assert shift_db.get_recovery(rec["id"]) is None


def test_recovery_stale_id_graceful(client):
    login_operator(client)
    assert client.post("/shift/recovery/999999/resolve",
                       data={"resolved": "1"}).status_code == 302
    assert client.post("/shift/recovery/999999/delete").status_code == 302


def test_recovery_admin_sees_issue_radar(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/recovery", data={"guest_name": "G1",
                                         "issue": "order-error", "remedy": "refund"})
    assert b"Last 28 days" not in client.get("/shift/recovery").data
    client.post("/shift/logout")
    login_operator(client)
    assert b"Last 28 days" in client.get("/shift/recovery").data


# --- to-do completion notifications (Slack + opt-in email) ------------------

@pytest.fixture()
def notifications(monkeypatch):
    """Arm both notification channels and capture what each would send."""
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_CHANNEL", "C123TEST")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_EMAIL", "op@example.com")
    sent = {"slack": [], "email": []}

    def capture(kind):
        return lambda task, leader_name, remaining: sent[kind].append(
            {"task": task, "leader": leader_name, "remaining": remaining})

    monkeypatch.setattr("shift_app.send_completion_slack", capture("slack"))
    monkeypatch.setattr("shift_app.send_completion_email", capture("email"))
    return sent


def test_leader_completion_notifies_operator(client, notifications):
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Task A"})
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Task B"})
    client.post("/shift/logout")

    login_leader(client)
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})

    for sent in (notifications["slack"], notifications["email"]):
        assert len(sent) == 1
        assert sent[0]["leader"] == "Maya"
        assert sent[0]["task"]["title"] == "Task A"
        assert sent[0]["task"]["completed_by"] == "Maya"
        assert sent[0]["remaining"] == 1

    # Reopening sends nothing
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "0"})
    assert len(notifications["slack"]) == 1
    assert len(notifications["email"]) == 1


def test_slack_only_when_email_unset(client, notifications, monkeypatch):
    # The production shape: Slack channel configured, email left disabled.
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_EMAIL", "")
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Slack me"})
    client.post("/shift/logout")
    login_leader(client)
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert len(notifications["slack"]) == 1
    assert not notifications["email"]


def test_operator_completion_sends_nothing(client, notifications):
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Reviewed live"})
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert not notifications["slack"]
    assert not notifications["email"]


def test_notify_failure_never_breaks_the_tap(client, notifications, monkeypatch):
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Fragile"})
    client.post("/shift/logout")
    task = shift_db.tasks_for_leader(leader["id"])[0][0]

    def boom(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(shift_db, "tasks_for_leader", boom)

    login_leader(client)
    resp = client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert resp.status_code == 302             # the tap still succeeds
    assert shift_db.get_task(task["id"])["completed_at"]
    assert not notifications["slack"]          # both quietly skipped
    assert not notifications["email"]


def test_notify_disabled_by_empty_env(client, notifications, monkeypatch):
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_CHANNEL", "")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_EMAIL", "")
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Quiet task"})
    client.post("/shift/logout")
    login_leader(client)
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert not notifications["slack"]
    assert not notifications["email"]


def test_slack_ping_payload(monkeypatch):
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_CHANNEL", "C123TEST")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_MENTION", "U05R80802EB")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://app.example.com")

    posted = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)

        class R:
            status_code = 200
            text = '{"ok": true}'
        return R()

    monkeypatch.setattr(shift_app.requests, "post", fake_post)
    shift_app.send_completion_slack(
        {"title": "Clean ice machine", "due_date": "2026-10-01", "leader_id": 7},
        "Riya", 2)

    assert posted["url"] == "https://slack.com/api/chat.postMessage"
    assert posted["headers"]["Authorization"] == "Bearer xoxb-test"
    body = posted["json"]
    assert body["channel"] == "C123TEST"
    assert body["text"].startswith("<@U05R80802EB> ")   # the ping that buzzes
    assert "*Riya*" in body["text"]
    assert "*Clean ice machine*" in body["text"]
    assert "Due: 2026-10-01" in body["text"]
    assert "2 still open for Riya" in body["text"]
    assert "https://app.example.com/shift/todo/7" in body["text"]


def test_slack_ping_without_token_or_extras(monkeypatch, capsys):
    # No mention, no external URL, no due date — message still well-formed;
    # and with no token at all, nothing is posted.
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_CHANNEL", "C123TEST")
    monkeypatch.setattr(shift_app, "SHIFT_NOTIFY_SLACK_MENTION", "")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)

    posted = {}

    def fake_post(url, **kwargs):
        posted.update(kwargs)

        class R:
            status_code = 200
            text = '{"ok": true}'
        return R()

    monkeypatch.setattr(shift_app.requests, "post", fake_post)
    shift_app.send_completion_slack({"title": "Solo", "leader_id": 3}, "Aron", 0)
    assert posted["json"]["text"].startswith("✅ *Aron*")
    assert "last open to-do" in posted["json"]["text"]

    # mrkdwn injection in a title is escaped, never a live mention
    posted.clear()
    shift_app.send_completion_slack(
        {"title": "Tell <!channel> & co", "leader_id": 3}, "Aron", 0)
    assert "<!channel>" not in posted["json"]["text"]
    assert "&lt;!channel&gt; &amp; co" in posted["json"]["text"]

    posted.clear()
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "")
    shift_app.send_completion_slack({"title": "Solo", "leader_id": 3}, "Aron", 0)
    assert not posted
    assert "SLACK_BOT_TOKEN is missing" in capsys.readouterr().out


# --- backup ----------------------------------------------------------------

def test_admin_export_import_routes(client):
    login_operator(client)
    client.post("/shift/notes", data={"body": "Keep me", "category": "general"})
    resp = client.get("/shift/admin/export")
    assert resp.status_code == 200
    backup = resp.data

    shift_db.delete_note(shift_db.notes_feed()[0]["id"])
    resp = client.post("/shift/admin/import", data={
        "confirm": "1", "backup": (io.BytesIO(backup), "backup.json"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert shift_db.notes_feed()[0]["body"] == "Keep me"


def test_admin_import_requires_confirmation(client):
    login_operator(client)
    resp = client.post("/shift/admin/import", data={
        "backup": (io.BytesIO(b"{}"), "backup.json"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302  # bounced with a flash, nothing imported


def test_scheduled_backup_endpoint(client):
    assert client.get("/scheduled/shift-backup").status_code == 401
    resp = client.get("/scheduled/shift-backup?token=test-schedule-secret")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    assert payload["format"] == 1
