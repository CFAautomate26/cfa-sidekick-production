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
                 "/shift/notes", "/shift/roster", "/shift/history", "/shift/more"]:
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
