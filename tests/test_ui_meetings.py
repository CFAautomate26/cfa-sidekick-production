"""Meetings (1:1s and to-dos) in the Team Hub look: the leader's page, the
Operator's index, an agenda, a team-member thread and the to-do pages —
plus the read-only shift_db.tasks_assigned_by behind "You assigned".

The 1:1 privacy rules are tested in test_shift_app.py and test_roles.py;
these tests check the new screens keep them (no other leader's agenda,
thread or assignments ever shows up) and that every form still posts the
same fields to the same routes."""

import re

import pytest

import app as cfa_app
import shift_app
import shift_db

NBSP = " "


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "meetings.db"))
    shift_db.init_db()
    shift_app._pin_fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    cfa_app.app.config["TESTING"] = True
    with cfa_app.app.test_client() as c:
        yield c


def login(client, name="Operator", pin="9999"):
    client.post("/shift/logout")
    return client.post("/shift/login", data={"name": name, "pin": pin})


@pytest.fixture()
def store(client):
    """Maya (lead), Devon (lead), Dana (admin), two team members."""
    shift_db.add_leader("Maya", "4721", "lead")
    shift_db.add_leader("Devon", "8888", "lead")
    shift_db.add_leader("Dana", "5555", "admin")
    ids = {l["name"]: l["id"] for l in shift_db.leaders()}
    shift_db.add_member("Avery")
    shift_db.add_member("Blake")
    for m in shift_db.roster():
        ids[m["name"]] = m["id"]
    return ids


def section(page, anchor_id, next_id=None):
    start = page.index(f'id="{anchor_id}"')
    return page[start:page.index(f'id="{next_id}"', start)] if next_id else page[start:]


# --- shift_db.tasks_assigned_by ------------------------------------------------

