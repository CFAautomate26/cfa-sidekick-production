"""Team Hub's home (/shift/): a leader lands on their to-dos and nothing
else — a greeting, every open to-do (ticked off right there) and a box to
add one; the Operator sees the to-dos they handed out. Goals, lineups and
the section tiles are gone; the tabs and ☰ menu lead with To-dos, then
Checklists."""

import re
from datetime import datetime
from html import escape

import pytest

import app as cfa_app
import hub
import hub_db
import shift_app
import shift_db

FRI = "2026-10-23"          # a Friday (weekday 4)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SHIFT_HUB_FILES_DIR", str(tmp_path / "hub_files"))
    shift_db.init_db()
    shift_app._pin_fails.clear()
    hub._fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    cfa_app.app.config["TESTING"] = True
    with cfa_app.app.test_client() as c:
        yield c


@pytest.fixture()
def clock(monkeypatch):
    """The store clock, frozen (store-local); set clock["now"] to move it."""
    state = {"now": datetime(2026, 10, 23, 12, 7, tzinfo=shift_db.STORE_TZ)}
    monkeypatch.setattr(shift_db, "now_local", lambda: state["now"])
    return state


def at(stamp):
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=shift_db.STORE_TZ)


def login_operator(client):
    return client.post("/shift/login", data={"name": "Operator", "pin": "9999"})


def make_leader(client, name="Maya", pin="4721", role="lead"):
    login_operator(client)
    client.post("/shift/admin/leaders/add", data={"name": name, "pin": pin, "role": role})
    client.post("/shift/logout")
    return next(l["id"] for l in shift_db.leaders() if l["name"] == name)


def login_leader(client, name="Maya", pin="4721"):
    return client.post("/shift/login", data={"name": name, "pin": pin})


def today_page(client):
    resp = client.get("/shift/")
    assert resp.status_code == 200
    return resp.data.decode()


def section(page, head_id):
    """The HTML of one Today section, by its heading id."""
    start = page.index(f'id="{head_id}"')
    return page[start:page.index("</section>", start)]


def todos(page):
    """The home's to-dos section."""
    part = page[page.index('id="todo-head"'):]
    return part[:part.index("</section>")]


def tabs(page):
    bar = page[page.index('<nav class="tabbar"'):]
    bar = bar[:bar.index("</nav>")]
    return re.findall(r"<span>([^<]+)</span>", bar)


def menu_words(client):
    menu = client.get("/shift/more").data.decode()
    return [w.strip() for w in re.findall(r'<span class="w">([^<]+)', menu)], menu


def test_home_is_just_the_to_dos(client, clock):
    maya = make_leader(client)
    for n in range(7):
        shift_db.add_task(maya, f"To-do {n}", "", "", "Operator")
    login_operator(client)
    client.post("/shift/announcements", data={"body": "Pretzel launch Monday"})
    client.post("/shift/logout")
    login_leader(client)
    page = today_page(client)
    assert "<title>Team Hub</title>" in page
    assert "Lunch is in session · Fri, Oct 23 · 12:07" in page
    assert "Good afternoon, Maya." in page and "<b>7</b> to-dos on your list." in page
    assert todos(page).count('class="row todo-row"') == 7      # all of them, not a top five
    # Nothing else on the home screen: no goals, tiles, counts or announcements
    for gone in ("Today's goals", "Today&#39;s goals", 'class="tile"', "Your Team Hub",
                 "On shift", "goal-form", "new announcement", "Your leaders", "Past days"):
        assert gone not in page, gone
    assert tabs(page) == ["To-dos", "Checklists", "Procedures", "Meetings", "Leadership"]


def test_greeting_follows_the_clock(client, clock):
    login_operator(client)
    for stamp, word in (("2026-10-23 07:15", "Good morning."), ("2026-10-23 12:00", "Good afternoon."),
                        ("2026-10-23 17:30", "Good evening."), ("2026-10-24 01:10", "Good evening.")):
        clock["now"] = at(stamp)
        assert word in today_page(client), stamp     # the Operator login has no name


