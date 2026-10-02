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
                 "/shift/recovery", "/shift/oneonone", "/shift/shoutouts",
                 "/shift/oneonone/member/1"]:
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

def test_todo_self_assign_allowed(client):
    # Assignment is open to every leader (Operator's call) — including
    # adding a task to your own list.
    make_leader(client)
    leader = shift_db.leaders()[0]
    login_leader(client)
    client.post(f"/shift/todo/{leader['id']}/assign", data={"title": "Self-assigned"})
    task = shift_db.tasks_for_leader(leader["id"])[0][0]
    assert task["title"] == "Self-assigned" and task["assigned_by"] == "Maya"


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


def test_any_leader_assigns_but_only_assignee_completes(client):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    devon = next(l for l in shift_db.leaders() if l["name"] == "Devon")

    login_leader(client)  # Maya, a plain lead
    # The index is open to every leader, with their own row marked
    page = client.get("/shift/todo")
    assert page.status_code == 200
    assert b"Maya (you)" in page.data and b"Devon" in page.data

    # A lead can open another leader's list and assign to them
    assert client.get(f"/shift/todo/{devon['id']}").status_code == 200
    client.post(f"/shift/todo/{devon['id']}/assign", data={"title": "From Maya"})
    task = shift_db.tasks_for_leader(devon["id"])[0][0]
    assert task["assigned_by"] == "Maya"

    # ...but completing stays with the assignee (or an admin)
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert not shift_db.tasks_for_leader(devon["id"])[1]  # still open

    # ...and delete stays admin-only
    client.post(f"/shift/todo/task/{task['id']}/delete")
    assert shift_db.get_task(task["id"])

    # The assignee can complete it
    client.post("/shift/logout")
    login_leader(client, name="Devon", pin="8888")
    client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert shift_db.tasks_for_leader(devon["id"])[1][0]["completed_by"] == "Devon"

    # Bogus ids are graceful
    assert client.post("/shift/todo/task/999999/toggle",
                       data={"done": "1"}).status_code == 302


# --- 1:1 agendas -------------------------------------------------------------