def test_tasks_assigned_by_open_then_recently_done(store):
    maya, devon, dana = store["Maya"], store["Devon"], store["Dana"]
    shift_db.add_task(devon, "Old open", "", "", "Maya")
    shift_db.add_task(dana, "New open", "", "", "Maya")
    shift_db.add_task(devon, "Done lately", "", "", "Maya")
    shift_db.add_task(devon, "Done long ago", "", "", "Maya")
    shift_db.add_task(devon, "Someone else's", "", "", "Dana")
    shift_db.add_task(maya, "Her own", "", "", "Maya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("UPDATE leader_tasks SET created_at='2026-01-01 09:00' WHERE title='Old open'")
        conn.execute("UPDATE leader_tasks SET created_at='2026-01-02 09:00' WHERE title='New open'")
        conn.execute("UPDATE leader_tasks SET completed_at=?, completed_by='Devon' "
                     "WHERE title='Done lately'", (shift_db.now_stamp(),))
        conn.execute("UPDATE leader_tasks SET completed_at='2020-01-01 09:00', "
                     "completed_by='Devon' WHERE title='Done long ago'")
    rows = shift_db.tasks_assigned_by("Maya")
    assert [t["title"] for t in rows] == ["Her own", "New open", "Old open", "Done lately"]
    assert rows[1]["leader_name"] == "Dana" and rows[2]["leader_name"] == "Devon"
    assert shift_db.tasks_assigned_by("  ") == []
    # A leader who has left drops off the list
    shift_db.set_leader_active(dana, False)
    assert "New open" not in [t["title"] for t in shift_db.tasks_assigned_by("Maya")]


def test_tasks_assigned_by_flags_overdue(store):
    shift_db.add_task(store["Devon"], "Late", "", "2020-01-01", "Maya")
    shift_db.add_task(store["Devon"], "Later", "", "2999-01-01", "Maya")
    flags = {t["title"]: t["overdue"] for t in shift_db.tasks_assigned_by("Maya")}
    assert flags == {"Late": True, "Later": False}


# --- date wording ------------------------------------------------------------------

def test_meetings_date_wording(monkeypatch):
    monkeypatch.setattr(shift_db, "today_local", lambda: "2026-10-23")   # a Friday
    day, due, when = shift_app._meetings_day, shift_app._meetings_due, shift_app._meetings_when
    assert day("2026-10-13") == f"Oct{NBSP}13"
    assert day("2026-10-26", weekday=True) == f"Mon, Oct{NBSP}26"
    assert day("2025-12-30") == f"Dec{NBSP}30, 2025"
    assert day("2026-10-13 02:30") == f"Oct{NBSP}12"      # before 4am = the night before
    assert day(None) == "" and day("garbage") == "garbage"
    assert due(None) == ("No due date", False)
    assert due("2026-10-23") == ("Due today", True)
    assert due("2026-10-24") == ("Due tomorrow", False)
    assert due("2026-10-26") == (f"Due Mon, Oct{NBSP}26", False)
    assert due("2026-10-20") == (f"Overdue · was due Oct{NBSP}20", True)
    assert when("2026-10-23 16:12") == "today 4:12 pm"
    assert when("2026-10-22 09:05") == "yesterday 9:05 am"
    assert when("2026-10-24 00:30") == "today 12:30 am"     # still the 23rd's shift
    assert when("2026-10-02 12:00") == f"Oct{NBSP}2"


# --- a leader's Meetings page --------------------------------------------------------

def test_leader_meetings_page(client, store):
    maya, devon = store["Maya"], store["Devon"]
    shift_db.add_oneonone_topic(maya, "Saturday lunch break coverage", "Maya")
    shift_db.add_oneonone_topic(maya, "Ready to start Leading Others?", "Operator")
    shift_db.add_oneonone_topic(devon, "DEVON-AGENDA", "Devon")
    shift_db.add_member_topic(store["Avery"], "Wants to learn the grill", "Maya", holder_id=maya)
    shift_db.add_member_topic(store["Blake"], "DEVON-THREAD", "Devon", holder_id=devon)
    shift_db.add_member_topic(store["Blake"], "OPERATOR-THREAD", "Operator")
    shift_db.add_task(maya, "Order DT headset batteries", "", shift_db.today_local(), "Maya")
    shift_db.add_task(devon, "Update the BOH prep chart", "", "", "Maya")
    shift_db.add_task(devon, "DEVON-FROM-DANA", "", "", "Dana")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("INSERT INTO goals (title, period, status, created_at, created_by, leader_id) "
                     "VALUES ('Solo breakfast open', 'monthly', 'active', '2026-01-01 09:00', "
                     "'Operator', ?)", (maya,))
        lesson = conn.execute("SELECT id FROM course_lessons ORDER BY sort LIMIT 1").fetchone()[0]
    shift_db.set_lesson_done(lesson, maya, "", "Maya")

    login(client, "Maya", "4721")
    page = client.get("/shift/oneonone").data.decode()
    assert '<p class="eyebrow">Meetings</p>' in page
    assert "Your 1:1s &amp; to-dos." in page
    rail = page[page.index('class="seg fit"'):page.index("</nav>", page.index('class="seg fit"'))]
    assert 'href="#mine">My 1:1 <span class="count">2</span>' in rail
    assert 'href="#team">Team 1:1s <span class="count">1</span>' in rail
    assert 'href="#todos">To-dos <span class="count">1</span>' in rail

    mine = section(page, "mine", "team")
    assert "Only you and the Operator see this agenda" in mine
    assert "Your agenda with the Operator" in mine
    total = shift_db.course_lesson_count()
    assert f"<b>1<small>/{total}</small></b><span>Course lessons</span>" in mine
    assert "<b>1</b><span>Open to-dos</span>" in mine
    assert "<b>1</b><span>Your goals</span>" in mine
    assert "added by you" in mine and "added by the Operator" in mine
    # The full agenda (history, goals) is one tap away
    assert f'href="/shift/oneonone/{maya}"' in mine

    team = section(page, "team", "todos")
    assert "Only you and the Operator see these, not other leaders or admins." in team
    assert "Avery" in team and "1 to talk about" in team

    todos = section(page, "todos")
    yours = todos[todos.index("Yours"):todos.index("You assigned")]
    assert "Order DT headset batteries" in yours and "Due today" in yours
    assigned = todos[todos.index("You assigned"):]
    assert "Update the BOH prep chart" in assigned and "Devon" in assigned
    assert "Order DT headset batteries" not in assigned          # her own: under Yours
    assert "Keep 1:1 notes about the work." in page
    assert "go to the Operator directly, never into Team Hub" in page

    # Never anyone else's agenda, threads or assignments
    for secret in ["DEVON-AGENDA", "DEVON-THREAD", "OPERATOR-THREAD", "DEVON-FROM-DANA"]:
        assert secret not in page, secret
    assert f'href="/shift/oneonone/{devon}"' not in page


def test_agenda_rows_post_the_existing_forms(client, store):
    maya = store["Maya"]
    shift_db.add_oneonone_topic(maya, "Kayla as a DT trainer", "Maya")
    shift_db.add_oneonone_topic(maya, "From the Operator", "Operator")
    mine_t, op_t = (t["id"] for t in shift_db.oneonone_for_leader(maya)[0])
    login(client, "Maya", "4721")
    page = client.get("/shift/oneonone").data.decode()
    row = page[page.index(f'id="topic-{mine_t}"'):page.index(f'id="topic-{op_t}"')]
    toggle = f'action="/shift/oneonone/topic/{mine_t}/toggle"'
    assert row.count(toggle) == 2            # the tick, and "what we decided"
    assert 'name="discussed" value="1"' in row and 'name="note"' in row
    assert f'action="/shift/oneonone/topic/{mine_t}/delete"' in row
    assert f'action="/shift/oneonone/{maya}/action"' in row
    assert '<input type="hidden" name="title" value="Kayla as a DT trainer">' in row
    assert "To-do +" in row
    assert 'name="back" value="meetings"' in row
    # Only whoever added a topic (or the Operator) gets Remove
    op_row = page[page.index(f'id="topic-{op_t}"'):]
    assert f'action="/shift/oneonone/topic/{op_t}/delete"' not in op_row
    add = page[page.index('class="add"'):]
    assert f'action="/shift/oneonone/{maya}/topics"' in add and 'name="topic"' in add


def test_meetings_forms_land_back_on_meetings(client, store):
    maya = store["Maya"]
    shift_db.add_oneonone_topic(maya, "Who reorders DT headset batteries?", "Maya")
    tid = shift_db.oneonone_for_leader(maya)[0][0]["id"]
    login(client, "Maya", "4721")
    # To-do + makes a to-do from the topic, then back to the To-dos block
    resp = client.post(f"/shift/oneonone/{maya}/action",
                       data={"title": "Who reorders DT headset batteries?", "back": "meetings"})
    assert resp.headers["Location"].endswith("/shift/oneonone#todos")
    task = shift_db.tasks_for_leader(maya)[0][0]
    assert task["title"] == "Who reorders DT headset batteries?" and task["assigned_by"] == "Maya"
    assert shift_db.get_topic(tid)["discussed_at"] is None    # the topic stays on the agenda
    resp = client.post(f"/shift/oneonone/{maya}/topics",
                       data={"topic": "Break plan", "back": "meetings"})
    assert resp.headers["Location"].endswith("/shift/oneonone#mine")
    resp = client.post(f"/shift/oneonone/topic/{tid}/toggle",
                       data={"discussed": "1", "back": "meetings"})
    assert resp.headers["Location"].endswith("/shift/oneonone#mine")
    assert shift_db.get_topic(tid)["discussed_at"]
    resp = client.post(f"/shift/todo/task/{task['id']}/toggle",
                       data={"done": "1", "back": "meetings"})
    assert resp.headers["Location"].endswith("/shift/oneonone#todos")
    assert shift_db.get_task(task["id"])["completed_by"] == "Maya"
    # Without it (or with anything else) each route keeps its own page
    resp = client.post(f"/shift/oneonone/{maya}/topics",
                       data={"topic": "Another", "back": "https://evil.example/"})
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{maya}#agenda")
    resp = client.post(f"/shift/oneonone/{maya}/action", data={"title": "Plain"})
    assert resp.headers["Location"].endswith(f"/shift/oneonone/{maya}#actions")


def test_back_never_opens_another_leaders_agenda(client, store):
    """back=meetings only changes where an allowed action lands; a refused
    one is still refused."""
    devon = store["Devon"]
    shift_db.add_oneonone_topic(devon, "DEVON-ONLY", "Devon")
    tid = shift_db.oneonone_for_leader(devon)[0][0]["id"]
    login(client, "Maya", "4721")
    for url, data in [(f"/shift/oneonone/{devon}/topics", {"topic": "SNEAKY"}),
                      (f"/shift/oneonone/{devon}/action", {"title": "SNEAKY"}),
                      (f"/shift/oneonone/topic/{tid}/toggle", {"discussed": "1"}),
                      (f"/shift/oneonone/topic/{tid}/delete", {})]:
        resp = client.post(url, data={**data, "back": "meetings"})
        assert resp.headers["Location"].endswith("/shift/"), url
    assert [t["topic"] for t in shift_db.oneonone_for_leader(devon)[0]] == ["DEVON-ONLY"]
    assert shift_db.tasks_for_leader(devon) == ([], [])


def test_admin_leader_meetings_page_is_their_own_only(client, store):
    shift_db.add_oneonone_topic(store["Maya"], "MAYA-AGENDA", "Maya")
    shift_db.add_member_topic(store["Avery"], "MAYA-THREAD", "Maya", holder_id=store["Maya"])
    login(client, "Dana", "5555")
    page = client.get("/shift/oneonone").data.decode()
    assert "Your agenda with the Operator" in page
    assert "MAYA-AGENDA" not in page and "MAYA-THREAD" not in page
    assert "Your 1:1s with leaders" not in page and 'id="leader-team"' not in page


# --- the Operator's index ----------------------------------------------------------

def test_operator_meetings_index(client, store):
    maya, devon = store["Maya"], store["Devon"]
    shift_db.add_oneonone_topic(devon, "One", "Operator")
    shift_db.add_oneonone_topic(devon, "Two", "Operator")
    shift_db.add_member_topic(store["Avery"], "Operator's own", "Operator")
    shift_db.add_member_topic(store["Blake"], "Maya's thread", "Maya", holder_id=maya)
    shift_db.add_task(maya, "From the Operator", "", "", "Operator")
    login(client)
    page = client.get("/shift/oneonone").data.decode()
    rail = page[page.index('class="seg fit"'):page.index("</nav>", page.index('class="seg fit"'))]
    assert 'href="#leaders">Leaders <span class="count">2</span>' in rail
    assert 'href="#team">Team 1:1s <span class="count">1</span>' in rail
    assert 'href="#todos">To-dos <span class="count">1</span>' in rail

    leaders = section(page, "leaders", "team")
    assert "Your 1:1s with leaders" in leaders
    # Agendas with something waiting come first, then A–Z
    order = [m for m in re.findall(r'href="/shift/oneonone/(\d+)"', leaders)]
    assert order == [str(devon), str(store["Dana"]), str(maya)]
    assert "2 to talk about" in leaders

    team = section(page, "team", "leader-team")
    assert "Only you can see these." in team
    assert "Avery" in team                                    # names, not topics:
    assert "Operator&#39;s own" not in page and "Maya&#39;s thread" not in page
    overview = section(page, "leader-team", "todos")
    assert "Blake" in overview and "with Maya" in overview
    assert f"/shift/oneonone/member/{store['Blake']}/by/{maya}" in overview

    todos = section(page, "todos")
    assert "You assigned" in todos and "From the Operator" in todos and "Maya" in todos
    assert "Every leader's to-dos" in todos
    # The Operator's own version of the rule note
    assert "never go in this app" in page
    assert "go to the Operator directly" not in page


# --- an agenda page and a team-member thread -----------------------------------------

def test_agenda_page_for_leader_and_operator(client, store):
    maya = store["Maya"]
    shift_db.add_oneonone_topic(maya, "Closing timing", "Maya")
    tid = shift_db.oneonone_for_leader(maya)[0][0]["id"]
    shift_db.set_topic_discussed(tid, True, "Start the lobby sweep at 9:30", "Operator")
    shift_db.add_oneonone_topic(maya, "Saturday coverage", "Maya")
    login(client, "Maya", "4721")
    page = client.get(f"/shift/oneonone/{maya}").data.decode()
    assert "Your 1:1 with the Operator" in page
    assert "Only you and the Operator see this agenda" in page
    assert "Saturday coverage" in page and "Past 1:1s" in page
    assert "Start the lobby sweep at 9:30" in page
    assert f'action="/shift/oneonone/{maya}/action"' in page and "To-do +" in page
    assert 'id="agenda"' in page and 'id="actions"' in page and 'id="goals"' in page
    assert 'name="back"' not in page             # its forms keep their own redirects
    login(client)
    page = client.get(f"/shift/oneonone/{maya}").data.decode()
    assert "1:1 with Maya" in page and "Only Maya and you see this agenda" in page
    assert "Maya&#39;s goals" in page


def test_member_thread_page(client, store):
    maya, avery = store["Maya"], store["Avery"]
    login(client, "Maya", "4721")
    client.post(f"/shift/oneonone/member/{avery}/topics", data={"topic": "Wants the grill"})
    page = client.get(f"/shift/oneonone/member/{avery}").data.decode()
    assert "1:1 with Avery" in page
    assert "Only you and the Operator see this 1:1, not other leaders or admins" in page
    assert "Wants the grill" in page and "added by you" in page
    assert f'action="/shift/oneonone/member/{avery}/topics"' in page
    assert '/action"' not in page and '>To-do +<' not in page    # team members have no to-dos
    tid = shift_db.oneonone_for_member(avery, maya)[0][0]["id"]
    assert f'action="/shift/oneonone/member/topic/{tid}/toggle"' in page
    assert f'action="/shift/oneonone/member/topic/{tid}/delete"' in page
    assert "Keep 1:1 notes about the work." in page
    login(client)
    page = client.get(f"/shift/oneonone/member/{avery}/by/{maya}").data.decode()
    assert "held by Maya" in page and "Maya's 1:1 with Avery: only Maya and you see it" in page
    page = client.get(f"/shift/oneonone/member/{avery}").data.decode()
    assert "Only you (the Operator) can see this 1:1" in page
    assert "Wants the grill" not in page                       # Maya's, not the Operator's


def test_member_thread_with_a_leader_login_has_no_add_form(client, store):
    shift_db.add_member("Maya")
    mid = next(m["id"] for m in shift_db.roster() if m["name"] == "Maya")
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("INSERT INTO oneonone_member_topics (member_id, topic, added_by, "
                     "created_at) VALUES (?,?,?,?)", (mid, "From before", "Operator",
                                                      "2026-01-01 10:00"))
    login(client)
    page = client.get(f"/shift/oneonone/member/{mid}").data.decode()
    assert "From before" in page and "has a leader login" in page
    assert f'action="/shift/oneonone/member/{mid}/topics"' not in page


# --- to-do pages -------------------------------------------------------------------

def test_todo_pages(client, store):
    maya, devon = store["Maya"], store["Devon"]
    shift_db.add_task(devon, "Late one", "Before Friday", "2020-01-01", "Dana")
    login(client, "Maya", "4721")
    index = client.get("/shift/todo").data.decode()
    assert "Leader to-dos" in index and "Maya (you)" in index and "1 overdue" in index
    page = client.get(f"/shift/todo/{devon}").data.decode()
    assert "Devon&#39;s to-dos" in page             # the name is escaped with its 's
    assert "Late one" in page and "Before Friday" in page and "Overdue" in page
    assert f'action="/shift/todo/{devon}/assign"' in page
    for field in ('name="title"', 'name="details"', 'name="due_date"'):
        assert field in page
    # A lead can't check off someone else's to-do, so no tick or delete
    assert "/toggle" not in page and "/delete" not in page
    page = client.get(f"/shift/todo/{maya}").data.decode()
    assert "Your to-dos" in page
    login(client, "Devon", "8888")
    page = client.get(f"/shift/todo/{devon}").data.decode()
    task = shift_db.tasks_for_leader(devon)[0][0]
    assert f'action="/shift/todo/task/{task["id"]}/toggle"' in page
    assert "/delete" not in page                       # delete stays admin-only
    login(client)
    page = client.get(f"/shift/todo/{devon}").data.decode()
    assert f'action="/shift/todo/task/{task["id"]}/delete"' in page


def test_meetings_pages_use_the_compact_hero(client, store):
    shift_db.add_member_topic(store["Avery"], "x", "Operator")
    login(client)
    for url in ["/shift/oneonone", f"/shift/oneonone/{store['Maya']}",
                f"/shift/oneonone/member/{store['Avery']}", "/shift/todo",
                f"/shift/todo/{store['Maya']}"]:
        page = client.get(url).data.decode()
        assert '<section class="hero compact">' in page, url
        assert page.count('<h1 class="headline">') == 1, url
        assert 'class="page"' not in page, url        # no legacy headings left