def test_today_rolls_over_at_4am_not_midnight(client, clock):
    maya = make_leader(client)
    shift_db.add_task(maya, "Count the safe", "", FRI, "Operator")
    login_leader(client)
    clock["now"] = at("2026-10-24 03:59")                     # Friday's close, after midnight
    page = today_page(client)
    assert "Dinner is in session · Fri, Oct 23 · 3:59" in page and "Due today" in page
    clock["now"] = at("2026-10-24 04:00")
    page = today_page(client)
    assert "Breakfast is in session · Sat, Oct 24 · 4:00" in page
    assert "Overdue · was due Fri, Oct 23" in page


def test_leader_todos_tick_back_to_today(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    shift_db.add_task(maya, "Order DT headset batteries", "", FRI, "Operator")
    shift_db.add_task(maya, "Extra", "", "", "Daniel")
    shift_db.add_task(daniel, "Daniel's own to-do", "", "", "Operator")
    login_leader(client)
    page = today_page(client)
    mine = todos(page)
    assert "Your to-dos" in mine and "Order DT headset batteries" in mine and "Due today" in mine
    assert "from the Operator" in mine and "from Daniel" in mine
    assert "Daniel&#39;s own to-do" not in page and "Daniel's own to-do" not in page
    task = next(t for t in shift_db.tasks_for_leader(maya)[0]
                if t["title"] == "Order DT headset batteries")
    assert 'name="back" value="today"' in mine
    resp = client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1", "back": "today"})
    assert resp.headers["Location"].endswith("/shift/")
    assert "Order DT headset batteries" not in today_page(client)


def test_add_a_to_do_from_home(client, clock):
    maya = make_leader(client)
    login_leader(client)
    page = today_page(client)
    assert f'action="/shift/todo/{maya}/assign"' in page and "Add a to-do" in page
    resp = client.post(f"/shift/todo/{maya}/assign",
                       data={"title": "Restock DT batteries", "due_date": FRI, "back": "today"})
    assert resp.headers["Location"].endswith("/shift/")
    task = shift_db.tasks_for_leader(maya)[0][0]
    assert (task["title"], task["due_date"], task["assigned_by"]) == \
        ("Restock DT batteries", FRI, "Maya")
    assert "Restock DT batteries" in todos(today_page(client))
    resp = client.post(f"/shift/todo/{maya}/assign", data={"title": "  ", "back": "today"},
                       follow_redirects=True)
    assert "A to-do needs a title" in resp.data.decode()
    assert len(shift_db.tasks_for_leader(maya)[0]) == 1
    # Without back=today the To-dos page keeps its own redirect
    resp = client.post(f"/shift/todo/{maya}/assign", data={"title": "From the list page"})
    assert resp.headers["Location"].endswith(f"/shift/todo/{maya}")


def test_operator_home_lists_the_to_dos_they_handed_out(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    shift_db.add_task(maya, "Order DT headset batteries", "", FRI, "Operator")
    shift_db.add_task(daniel, "Fix the ice machine filter", "", "2026-10-20", "Operator")
    shift_db.add_task(daniel, "Daniel's own reminder", "", "", "Daniel")
    login_operator(client)
    page = today_page(client)
    mine = todos(page)
    assert "To-dos you handed out" in mine and "<b>2</b> to-dos you handed out still open." in page
    # soonest due first, with whose it is
    assert mine.index("Fix the ice machine filter") < mine.index("Order DT headset batteries")
    assert "Daniel · " in mine and "Overdue · was due Tue, Oct 20" in mine
    assert "Maya · " in mine and "Due today" in mine
    assert "own reminder" not in mine                       # not handed out by the Operator
    assert "Add a to-do" not in page                        # no list of their own
    assert '<a class="row compact" href="/shift/todo">' in mine and "Assign a to-do" in mine
    assert "Every leader&#39;s to-dos" in mine or "Every leader's to-dos" in mine


def test_nothing_open(client, clock):
    make_leader(client)
    login_leader(client)
    page = today_page(client)
    assert "You&#39;re all caught up." in page or "You're all caught up." in page
    assert "Nothing on your list right now." in page
    client.post("/shift/logout")
    login_operator(client)
    page = today_page(client)
    assert "Every to-do you handed out is done." in page


def test_to_do_titles_are_escaped(client, clock):
    maya = make_leader(client)
    shift_db.add_task(maya, "<script>alert(1)</script>", "", "", "Operator")
    login_leader(client)
    page = today_page(client)
    assert "<script>alert(1)</script>" not in page and escape("<script>alert(1)</script>") in page


def test_goals_are_gone(client, clock):
    make_leader(client)
    login_leader(client)
    assert "goal" not in today_page(client).lower()
    page = client.get("/shift/?edit=goals").data.decode()
    assert "goal-form" not in page and "Leadership goal" not in page
    assert client.get("/shift/goals").headers["Location"].endswith("/shift/")
    assert client.post("/shift/today/goals", data={"leadership": "x"}).status_code in (404, 405)
    words, menu = menu_words(client)
    assert not [w for w in words if "Goal" in w or "Lineup" in w]


def test_menu_leads_with_to_dos_then_checklists(client, clock):
    maya = make_leader(client)
    shift_db.add_task(maya, "One", "", "", "Operator")
    shift_db.add_task(maya, "Two", "", "", "Operator")
    login_leader(client)
    words, menu = menu_words(client)
    assert words == ["To-dos", "Checklists", "Systems &amp; Procedures", "Meetings",
                     "Leadership Development"]
    to_dos = re.search(r'<a href="/shift/"[^>]*><span class="w">To-dos <span class="count">2</span>',
                       menu)
    assert to_dos, "To-dos word with her open count"
    for row in ("Guest recovery", "Equipment &amp; repairs", "Shift notes", "Announcements"):
        assert row in menu, row                                 # still a tap away
    page = today_page(client)
    assert re.search(r'<a href="/shift/" class="on" aria-current="page">.*?<span>To-dos</span>'
                     r'<span class="count">2</span>', page, re.S)


def test_to_do_pages_light_the_to_dos_tab(client, clock):
    import teamhub_nav
    maya = make_leader(client)
    login_leader(client)
    for url in ("/shift/todo", f"/shift/todo/{maya}"):
        page = client.get(url).data.decode()
        assert re.search(r'<a href="/shift/" class="on" aria-current="page">', page), url
    assert teamhub_nav.section_for("shift.todo_leader", "operator") == "today"


def test_menu_badges_for_guests_and_repairs(client, clock):
    make_leader(client)
    login_leader(client)
    shift_db.add_recovery("Linda R.", "519-555-0142", "", "order-error", "free-entree-card",
                          "Missing nuggets", follow_up=True, by="Maya")
    shift_db.add_equipment_issue("Fryer 3", "kitchen", "down", False, "", "Maya")
    _, menu = menu_words(client)
    assert re.search(r"Guest recovery.{0,200}1 open", menu, re.S)
    assert re.search(r"Equipment &amp; repairs.{0,200}1 to call in", menu, re.S)
    page = today_page(client)
    assert "Linda R." not in page and "Fryer 3" not in page     # never on the home screen


def test_home_without_the_hub(client, clock, monkeypatch):
    import teamhub_nav
    make_leader(client)
    monkeypatch.setattr(teamhub_nav, "hub_up", lambda: False)
    login_leader(client)
    page = today_page(client)
    assert tabs(page) == ["To-dos", "Checklists", "Meetings", "Leadership"]


def test_announcement_page_link_opens_the_hub_page(client, clock):
    make_leader(client)
    login_operator(client)
    category = next(c["id"] for c in hub_db.categories() if c["name"] == "FOH Operations")
    op = hub_db.Viewer("operator", None, "Operator", "admin")
    pid = hub_db.create_page(op, {"category_id": category, "section": "Training",
                                  "title": "Building the Pretzel Sandwich", "summary": "",
                                  "keywords": "", "body": "# Build", "leaders_only": 0,
                                  "required": 1, "annual_review": 0})
    assert hub_db.publish(op, pid, hub_db.get_page(pid)["rev"], "First", True) is None
    client.post("/shift/announcements", data={
        "body": f"Pretzel Sandwich launches Monday. Read https://x.onrender.com/hub/page/{pid}."})
    client.post("/shift/logout")
    login_leader(client)
    page = client.get("/shift/announcements").data.decode()
    assert "Pretzel Sandwich launches Monday. Read." in page and "onrender" not in page
    assert f'href="/hub/page/{pid}">Open&nbsp;the&nbsp;page' in page