def test_oneonone_private_to_leader_and_operator(client):
    make_leader(client)                                   # Maya, lead
    make_leader(client, name="Devon", pin="8888")
    make_leader(client, name="Dana", pin="5555", role="admin")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    devon = next(l for l in shift_db.leaders() if l["name"] == "Devon")
    dana = next(l for l in shift_db.leaders() if l["name"] == "Dana")

    login_leader(client)                                  # Maya
    assert client.get(f"/shift/oneonone/{maya['id']}").status_code == 200
    assert client.get(f"/shift/oneonone/{devon['id']}").status_code == 302
    resp = client.get("/shift/oneonone", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{maya['id']}")
    client.post("/shift/logout")

    # An ADMIN leader still sees only their own agenda — the all-agendas
    # view belongs to the Operator master login alone.
    login_leader(client, name="Dana", pin="5555")
    assert client.get(f"/shift/oneonone/{dana['id']}").status_code == 200
    assert client.get(f"/shift/oneonone/{maya['id']}").status_code == 302
    resp = client.get("/shift/oneonone", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{dana['id']}")
    client.post("/shift/logout")

    login_operator(client)
    assert client.get("/shift/oneonone").status_code == 200
    assert client.get(f"/shift/oneonone/{maya['id']}").status_code == 200
    assert client.get(f"/shift/oneonone/{devon['id']}").status_code == 200


def test_oneonone_topic_routes(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    login_operator(client)
    client.post(f"/shift/oneonone/{maya['id']}/topics",
                data={"topic": "Catering van keys"})
    client.post("/shift/logout")

    login_leader(client)
    client.post(f"/shift/oneonone/{maya['id']}/topics",
                data={"topic": "Saturday staffing"})
    page = client.get(f"/shift/oneonone/{maya['id']}").data
    assert b"Catering van keys" in page and b"Saturday staffing" in page

    # Maya can't remove the Operator's topic, only her own
    topics = shift_db.oneonone_for_leader(maya["id"])[0]
    operator_topic = next(t for t in topics if t["added_by"] == "Operator")
    own_topic = next(t for t in topics if t["added_by"] == "Maya")
    client.post(f"/shift/oneonone/topic/{operator_topic['id']}/delete")
    assert shift_db.get_topic(operator_topic["id"])
    client.post(f"/shift/oneonone/topic/{own_topic['id']}/delete")
    assert shift_db.get_topic(own_topic["id"]) is None

    # Discuss with a note during the meeting; race flashes; reopen
    resp = client.post(f"/shift/oneonone/topic/{operator_topic['id']}/toggle",
                       data={"discussed": "1", "note": "fixed the lockbox"})
    assert resp.headers["Location"].endswith("#agenda")   # keeps the meeting flowing
    assert b"Past 1:1s" in client.get(f"/shift/oneonone/{maya['id']}").data
    resp = client.post(f"/shift/oneonone/topic/{operator_topic['id']}/toggle",
                       data={"discussed": "1"}, follow_redirects=True)
    assert b"Already marked discussed by Maya" in resp.data
    client.post(f"/shift/oneonone/topic/{operator_topic['id']}/toggle",
                data={"discussed": "0"})
    assert shift_db.get_topic(operator_topic["id"])["discussed_at"] is None

    # Another leader can't touch Maya's topics at all
    make_leader(client, name="Devon", pin="8888")
    client.post("/shift/logout")
    login_leader(client, name="Devon", pin="8888")
    resp = client.post(f"/shift/oneonone/topic/{operator_topic['id']}/toggle",
                       data={"discussed": "1"})
    assert resp.status_code == 302
    assert shift_db.get_topic(operator_topic["id"])["discussed_at"] is None


def test_oneonone_action_creates_todo(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    login_leader(client)
    client.post(f"/shift/oneonone/{maya['id']}/action",
                data={"title": "Shadow a breakfast open", "due_date": "2026-11-01"})
    task = shift_db.tasks_for_leader(maya["id"])[0][0]
    assert task["title"] == "Shadow a breakfast open"
    assert task["assigned_by"] == "Maya" and task["due_date"] == "2026-11-01"
    # ...and it shows on the 1:1 page's open to-dos card
    assert b"Shadow a breakfast open" in client.get(f"/shift/oneonone/{maya['id']}").data


def test_more_badge_shows_my_agenda_count(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    login_leader(client)
    assert b"on your agenda" not in client.get("/shift/more").data
    client.post(f"/shift/oneonone/{maya['id']}/topics", data={"topic": "A"})
    client.post(f"/shift/oneonone/{maya['id']}/topics", data={"topic": "B"})
    assert "● 2 on your agenda".encode() in client.get("/shift/more").data


def test_goal_leader_tag_via_route(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    login_operator(client)
    client.post("/shift/goals", data={"title": "Solo breakfast open",
                                      "period": "monthly",
                                      "leader_id": str(maya["id"])})
    client.post("/shift/goals", data={"title": "Store goal", "period": "weekly",
                                      "leader_id": "not-a-number"})
    tagged = shift_db.goals_by_status("active", leader_id=maya["id"])
    assert [g["title"] for g in tagged] == ["Solo breakfast open"]
    # Tagged goals stay off the Today dashboard but show on the 1:1 page
    assert b"Solo breakfast open" not in client.get("/shift/").data
    assert b"Store goal" in client.get("/shift/").data
    assert b"Solo breakfast open" in client.get(f"/shift/oneonone/{maya['id']}").data


def test_tagged_goals_private_to_leader_and_operator(client):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    make_leader(client, name="Dana", pin="5555", role="admin")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    login_operator(client)
    client.post("/shift/goals", data={"title": "Confidence on window",
                                      "period": "monthly",
                                      "leader_id": str(maya["id"])})
    gid = shift_db.goals_by_status("active", leader_id=maya["id"])[0]["id"]
    client.post("/shift/logout")

    # Another lead — and even an admin-role leader — can't see or touch it
    for name, pin in [("Devon", "8888"), ("Dana", "5555")]:
        login_leader(client, name=name, pin=pin)
        assert b"Confidence on window" not in client.get("/shift/goals").data
        assert client.get(f"/shift/goals/{gid}",
                          follow_redirects=False).status_code == 302
        client.post(f"/shift/goals/{gid}/status", data={"status": "archived"})
        assert shift_db.goal_with_updates(gid)[0]["status"] == "active"
        client.post(f"/shift/goals/{gid}/update", data={"note": "snooping"})
        assert not shift_db.goal_with_updates(gid)[1]
        # A lead can't tag a goal to someone else — it lands store-wide
        client.post("/shift/goals", data={"title": f"Planted by {name}",
                                          "period": "weekly",
                                          "leader_id": str(maya["id"])})
        planted = [g for g in shift_db.goals_by_status("active")
                   if g["title"] == f"Planted by {name}"]
        assert planted[0]["leader_id"] is None
        client.post("/shift/logout")

    # Maya herself sees and updates it
    login_leader(client)
    assert b"Confidence on window" in client.get("/shift/goals").data
    assert client.get(f"/shift/goals/{gid}").status_code == 200
    client.post(f"/shift/goals/{gid}/update", data={"note": "getting there"})
    assert shift_db.goal_with_updates(gid)[1][0]["note"] == "getting there"


def test_backups_and_pin_resets_are_operator_only(client):
    make_leader(client)
    make_leader(client, name="Dana", pin="5555", role="admin")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    login_operator(client)
    client.post(f"/shift/oneonone/{maya['id']}/topics",
                data={"topic": "private growth topic"})
    client.post("/shift/logout")

    # Dana is an admin, but backups carry 1:1 content and PIN resets allow
    # impersonation — both bounce her to the admin page.
    login_leader(client, name="Dana", pin="5555")
    assert client.get("/shift/admin").status_code == 200
    resp = client.get("/shift/admin/export", follow_redirects=False)
    assert resp.status_code == 302
    resp = client.post("/shift/admin/import", data={"confirm": "1"},
                       follow_redirects=False)
    assert resp.status_code == 302
    client.post(f"/shift/admin/leaders/{maya['id']}/reset-pin",
                data={"pin": "9876"})
    client.post("/shift/logout")
    assert login_leader(client).status_code == 302     # Maya's PIN unchanged
    client.post("/shift/logout")

    login_operator(client)
    assert b"private growth topic" in client.get("/shift/admin/export").data
    shift_db.add_member("Avery")
    av = next(m for m in shift_db.roster() if m["name"] == "Avery")
    client.post(f"/shift/oneonone/member/{av['id']}/topics",
                data={"topic": "operator-only coaching note"})
    assert b"operator-only coaching note" in client.get("/shift/admin/export").data
    client.post("/shift/logout")
    login_leader(client, name="Dana", pin="5555")
    assert client.get("/shift/admin/export",
                      follow_redirects=False).status_code == 302


def test_admin_cannot_take_over_a_login_by_re_adding_it(client):
    make_leader(client)                                   # Maya, PIN 4721
    make_leader(client, name="Dana", pin="5555", role="admin")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    login_operator(client)
    client.post(f"/shift/oneonone/{maya['id']}/topics",
                data={"topic": "MAYA-PRIVATE-NOTE"})
    client.post("/shift/logout")

    # Re-adding an existing name would replace its PIN — a back-door reset
    login_leader(client, name="Dana", pin="5555")
    for name in ["Maya", "  mAYA "]:
        resp = client.post("/shift/admin/leaders/add",
                           data={"name": name, "pin": "0000", "role": "admin"},
                           follow_redirects=True)
        assert b"only the Operator can reset a PIN" in resp.data
    # Brand-new names still work for admins
    client.post("/shift/admin/leaders/add", data={"name": "Jordan", "pin": "6060"})
    client.post("/shift/logout")
    assert login_leader(client, name="Maya", pin="0000").status_code == 401
    assert login_leader(client).status_code == 302        # original PIN intact
    assert next(l for l in shift_db.leaders()
                if l["name"] == "Maya")["role"] == "lead"
    assert b"MAYA-PRIVATE-NOTE" in client.get(f"/shift/oneonone/{maya['id']}").data
    client.post("/shift/logout")
    assert login_leader(client, name="Jordan", pin="6060").status_code == 302
    client.post("/shift/logout")

    # The Operator keeps the re-add path (reactivate + new PIN)
    login_operator(client)
    client.post("/shift/admin/leaders/add", data={"name": "Maya", "pin": "1357"})
    client.post("/shift/logout")
    assert login_leader(client, name="Maya", pin="1357").status_code == 302


def test_restore_invalidates_leader_sessions(client):
    make_leader(client)
    login_operator(client)
    raw = client.get("/shift/admin/export").data
    client.post("/shift/logout")

    login_leader(client)
    assert client.get("/shift/").status_code == 200
    assert shift_db.import_json(raw.decode()) is None
    # Her 30-day cookie predates the restore: next request re-logs-in
    resp = client.get("/shift/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/shift/login" in resp.headers["Location"]


# --- team-member 1:1s (Operator-only) ---------------------------------------

def _roster_id(name):
    return next(m["id"] for m in shift_db.roster(include_inactive=True)
                if m["name"] == name)


def test_member_oneonone_operator_only(client):
    make_leader(client)                                   # Maya, lead
    make_leader(client, name="Dana", pin="5555", role="admin")
    maya = next(l for l in shift_db.leaders() if l["name"] == "Maya")
    shift_db.add_member("Avery")
    av = _roster_id("Avery")
    assert av == maya["id"]                               # same numeric id
    login_operator(client)
    client.post(f"/shift/oneonone/member/{av}/topics",
                data={"topic": "Cross-train breading"})
    tid = shift_db.oneonone_for_member(av)[0][0]["id"]
    client.post("/shift/logout")

    for name, pin in [("Maya", "4721"), ("Dana", "5555")]:
        login_leader(client, name=name, pin=pin)
        resp = client.get(f"/shift/oneonone/member/{av}", follow_redirects=False)
        assert resp.status_code == 302, name
        assert b"Cross-train" not in client.get(
            f"/shift/oneonone/member/{av}", follow_redirects=True).data
        for url, data in [
            (f"/shift/oneonone/member/{av}/topics", {"topic": "sneaky"}),
            (f"/shift/oneonone/member/topic/{tid}/toggle", {"discussed": "1"}),
            (f"/shift/oneonone/member/topic/{tid}/delete", {}),
            # The leader route with a member topic id only ever touches
            # oneonone_topics.
            (f"/shift/oneonone/topic/{tid}/toggle", {"discussed": "1"}),
            (f"/shift/oneonone/topic/{tid}/delete", {}),
        ]:
            assert client.post(url, data=data).status_code == 302, (name, url)
        agenda, history = shift_db.oneonone_for_member(av)
        assert [t["topic"] for t in agenda] == ["Cross-train breading"] \
            and not history, name
        # ?q= on the index never looks anything up for a leader
        resp = client.get("/shift/oneonone?q=Avery", follow_redirects=False)
        own = next(l for l in shift_db.leaders() if l["name"] == name)
        assert resp.headers["Location"].endswith(f"/shift/oneonone/{own['id']}")
        page = client.get("/shift/oneonone?q=Avery", follow_redirects=True).data
        assert b"Avery" not in page and b"Cross-train" not in page
        for path in ["/shift/", "/shift/more", "/shift/roster"]:
            page = client.get(path).data
            assert b"Cross-train" not in page, (name, path)
        assert b"/shift/oneonone/member/" not in client.get("/shift/roster").data
        client.post("/shift/logout")


def test_member_topic_routes(client):
    shift_db.add_member("Avery")
    av = _roster_id("Avery")
    login_operator(client)
    resp = client.post(f"/shift/oneonone/member/{av}/topics",
                       data={"topic": "Wants weekend closes"})
    assert resp.headers["Location"].endswith("#agenda")
    resp = client.post(f"/shift/oneonone/member/{av}/topics",
                       data={"topic": "   "}, follow_redirects=True)
    assert b"needs some words" in resp.data
    page = client.get(f"/shift/oneonone/member/{av}").data
    assert b"Wants weekend closes" in page
    assert b"never go in this app" in page
    assert b"Only you (the Operator)" in page

    tid = shift_db.oneonone_for_member(av)[0][0]["id"]
    resp = client.post(f"/shift/oneonone/member/topic/{tid}/toggle",
                       data={"discussed": "1", "note": "two closes a week"})
    assert resp.headers["Location"].endswith("#agenda")
    page = client.get(f"/shift/oneonone/member/{av}").data
    assert b"Past 1:1s" in page and b"two closes a week" in page
    resp = client.post(f"/shift/oneonone/member/topic/{tid}/toggle",
                       data={"discussed": "1"}, follow_redirects=True)
    assert b"Already marked discussed by Operator" in resp.data
    resp = client.post(f"/shift/oneonone/member/topic/{tid}/toggle",
                       data={"discussed": "0"})
    assert resp.headers["Location"].endswith(f"#topic-{tid}")
    assert shift_db.get_member_topic(tid)["discussed_at"] is None

    client.post(f"/shift/oneonone/member/{av}/topics", data={"topic": "Drop me"})
    drop = next(t for t in shift_db.oneonone_for_member(av)[0]
                if t["topic"] == "Drop me")
    client.post(f"/shift/oneonone/member/topic/{drop['id']}/delete")
    assert shift_db.get_member_topic(drop["id"]) is None
    client.post(f"/shift/oneonone/member/topic/{tid}/toggle", data={"discussed": "1"})
    resp = client.post(f"/shift/oneonone/member/topic/{tid}/delete",
                       follow_redirects=True)
    assert b"part of meeting history" in resp.data
    assert shift_db.get_member_topic(tid)

    # Stale ids and unknown members never 500
    for url in ["/shift/oneonone/member/topic/999999/toggle",
                "/shift/oneonone/member/topic/999999/delete",
                "/shift/oneonone/member/999999/topics"]:
        assert client.post(url, data={"discussed": "1", "topic": "x"}).status_code == 302
    resp = client.get("/shift/oneonone/member/999999", follow_redirects=True)
    assert b"isn&#39;t on the roster" in resp.data or b"isn't on the roster" in resp.data


def test_oneonone_index_picker(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    for n in ["Avery", "maya", "Blake"]:
        shift_db.add_member(n)
    login_operator(client)
    page = client.get("/shift/oneonone").data.decode()
    assert "Leaders · shared agendas" in page and "Team members" in page
    assert '<option value="Avery">' in page and '<option value="Blake">' in page
    # Roster 'maya' + leader 'Maya' → exactly one option, labelled Leader
    assert page.lower().count('<option value="maya"') == 1
    assert '<option value="Maya" label="Leader">' in page

    resp = client.get("/shift/oneonone?q=avery", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/oneonone/member/{_roster_id('Avery')}")
    resp = client.get("/shift/oneonone?q=%20MAYA%20", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{maya['id']}")
    def match_block(q):
        # Only the search-results block — the roster browse list further
        # down links everyone regardless of the query.
        page = client.get("/shift/oneonone", query_string={"q": q}).data.decode()
        return page[page.index("</datalist>"):page.index('id="leaders"')]

    block = match_block("av")
    assert f"/shift/oneonone/member/{_roster_id('Avery')}" in block
    assert "Blake" not in block and "No one matches" not in block
    assert '<span class="note-cat">Team</span>' in block
    block = match_block("ay")                             # partial leader name
    assert f"/shift/oneonone/{maya['id']}" in block
    assert '<span class="note-cat">Leader</span>' in block
    shift_db.add_member("Casey Former")
    cf = _roster_id("Casey Former")
    shift_db.add_member_topic(cf, "History", "Operator")
    shift_db.set_member_active(cf, False)
    block = match_block("casey")
    assert f"/shift/oneonone/member/{cf}" in block
    assert '<span class="note-cat">former</span>' in block
    roster_size = len(shift_db.roster(include_inactive=True))
    page = client.get("/shift/oneonone?q=nobody").data
    assert b"No one matches" in page
    assert len(shift_db.roster(include_inactive=True)) == roster_size

    client.post(f"/shift/oneonone/member/{_roster_id('Blake')}/topics",
                data={"topic": "Check in"})
    page = client.get("/shift/oneonone").data.decode()
    team = page[page.index('id="team"'):]
    assert "Blake" in team and "● 1 waiting" in team


def test_member_page_defers_to_leader_login(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    shift_db.add_member("Maya")
    mid = _roster_id("Maya")
    login_operator(client)
    resp = client.get(f"/shift/oneonone/member/{mid}", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{maya['id']}")
    resp = client.post(f"/shift/oneonone/member/{mid}/topics",
                       data={"topic": "Shadow file"}, follow_redirects=True)
    assert b"has a leader login" in resp.data
    assert shift_db.oneonone_for_member(mid) == ([], [])

    # Pre-promotion history still opens, with the banner and no compose form
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("INSERT INTO oneonone_member_topics (member_id, topic, "
                     "added_by, created_at) VALUES (?,?,?,?)",
                     (mid, "From before", "Operator", "2026-01-01 10:00"))
    page = client.get(f"/shift/oneonone/member/{mid}").data
    assert b"From before" in page and b"has a leader login" in page
    assert b"+ Add a talking point" not in page


def test_member_page_content(client):
    shift_db.add_member("Avery")
    av = _roster_id("Avery")
    login_operator(client)
    page = client.get(f"/shift/oneonone/member/{av}").data
    for leader_only in [b"Their goals", b"Course", b"Action item"]:
        assert leader_only not in page
    shift_db.set_member_active(av, False)
    page = client.get(f"/shift/oneonone/member/{av}").data
    assert b"active roster" in page


def test_roster_1on1_link_operator_only(client):
    make_leader(client)
    shift_db.add_member("Avery")
    login_operator(client)
    assert b"/shift/oneonone/member/" in client.get("/shift/roster").data
    client.post("/shift/logout")
    login_leader(client)
    assert b"/shift/oneonone/member/" not in client.get("/shift/roster").data


def test_leader_more_badge_ignores_member_topics(client):
    make_leader(client)
    maya = shift_db.leaders()[0]
    shift_db.add_member("Avery")
    login_operator(client)
    for t in ["One", "Two", "Three"]:
        client.post(f"/shift/oneonone/member/{_roster_id('Avery')}/topics",
                    data={"topic": t})
    client.post("/shift/logout")
    login_leader(client)
    assert b"on your agenda" not in client.get("/shift/more").data
    assert shift_db.open_topic_count(maya["id"]) == 0


# --- shout-outs ---------------------------------------------------------------

@pytest.fixture()
def slack_shares(monkeypatch):
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_SHOUTOUT_SLACK_CHANNEL", "C123TEAM")
    calls = []
    monkeypatch.setattr("shift_app.send_shoutout_slack",
                        lambda shoutout: calls.append(shoutout))
    return calls


def test_shoutout_posts_and_crossposts(client, slack_shares):
    make_leader(client)
    login_leader(client)
    resp = client.post("/shift/shoutouts", data={
        "member_name": "Avery P", "value_tag": "speed",
        "message": "Flew through the lunch rush", "share": "1",
    })
    assert resp.status_code == 302 and "#shout-" in resp.headers["Location"]
    assert len(slack_shares) == 1
    assert slack_shares[0]["member_name"] == "Avery P"
    shout = shift_db.shoutout_feed()[0]
    assert shout["shared_at"] and shout["author"] == "Maya"
    # The roster learned the new name for next time's autosuggest
    assert any(m["name"] == "Avery P" for m in shift_db.roster())
    # Feed + Today strip render it
    assert b"Flew through the lunch rush" in client.get("/shift/shoutouts").data
    assert b"Avery P" in client.get("/shift/").data


def test_shoutout_without_checkbox_stays_in_app(client, slack_shares):
    login_operator(client)
    client.post("/shift/shoutouts", data={
        "member_name": "Sam", "message": "Covered a double",
    })
    assert not slack_shares
    assert shift_db.shoutout_feed()[0]["shared_at"] is None


def test_shoutout_unconfigured_slack_is_honest(client, monkeypatch):
    monkeypatch.setattr(shift_app, "SHIFT_SHOUTOUT_SLACK_CHANNEL", "")
    login_operator(client)
    resp = client.post("/shift/shoutouts", data={
        "member_name": "Sam", "message": "Great hustle", "share": "1",
    }, follow_redirects=True)
    assert b"Posted" in resp.data
    assert shift_db.shoutout_feed()[0]["shared_at"] is None   # never stamped


def test_shoutout_validation_and_delete_rules(client, slack_shares):
    make_leader(client)
    make_leader(client, name="Devon", pin="8888")
    login_leader(client)
    resp = client.post("/shift/shoutouts", data={"member_name": "  ",
                                                 "message": "hi"},
                       follow_redirects=True)
    assert b"needs a name and a message" in resp.data
    client.post("/shift/shoutouts", data={"member_name": "Avery",
                                          "message": "Nice save"})
    shout = shift_db.shoutout_feed()[0]
    client.post("/shift/logout")

    login_leader(client, name="Devon", pin="8888")
    client.post(f"/shift/shoutouts/{shout['id']}/delete")
    assert shift_db.get_shoutout(shout["id"])          # not Devon's to delete
    client.post("/shift/logout")
    login_operator(client)
    client.post(f"/shift/shoutouts/{shout['id']}/delete")
    assert shift_db.get_shoutout(shout["id"]) is None


def test_shoutout_slack_payload(client, monkeypatch):
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_SHOUTOUT_SLACK_CHANNEL", "C123TEAM")
    posted = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)

        class R:
            status_code = 200
            text = '{"ok": true}'

            def json(self):
                return {"ok": True}
        return R()

    monkeypatch.setattr(shift_app.requests, "post", fake_post)
    sid = shift_db.add_shoutout("Avery <&> P", "speed",
                                "Tell <!channel> nothing", "Maya", share=True)
    shift_app.send_shoutout_slack(shift_db.get_shoutout(sid))
    assert posted["url"] == "https://slack.com/api/chat.postMessage"
    assert posted["headers"]["Authorization"] == "Bearer xoxb-test"
    body = posted["json"]
    assert body["channel"] == "C123TEAM"
    assert "SHOUT-OUT: Avery &lt;&amp;&gt; P" in body["text"]
    assert "⚡ Speed of service" in body["text"]
    assert "<!channel>" not in body["text"]            # mrkdwn injection escaped
    # ok:true marks real delivery — that's what the 📣 badge renders from
    assert shift_db.get_shoutout(sid)["delivered_at"]


def test_shoutout_badge_honest_on_failed_delivery(client, monkeypatch):
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_SHOUTOUT_SLACK_CHANNEL", "C123TEAM")

    def fake_post(url, **kwargs):
        class R:
            status_code = 200
            text = '{"ok": false, "error": "not_in_channel"}'

            def json(self):
                return {"ok": False, "error": "not_in_channel"}
        return R()

    monkeypatch.setattr(shift_app.requests, "post", fake_post)
    login_operator(client)
    client.post("/shift/shoutouts", data={"member_name": "Sam",
                                          "message": "Clutch", "share": "1"})
    shout = shift_db.shoutout_feed()[0]
    assert shout["shared_at"] and shout["delivered_at"] is None
    assert "📣 Slack".encode() not in client.get("/shift/shoutouts").data


def test_shoutout_never_resurrects_departed_member(client, slack_shares):
    login_operator(client)
    shift_db.add_member("Sam Kennedy", role="leader")
    sam = next(m for m in shift_db.roster() if m["name"] == "Sam Kennedy")
    shift_db.set_member_active(sam["id"], False)
    client.post("/shift/shoutouts", data={
        "member_name": "Sam Kennedy", "message": "Thanks for the years!",
    })
    kept = next(m for m in shift_db.roster(include_inactive=True)
                if m["name"] == "Sam Kennedy")
    assert kept["active"] == 0 and kept["role"] == "leader"
    assert shift_db.shoutout_feed()[0]["member_name"] == "Sam Kennedy"


def test_shoutout_slack_failure_never_breaks_post(client, monkeypatch):
    monkeypatch.setattr(shift_app, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(shift_app, "SHIFT_SHOUTOUT_SLACK_CHANNEL", "C123TEAM")

    def boom(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(shift_db, "get_shoutout", boom)  # prepare path blows up
    login_operator(client)
    resp = client.post("/shift/shoutouts", data={
        "member_name": "Sam", "message": "Clutch", "share": "1",
    })
    assert resp.status_code == 302                     # the post still lands
    assert shift_db.shoutout_feed()[0]["member_name"] == "Sam"


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
    assert shift_db.recovery_feed()[2][0]["guest_name"] == "Sam"


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


def test_recovery_contact_route_flow(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/recovery", data={"guest_name": "Jordan",
                                         "issue": "order-error",
                                         "remedy": "free-entree-card"})
    rec = shift_db.recovery_feed()[0][0]
    resp = client.post(f"/shift/recovery/{rec['id']}/contact",
                       data={"contacted": "1", "note": "coming Saturday"})
    assert resp.headers["Location"].endswith(f"#rec-{rec['id']}")
    page = client.get("/shift/recovery").data
    assert b"Waiting to come back (1)" in page and b"coming Saturday" in page

    today_page = client.get("/shift/").data
    assert "📞 1 contacted — coming back".encode() in today_page
    assert b"Jordan" in today_page

    # Second contact from a stale page: told, first stamp kept
    resp = client.post(f"/shift/recovery/{rec['id']}/contact",
                       data={"contacted": "1"}, follow_redirects=True)
    assert b"Already marked contacted by Maya" in resp.data

    # They came back: resolving keeps both stamps
    client.post(f"/shift/recovery/{rec['id']}/resolve",
                data={"resolved": "1", "note": "picked up the card"})
    done = shift_db.get_recovery(rec["id"])
    assert done["resolved_by"] == "Maya" and done["contacted_by"] == "Maya"
    assert b"Jordan" not in client.get("/shift/").data

    # Undo on a resolved row is refused with a flash, not a 500
    resp = client.post(f"/shift/recovery/{rec['id']}/contact",
                       data={"contacted": "0"}, follow_redirects=True)
    assert b"already fully resolved" in resp.data


def test_recovery_contacted_now_checkbox(client):
    login_operator(client)
    client.post("/shift/recovery", data={
        "guest_name": "Sam", "issue": "food-quality", "remedy": "remade-now",
        "contacted_now": "1",
    })
    open_recs, awaiting, _ = shift_db.recovery_feed()
    assert not open_recs and awaiting[0]["guest_name"] == "Sam"
    page = client.get("/shift/recovery").data
    assert b"Waiting to come back (1)" in page
    # The open list's empty-state must not celebrate while a guest waits
    assert b"every guest taken care of" not in page
    assert b"Nobody left to contact" in page
    # Today card renders in its calm (non-red) awaiting-only form
    today_page = client.get("/shift/").data
    assert "📞 1 contacted — coming back".encode() in today_page
    assert b"Hand over what was promised" in today_page
    # More badge must agree: no red "open" badge, a calm coming-back note
    more_page = client.get("/shift/more").data
    assert b"open</span>" not in more_page
    assert "📞 1 coming back".encode() in more_page


def test_recovery_operator_create_and_resolve(client):
    login_operator(client)
    client.post("/shift/recovery", data={
        "guest_name": "Pat", "issue": "wait-time", "remedy": "free-dessert-drink",
    })
    rec = shift_db.recovery_feed()[0][0]
    assert rec["logged_by"] == "Operator"
    client.post(f"/shift/recovery/{rec['id']}/resolve",
                data={"resolved": "1", "note": "spoke in person"})
    assert shift_db.get_recovery(rec["id"])["resolved_by"] == "Operator"


def test_recovery_double_resolve_route_flashes(client):
    make_leader(client)
    login_operator(client)
    client.post("/shift/recovery", data={"guest_name": "Jordan",
                                         "issue": "service", "remedy": "refund"})
    rec = shift_db.recovery_feed()[0][0]
    client.post(f"/shift/recovery/{rec['id']}/resolve",
                data={"resolved": "1", "note": "mailed cards"})
    client.post("/shift/logout")

    # Maya resolves from a stale page: first stamp survives, she's told
    login_leader(client)
    resp = client.post(f"/shift/recovery/{rec['id']}/resolve",
                       data={"resolved": "1"}, follow_redirects=True)
    assert b"Already resolved by Operator" in resp.data
    kept = shift_db.get_recovery(rec["id"])
    assert kept["resolved_by"] == "Operator"
    assert kept["resolution_note"] == "mailed cards"


def test_recovery_today_card_truncates(client):
    make_leader(client)
    login_leader(client)
    for n in range(5):
        client.post("/shift/recovery", data={"guest_name": f"Guest{n}",
                                             "issue": "service", "remedy": "refund"})
    page = client.get("/shift/").data
    for n in range(3):
        assert f"Guest{n}".encode() in page
    assert b"Guest3" not in page and b"Guest4" not in page
    assert b"+2 more" in page
    assert b"5 guests waiting" in page


def test_recovery_more_badge_counts_open(client):
    make_leader(client)
    login_leader(client)
    assert b"open" not in client.get("/shift/more").data.split(b"Guest recovery")[1][:120]
    client.post("/shift/recovery", data={"guest_name": "Jordan",
                                         "issue": "service", "remedy": "refund"})
    assert "● 1 open".encode() in client.get("/shift/more").data


def test_recovery_textual_phone_still_shown(client):
    login_operator(client)
    client.post("/shift/recovery", data={
        "guest_name": "Maria", "guest_phone": "ask for Maria at pickup",
        "issue": "order-error", "remedy": "remade-now",
    })
    page = client.get("/shift/recovery").data
    assert b"ask for Maria at pickup" in page       # visible, just not a tel: link
    assert b"tel:" not in page
    assert "no contact on file".encode() not in page


def test_demote_last_admin_blocked_without_pin(client, monkeypatch):
    make_leader(client, name="Dana", pin="5555", role="admin")
    dana = shift_db.leaders()[0]
    login_leader(client, name="Dana", pin="5555")
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "")

    resp = client.post(f"/shift/admin/leaders/{dana['id']}/role",
                       data={"role": "lead"}, follow_redirects=True)
    assert b"lock everyone out" in resp.data
    assert shift_db.get_leader(dana["id"])["role"] == "admin"

    # With a second admin (or the master PIN back), demotion works again
    shift_db.add_leader("Backup", "7777", role="admin")
    client.post(f"/shift/admin/leaders/{dana['id']}/role", data={"role": "lead"})
    assert shift_db.get_leader(dana["id"])["role"] == "lead"


def test_recovery_issue_radar_visible_to_all_leaders(client):
    make_leader(client)
    login_leader(client)
    client.post("/shift/recovery", data={"guest_name": "G1",
                                         "issue": "order-error", "remedy": "refund"})
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
