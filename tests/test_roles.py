"""Who can open what in Team Hub: every role against every page.

Navigation only hides links; these routes are the real gate. One table
says, for each GET page, which roles get it (200) and which are turned
away (a redirect) — a team member signed in with a clock-in number must
never reach a leader page, a lead never another leader's 1:1 or the admin
pages, an admin-role leader never the Operator-only ones."""

import io

import pytest

import app as cfa_app
import hub
import hub_db
import shift_app
import shift_db

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
ROLES = ("member", "lead", "admin", "operator")
EVERYONE = set(ROLES)
LEADERS = {"lead", "admin", "operator"}
EDITORS = {"admin", "operator"}
OPERATOR = {"operator"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A store with one of each: a team member with Hub access, a lead, an
    admin-role leader, the Operator, and a page/file/thread to open."""
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "roles.db"))
    monkeypatch.setenv("SHIFT_HUB_FILES_DIR", str(tmp_path / "files"))
    shift_db.init_db()
    shift_app._pin_fails.clear()
    hub._fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    cfa_app.app.config["TESTING"] = True
    shift_db.add_leader("Lena", "1111", "lead")
    shift_db.add_leader("Ada", "2222", "admin")
    leaders = {l["name"]: l["id"] for l in shift_db.leaders()}
    shift_db.add_member("Sam Patel")
    member = next(m for m in shift_db.roster() if m["name"] == "Sam Patel")
    hub_db.grant(member["id"], "1042", "Operator")
    operator = hub_db.Viewer("operator", None, "Operator", "admin")
    foh = next(c["id"] for c in hub_db.categories() if c["name"] == "FOH Operations")
    page_id = hub_db.create_page(operator, {
        "category_id": foh, "section": "", "title": "Drive-thru", "summary": "",
        "keywords": "", "body": "Steps.", "leaders_only": 0, "required": 1,
        "annual_review": 0})
    page = hub_db.get_page(page_id)
    assert hub_db.publish(operator, page_id, page["rev"], "", True) is None
    att, _ = hub_db.add_attachment(operator, hub_db.get_page(page_id), "x.pdf", PDF,
                                   rev=hub_db.get_page(page_id)["rev"])
    shift_db.ensure_runs_for_date(shift_db.today_local())
    run_id = shift_db.runs_for_date(shift_db.today_local())[0]["id"]
    template_id = shift_db.all_templates()[0]["id"]
    with shift_db.connect() as conn:
        goal_id = conn.execute(
            "INSERT INTO goals (title, period, status, created_at, created_by) "
            "VALUES ('Store goal', 'weekly', 'active', '2026-01-01 09:00', 'Operator')"
        ).lastrowid
    yield {"lead": leaders["Lena"], "admin": leaders["Ada"], "member": member["id"],
           "page": page_id, "file": att["id"], "run": run_id, "template": template_id,
           "goal": goal_id, "foh": foh}


def sign_in(client, role):
    if role == "member":
        return client.post("/hub/login", data={"name": "Sam", "code": "1042"})
    name, pin = {"lead": ("Lena", "1111"), "admin": ("Ada", "2222"),
                 "operator": ("Operator", "9999")}[role]
    return client.post("/shift/login", data={"name": name, "pin": pin})


def table(w):
    """(path, roles that may open it). Anything else must be turned away."""
    lead, admin = w["lead"], w["admin"]
    return [
        # Systems & Procedures — everyone (filtered by can_view)
        ("/hub/library", EVERYONE), ("/hub/search?q=drive", EVERYONE),
        ("/hub/to-read", EVERYONE), ("/hub/me", EVERYONE), ("/hub/menu", EVERYONE),
        (f"/hub/c/{w['foh']}", EVERYONE), (f"/hub/page/{w['page']}", EVERYONE),
        (f"/hub/file/{w['file']}", EVERYONE),
        ("/hub/", {"member"}),                          # a leader's home is Today
        # Editors (the Operator + admin-role leaders)
        ("/hub/manage", EDITORS), ("/hub/new", EDITORS),
        (f"/hub/page/{w['page']}/edit", EDITORS), (f"/hub/page/{w['page']}/history", EDITORS),
        (f"/hub/page/{w['page']}/readers", EDITORS),
        (f"/hub/page/{w['page']}/readers.csv", EDITORS),
        (f"/hub/page/{w['page']}/v/1", EDITORS), (f"/hub/file/{w['file']}/remove", EDITORS),
        ("/hub/people", OPERATOR),
        # Leader tools — never a team member
        ("/shift/", LEADERS), ("/shift/?edit=goals", LEADERS), ("/shift/more", LEADERS),
        ("/shift/checklists", LEADERS),
        (f"/shift/checklists/run/{w['run']}", LEADERS), ("/shift/checklists/scorecard", LEADERS),
        ("/shift/history", LEADERS), ("/shift/notes", LEADERS),
        ("/shift/announcements", LEADERS), ("/shift/recovery", LEADERS),
        ("/shift/equipment", LEADERS),
        ("/shift/shoutouts", LEADERS), ("/shift/roster", LEADERS),
        ("/shift/development", EDITORS),        # a lead is sent on to their own page
        ("/shift/oneonone", LEADERS), ("/shift/todo", LEADERS),
        (f"/shift/oneonone/member/{w['member']}", LEADERS),   # each opens their own thread
        # Private to one leader (+ the Operator) — admin-role leaders included
        (f"/shift/oneonone/{lead}", {"lead", "operator"}),
        (f"/shift/oneonone/{admin}", {"admin", "operator"}),
        (f"/shift/development/{lead}", {"lead", "admin", "operator"}),
        (f"/shift/development/{admin}", {"admin", "operator"}),
        # Admin pages, and the Operator's own
        ("/shift/admin", EDITORS), ("/shift/admin/templates", EDITORS),
        ("/shift/admin/checklist-views", EDITORS),
        ("/shift/admin/templates/new", EDITORS),
        (f"/shift/admin/templates/{w['template']}", EDITORS),
        ("/shift/admin/export", OPERATOR),
    ]


@pytest.mark.parametrize("role", ROLES)
def test_every_page_for_every_role(world, role):
    with cfa_app.app.test_client() as client:
        assert sign_in(client, role).status_code == 302
        for path, allowed in table(world):
            status = client.get(path).status_code
            if role in allowed:
                assert status == 200, (role, path, status)
            else:
                assert status in (302, 404), (role, path, status)


def test_retired_pages_land_on_today(world):
    """Lineups and goals left the app: old links and home-screen
    shortcuts land a leader on Today, and never get a team member past the
    leader gate."""
    for role in ROLES:
        with cfa_app.app.test_client() as client:
            sign_in(client, role)
            for path in ("/shift/lineup", "/shift/goals", f"/shift/goals/{world['goal']}"):
                resp = client.get(path)
                where = resp.headers.get("Location", "")
                assert resp.status_code == 302, (role, path)
                if role in LEADERS:
                    assert where.endswith("/shift/"), (role, path, where)
                else:
                    assert "/shift" not in where, (role, path, where)


def test_signed_out_sees_only_the_front_door(world):
    with cfa_app.app.test_client() as client:
        for path, _ in table(world):
            resp = client.get(path)
            assert resp.status_code == 302 and "/login" in resp.headers["Location"], path
        assert client.get("/hub/login").status_code == 200
        assert client.get("/hub/manifest.webmanifest").status_code == 200
