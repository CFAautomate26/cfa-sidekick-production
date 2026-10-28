"""Team Hub Today (/shift/): the pure Due-now builder (_due_now, which the
Checklists tile counts from) and the calm page a leader lands on — a
greeting, today's two goals (leadership + operational), their to-dos, and
the section tiles."""

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


def tick_all(run_name, day, critical_only=False):
    run = next(r for r in shift_db.runs_for_date(day) if r["name"] == run_name)
    _, items = shift_db.run_with_items(run["id"])
    for i in items:
        if i["critical"] or not critical_only:
            shift_db.set_item_done(i["id"], True, "Maya")


# --- _due_now: a pure function over runs_for_date's rows -------------------

def run(rid, name, daypart="lunch", frequency="daily", done=0, total=4, crit=0,
        owner=None, due_weekday=None, period_end=FRI, scheduled=True):
    return {"id": rid, "name": name, "daypart": daypart, "frequency": frequency,
            "done": done, "total": total, "critical_remaining": crit, "owner": owner,
            "due_weekday": due_weekday, "period_end": period_end, "scheduled": scheduled}


def titles(rows):
    return [r["title"] for r in rows]


def test_due_now_is_pure(monkeypatch):
    def no_db():
        raise AssertionError("_due_now must not query")
    monkeypatch.setattr(shift_db, "connect", no_db)
    rows = shift_app._due_now([run(1, "Transition", crit=1)], "lunch", FRI)
    assert titles(rows) == ["Transition"]


def test_due_now_current_daypart_lists_until_finished():
    runs = [run(1, "Transition", done=7, total=9, owner="Priya"),
            run(2, "Lunch done", done=4, total=4),
            run(3, "Empty list", done=0, total=0),
            run(4, "Retired", scheduled=False),
            run(5, "Afternoon Reset", daypart="afternoon", crit=1),   # later: waits
            run(6, "FOH Closing", daypart="dinner", crit=3)]
    rows = shift_app._due_now(runs, "lunch", FRI)
    assert titles(rows) == ["Transition"]
    row = rows[0]
    assert (row["kind"], row["run_id"], row["done"], row["total"], row["left"]) == \
        ("run", 1, 7, 9, 2)
    assert row["meta"] == "7 of 9 · owner Priya" and row["tags"] == []


def test_due_now_earlier_lists_only_while_critical_items_are_left():
    runs = [run(1, "BOH Opening", daypart="breakfast", done=9, total=10, crit=1),
            run(2, "FOH Opening", daypart="breakfast", done=3, total=10),  # no critical left
            run(3, "Transition", daypart="lunch", done=9, total=9),
            run(4, "Afternoon Reset", daypart="afternoon", done=1, total=7, crit=1)]
    lunch = shift_app._due_now(runs, "lunch", FRI)
    assert titles(lunch) == ["BOH Opening"]
    assert lunch[0]["tags"] == [("From breakfast", "b-tan")] and lunch[0]["critical"] == 1
    assert lunch[0]["meta"] == "" and lunch[0]["left"] == 1     # the ring says 1 left
    dinner = shift_app._due_now(runs, "dinner", FRI)
    assert titles(dinner) == ["BOH Opening", "Afternoon Reset"]
    assert dinner[1]["tags"] == [("From the afternoon", "b-tan")]
    assert shift_app._due_now(runs, "breakfast", FRI)[0]["tags"] == []   # its own daypart


def test_due_now_station_sheets_fold_into_one_row():
    runs = [run(1, "Boards — Daily", daypart="allday", done=11, total=15, crit=2),
            run(2, "Dining Room — Daily", daypart="allday", done=10, total=17),
            run(3, "Drive Thru — Daily", daypart="allday", done=17, total=17),
            run(4, "Machines — Daily", daypart="allday", done=3, total=9)]
    rows = shift_app._due_now(runs, "lunch", FRI)
    assert titles(rows) == ["Boards — Daily", "Station cleaning sheets"]
    boards, sheets = rows
    assert boards["meta"] == "All day · 11 of 15" and boards["critical"] == 2
    assert sheets["kind"] == "cleaning" and sheets["run_id"] is None
    assert (sheets["count"], sheets["done"], sheets["total"], sheets["left"]) == (3, 30, 43, 13)
    assert sheets["meta"] == "3 all-day lists · 30 of 43 done"
    # Every sheet done (critical ones too): nothing left to fold
    finished = [run(2, "Dining Room — Daily", daypart="allday", done=17, total=17)]
    assert shift_app._due_now(finished, "lunch", FRI) == []


def test_due_now_weekly_and_monthly_when_due():
    runs = [run(1, "Drive Thru — Weekly", frequency="weekly", done=3, total=8, due_weekday=4),
            run(2, "Boards — Weekly", frequency="weekly", total=10, due_weekday=2),
            run(3, "Machines — Weekly", frequency="weekly", total=6),           # Saturday
            run(4, "Produce — Weekly", frequency="weekly", done=10, total=10, due_weekday=0),
            run(5, "Patio — Weekly", frequency="weekly", total=3, due_weekday=0,
                scheduled=False),
            run(6, "Machines — Monthly", frequency="monthly", total=7,
                period_end="2026-10-31")]
    rows = shift_app._due_now(runs, "lunch", FRI)
    assert titles(rows) == ["Drive Thru — Weekly", "Boards — Weekly"]
    assert rows[0]["tags"] == [("Due today", "b-tan")] and rows[0]["meta"] == "3 of 8"
    assert rows[1]["tags"] == [("Overdue · was due Wednesday", "b-red")]
    late_month = shift_app._due_now(runs, "lunch", "2026-10-26")          # Monday
    monthly = next(r for r in late_month if r["title"] == "Machines — Monthly")
    assert monthly["tags"] == [("Due by Oct 31", "b-tan")]


def test_due_now_critical_first_order_kept_within():
    runs = [run(1, "Transition", done=1, total=9),
            run(2, "Sanitizer", crit=2),
            run(3, "BOH Opening", daypart="breakfast", crit=1),
            run(4, "Breading — Daily", daypart="allday", crit=1),
            run(5, "Dining Room — Daily", daypart="allday", done=2, total=17),
            run(6, "Drive Thru — Weekly", frequency="weekly", due_weekday=4),
            run(7, "Machines — Monthly", frequency="monthly", crit=1, period_end=FRI)]
    rows = shift_app._due_now(runs, "lunch", FRI)
    assert titles(rows) == ["Sanitizer", "BOH Opening", "Breading — Daily",
                            "Machines — Monthly",
                            "Transition", "Station cleaning sheets", "Drive Thru — Weekly"]


# --- dayparts and the 4am business-day rollover (the real clock path) ------

def due_now_at(clock, stamp):
    clock["now"] = at(stamp)
    day = shift_db.today_local()
    shift_db.ensure_runs_for_date(day)
    return day, shift_db.current_daypart(), shift_app._due_now(
        shift_db.runs_for_date(day), shift_db.current_daypart(), day)


def test_due_now_breakfast_to_lunch_at_1030(client, clock):
    day, daypart, rows = due_now_at(clock, "2026-10-23 10:29")
    assert (day, daypart) == (FRI, "breakfast")
    names = titles(rows)
    assert "FOH Opening" in names and "BOH Opening" in names
    assert "Breakfast → Lunch Transition" not in names
    assert all(not r["tags"] for r in rows if r["title"] == "BOH Opening")
    tick_all("FOH Opening", FRI, critical_only=True)         # unfinished, nothing critical
    day, daypart, rows = due_now_at(clock, "2026-10-23 10:30")
    assert daypart == "lunch"
    names = titles(rows)
    assert "Breakfast → Lunch Transition" in names
    assert "FOH Opening" not in names                         # leftover without critical
    boh = next(r for r in rows if r["title"] == "BOH Opening")
    assert ("From breakfast", "b-tan") in boh["tags"]


def test_due_now_lunch_to_afternoon_at_1400(client, clock):
    _, daypart, rows = due_now_at(clock, "2026-10-23 13:59")
    assert daypart == "lunch"
    assert "Afternoon Reset" not in titles(rows)
    _, daypart, rows = due_now_at(clock, "2026-10-23 14:00")
    assert daypart == "afternoon"
    assert "Afternoon Reset" in titles(rows)
    transition = next(r for r in rows if r["title"] == "Breakfast → Lunch Transition")
    assert ("From lunch", "b-tan") in transition["tags"]


def test_today_rolls_over_at_4am_not_midnight(client, clock):
    login_operator(client)
    clock["now"] = at("2026-10-24 03:59")                     # Friday's close, after midnight
    assert "Dinner is in session · Fri, Oct 23 · 3:59" in today_page(client)
    client.post("/shift/today/goals", data={"leadership": "Thank the closers", "operational": "",
                                            "area": ""})
    assert shift_db.daily_goals(None, FRI)["leadership"]["body"] == "Thank the closers"
    clock["now"] = at("2026-10-24 04:00")
    page = today_page(client)
    assert "Breakfast is in session · Sat, Oct 24 · 4:00" in page
    assert "Thank the closers" not in page                    # a new day, a new set
    assert "Set today&#39;s goals" in page or "Set today's goals" in page


# --- the page ---------------------------------------------------------------

def goals_section(page):
    start = page.index('id="goals"')
    return page[start:page.index("</section>", start)]


def test_today_is_calm_greeting_goals_todos_tiles(client, clock):
    make_leader(client)
    login_operator(client)
    client.post("/shift/announcements", data={"body": "Pretzel launch Monday"})
    shift_db.add_recovery("Linda R.", "519-555-0142", "", "order-error", "free-entree-card",
                          "Missing 8-ct nuggets", follow_up=True, by="Daniel")
    client.post("/shift/logout")
    login_leader(client)
    page = today_page(client)
    assert "<title>Team Hub</title>" in page
    assert "Lunch is in session · Fri, Oct 23 · 12:07" in page
    assert "Good afternoon, Maya." in page
    # No triage on the home screen: no counts of what "needs you", no
    # checklist rows, guests, lineup grid or shift log.
    for gone in ("need you", "needs you", "Due now", "Linda R.", "lineup-head", "log-head",
                 'aria-label="Announcement"', 'class="ring"'):
        assert gone not in page, gone
    # ...just one quiet link to the unread announcement.
    assert '<a class="textlink" href="/shift/announcements">1 new announcement</a>' in page
    assert 'id="goals"' in page and 'id="tiles-head"' in page


def test_greeting_follows_the_clock(client, clock):
    login_operator(client)
    for stamp, word in (("2026-10-23 07:15", "Good morning."), ("2026-10-23 12:00", "Good afternoon."),
                        ("2026-10-23 17:30", "Good evening."), ("2026-10-24 01:10", "Good evening.")):
        clock["now"] = at(stamp)
        assert word in today_page(client), stamp     # the Operator login has no name


def test_first_visit_asks_for_two_goals(client, clock):
    maya = make_leader(client)
    login_leader(client)
    goals = goals_section(today_page(client))
    assert 'action="/shift/today/goals"' in goals
    for field in ('name="leadership"', 'name="operational"', 'name="area"', 'list="g-areas"'):
        assert field in goals
    assert f'maxlength="{shift_db.DAILY_GOAL_MAX}"' in goals
    assert "Only you and the Operator see these." in goals
    assert "Drive-Thru" in goals                            # an area suggestion
    resp = client.post("/shift/today/goals", data={
        "leadership": "  Give one team member   specific praise ",
        "operational": "Lunch drive-thru under 4:30 a car", "area": " Drive-Thru "})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/#goals")
    saved = shift_db.daily_goals(maya, FRI)
    assert saved["leadership"]["body"] == "Give one team member specific praise"
    assert saved["operational"]["area"] == "Drive-Thru" and saved["leadership"]["area"] is None
    assert shift_db.get_leader(maya)["area"] == "Drive-Thru"      # remembered for next time
    goals = goals_section(today_page(client))
    assert "Give one team member specific praise" in goals
    assert "Operational · Drive-Thru" in goals and 'name="leadership"' not in goals
    assert 'href="/shift/?edit=goals#goals">Edit</a>' in goals


def test_tick_a_goal_off_and_back(client, clock):
    maya = make_leader(client)
    login_leader(client)
    client.post("/shift/today/goals", data={"leadership": "Coach Kayla on greetings",
                                            "operational": "", "area": ""})
    goal = shift_db.daily_goals(maya, FRI)["leadership"]
    goals = goals_section(today_page(client))
    assert "Add an operational goal" in goals                 # one set, one still open
    assert f'action="/shift/today/goals/{goal["id"]}/done"' in goals
    assert 'aria-label="Mark done: Coach Kayla on greetings"' in goals
    client.post(f"/shift/today/goals/{goal['id']}/done", data={"done": "1"})
    goals = goals_section(today_page(client))
    assert ">Done</span>" in goals and 'class="tick on"' in goals
    assert 'aria-label="Mark not done: Coach Kayla on greetings"' in goals
    client.post(f"/shift/today/goals/{goal['id']}/done", data={"done": "0"})
    assert shift_db.daily_goals(maya, FRI)["leadership"]["done_at"] is None


def test_changing_a_goal_clears_its_tick_and_blank_clears_it(client, clock):
    maya = make_leader(client)
    login_leader(client)
    client.post("/shift/today/goals", data={"leadership": "A", "operational": "B", "area": "BOH"})
    lead = shift_db.daily_goals(maya, FRI)["leadership"]
    shift_db.set_daily_goal_done(lead["id"], maya, True)
    client.post("/shift/today/goals", data={"leadership": "A", "operational": "B2", "area": "BOH"})
    now = shift_db.daily_goals(maya, FRI)
    assert now["leadership"]["done_at"] is not None           # same words: still done
    client.post("/shift/today/goals", data={"leadership": "A, but better", "operational": "B2",
                                            "area": "BOH"})
    assert shift_db.daily_goals(maya, FRI)["leadership"]["done_at"] is None
    client.post("/shift/today/goals", data={"leadership": "", "operational": "B2", "area": "BOH"})
    assert set(shift_db.daily_goals(maya, FRI)) == {"operational"}
    page = today_page(client)
    assert "Add a leadership goal" in page and 'name="leadership"' not in page


def test_edit_mode_prefills_and_cancels(client, clock):
    make_leader(client)
    login_leader(client)
    client.post("/shift/today/goals", data={"leadership": "Lead", "operational": "Ops",
                                            "area": "Hospitality"})
    resp = client.get("/shift/?edit=goals")
    goals = goals_section(resp.data.decode())
    assert ">Lead</textarea>" in goals and ">Ops</textarea>" in goals
    assert 'value="Hospitality"' in goals and "Save goals" in goals
    assert 'href="/shift/#goals">Cancel</a>' in goals


def test_goals_are_private_to_their_leader_and_the_operator(client, clock):
    maya = make_leader(client)
    make_leader(client, "Daniel", "1111", role="admin")
    login_leader(client)
    client.post("/shift/today/goals", data={"leadership": "Maya's private growth goal",
                                            "operational": "Ops", "area": "Drive-Thru"})
    goal = shift_db.daily_goals(maya, FRI)["leadership"]
    client.post("/shift/logout")
    login_leader(client, "Daniel", "1111")                  # an admin-role leader
    assert "private growth goal" not in today_page(client)
    goals_page = client.get("/shift/goals").data.decode()
    assert "private growth goal" not in goals_page and "Your leaders today" not in goals_page
    client.post(f"/shift/today/goals/{goal['id']}/done", data={"done": "1"})
    assert shift_db.daily_goals(maya, FRI)["leadership"]["done_at"] is None
    client.post("/shift/logout")
    login_operator(client)
    assert "private growth goal" not in today_page(client)   # the Operator's home stays theirs
    client.post(f"/shift/today/goals/{goal['id']}/done", data={"done": "1"})
    assert shift_db.daily_goals(maya, FRI)["leadership"]["done_at"] is None   # only its holder
    goals_page = client.get("/shift/goals").data.decode()
    daily = goals_page[goals_page.index('id="daily"'):goals_page.index("Store goals")]
    assert "Your leaders today" in daily and "private growth goal" in daily
    assert "Drive-Thru" in daily and "Daniel" in daily and "Not set yet today" in daily


def test_operator_home_shows_team_line_and_handed_out_todos(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    shift_db.save_daily_goals(maya, FRI, "Lead", "Ops", "BOH")
    shift_db.add_task(maya, "Order DT headset batteries", "", FRI, "Operator")
    shift_db.add_task(daniel, "Fix the ice machine filter", "", "2026-10-20", "Operator")
    shift_db.add_task(daniel, "Daniel's own reminder", "", "", "Daniel")
    login_operator(client)
    page = today_page(client)
    assert "1 of 2 leaders set today&#39;s goals" in page or "1 of 2 leaders set today's goals" in page
    assert 'href="/shift/goals#daily"' in page
    todos = page[page.index('id="todo-head"'):]
    todos = todos[:todos.index("</section>")]
    assert "To-dos you handed out" in todos
    # soonest due first, with whose it is
    assert todos.index("Fix the ice machine filter") < todos.index("Order DT headset batteries")
    assert "Daniel · " in todos and "Overdue · was due Tue, Oct 20" in todos
    assert "Maya · " in todos and "Due today" in todos
    assert "own reminder" not in todos                      # not handed out by the Operator
    assert 'href="/shift/todo">All 2</a>' in todos


def test_leader_todos_tick_back_to_today(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    shift_db.add_task(maya, "Order DT headset batteries", "", FRI, "Operator")
    for n in range(6):
        shift_db.add_task(maya, f"Extra {n}", "", "", "Daniel")
    shift_db.add_task(daniel, "Daniel's own to-do", "", "", "Operator")
    login_leader(client)
    page = today_page(client)
    todos = page[page.index('id="todo-head"'):]
    todos = todos[:todos.index("</section>")]
    assert todos.count('class="row todo-row"') == 5            # top five
    assert "Your to-dos" in todos and f'href="/shift/todo/{maya}">All 7</a>' in todos
    assert "Order DT headset batteries" in todos and "Due today" in todos
    assert "from the Operator" in todos and "from Daniel" in todos
    assert "Daniel&#39;s own to-do" not in page and "Daniel's own to-do" not in page
    task = shift_db.tasks_for_leader(maya)[0][0]
    assert 'name="back" value="today"' in todos
    resp = client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1", "back": "today"})
    assert resp.headers["Location"].endswith("/shift/")
    assert "Order DT headset batteries" not in today_page(client)


def test_no_todos_no_todo_section(client, clock):
    make_leader(client)
    login_leader(client)
    page = today_page(client)
    assert 'id="todo-head"' not in page and "team-head" not in page


def test_yesterday_line(client, clock):
    maya = make_leader(client)
    shift_db.save_daily_goals(maya, "2026-10-22", "Lead", "Ops", "BOH")
    lead = shift_db.daily_goals(maya, "2026-10-22")["leadership"]
    shift_db.set_daily_goal_done(lead["id"], maya, True)
    shift_db.save_daily_goals(maya, FRI, "Today lead", "Today ops", "BOH")
    login_leader(client)
    assert "Yesterday you hit <b>1 of 2</b>." in today_page(client)
    history = client.get("/shift/goals").data.decode()
    daily = history[history.index('id="daily"'):history.index("Store goals")]
    assert "Today lead" in daily and "Thu, Oct 22" in daily and "Lead" in daily


def test_tiles_link_every_section_and_count_quietly(client, clock):
    maya = make_leader(client)
    make_leader(client, "Daniel", "1111")
    shift_db.create_goal("OSAT 80%", "", "OSAT", "%", 80, "up", "monthly", "", "Operator")
    shift_db.create_goal("Catering accounts", "", "", "", None, "up", "monthly", "", "Operator")
    shift_db.create_goal("Maya's personal goal", "", "", "", None, "up", "monthly", "",
                         "Operator", leader_id=maya)
    login_leader(client, "Daniel", "1111")
    tiles = section(today_page(client), "tiles-head")
    for href in ("/hub/library", "/shift/checklists", "/shift/lineup", "/shift/goals",
                 "/shift/oneonone", "/shift/development"):
        assert f'href="{href}"' in tiles
    assert "2 active" in tiles                                  # Daniel can't see Maya's
    assert "due now" in tiles and "critical" not in tiles       # a count, not an alarm
    assert "0 of " in tiles and "lessons" in tiles
    client.post("/shift/logout")
    login_leader(client)
    assert "3 active" in section(today_page(client), "tiles-head")  # her own counts


def todos_tile(client):
    page = today_page(client)
    tile = re.search(r'<a class="tile" href="[^"]*"><span class="tile-art"><svg[^>]*>'
                     r'<circle cx="17" cy="18".*?</a>', page, re.S)
    assert tile, "no To-dos tile"
    return tile.group(0)


def menu_word(client, label):
    """The ☰ menu's big word for a section, with its count and current flag."""
    page = client.get("/shift/more").data.decode()
    m = re.search(r'<a href="[^"]*"[^>]*><span class="w">' + label + r'.*?</a>', page, re.S)
    assert m, label
    return m.group(0)


def test_todos_tile_and_menu_word(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    login_leader(client)
    tile = todos_tile(client)
    assert f'href="/shift/todo/{maya}"' in tile and "To-dos" in tile
    assert "All caught up" in tile and 'class="count"' not in tile
    assert 'class="count"' not in menu_word(client, "To-dos")
    shift_db.add_task(maya, "Order headset batteries", "", "2026-10-22", "Operator")  # overdue
    shift_db.add_task(maya, "Pretzel training", "", "2026-10-23", "Daniel")          # due today
    shift_db.add_task(maya, "Clean the office", "", None, "Operator")
    shift_db.add_task(daniel, "Daniel's own", "", "2026-10-01", "Operator")           # not hers
    shift_db.add_task(maya, "Done already", "", None, "Operator")
    task = next(t for t in shift_db.tasks_for_leader(maya)[0] if t["title"] == "Done already")
    shift_db.set_task_done(task["id"], True, "Maya")
    tile = todos_tile(client)
    assert "3 open · 1 overdue" in tile                          # her own, open only
    assert '<span class="count" aria-hidden="true">1</span>' in tile   # red only when overdue
    word = menu_word(client, "To-dos")
    assert 'href="/shift/todo"' in word and '<span class="count">3</span>' in word
    client.post("/shift/logout")
    login_operator(client)                                       # the whole team's
    tile = todos_tile(client)
    assert 'href="/shift/todo"' in tile and "4 open · 2 overdue" in tile
    assert '<span class="count">4</span>' in menu_word(client, "To-dos")
    shift_db.set_leader_active(daniel, False)                    # a departed leader's
    assert "3 open · 1 overdue" in todos_tile(client)            # don't count
    for t in shift_db.tasks_for_leader(maya)[0]:
        shift_db.set_task_done(t["id"], True, "Operator")
    tile = todos_tile(client)
    assert "Assign one" in tile and 'class="count"' not in tile


def test_todo_pages_are_their_own_section(client, clock):
    maya = make_leader(client)
    login_leader(client)
    for url in ("/shift/todo", f"/shift/todo/{maya}"):
        page = client.get(url).data.decode()
        assert '<p class="eyebrow">To-dos</p>' in page, url
        assert "Meetings · To-dos" not in page and "← Meetings" not in page, url
    import teamhub_nav
    assert teamhub_nav.section_for("shift.todo", "leader") == "todos"
    assert teamhub_nav.section_for("shift.todo_leader", "operator") == "todos"
    page = client.get("/shift/todo").data.decode()            # not lit up as Meetings
    tab = re.search(r'<a href="/shift/oneonone"[^>]*>', page)
    assert tab and 'aria-current' not in tab.group(0)


def recovery_tile(client):
    page = today_page(client)
    tile = re.search(r'<a class="tile" href="/shift/recovery">.*?</a>', page, re.S)
    assert tile, "no Guest Recovery tile"
    return page, tile.group(0)


def test_guest_recovery_tile_counts_guests_still_to_reach(client, clock):
    make_leader(client)
    login_leader(client)
    _, tile = recovery_tile(client)
    assert "Guest Recovery" in tile and "Log a guest" in tile
    assert 'class="count"' not in tile                         # nothing waiting: calm
    linda = shift_db.add_recovery("Linda R.", "519-555-0142", "", "order-error",
                                  "free-entree-card", "Missing 8-ct nuggets",
                                  follow_up=True, by="Maya")
    sam = shift_db.add_recovery("Sam P.", "", "", "food-quality", "remade-now",
                                "Cold fries", follow_up=True, by="Maya")
    shift_db.add_recovery("Pat D.", "", "", "order-error", "remade-now", "",
                          follow_up=False, by="Maya", resolved_now=True)  # never counted
    page, tile = recovery_tile(client)
    assert '<span class="count" aria-hidden="true">2</span>' in tile and "2 open" in tile
    assert "coming back" not in tile
    for name in ("Linda R.", "Sam P.", "519-555-0142"):          # counts, never guests
        assert name not in page
    shift_db.set_recovery_contacted(sam, True, "", "Maya")
    _, tile = recovery_tile(client)
    assert '<span class="count" aria-hidden="true">1</span>' in tile
    assert "1 open · 1 coming back" in tile
    shift_db.set_recovery_contacted(linda, True, "", "Maya")
    _, tile = recovery_tile(client)                             # reached: no red count
    assert 'class="count"' not in tile and "2 coming back" in tile
    shift_db.set_recovery_resolved(linda, True, "Came back", "Maya")
    shift_db.set_recovery_resolved(sam, True, "Came back", "Maya")
    assert "Log a guest" in recovery_tile(client)[1]
    client.post("/shift/logout")
    login_operator(client)                                      # the Operator has it too
    assert "Log a guest" in recovery_tile(client)[1]


def test_today_without_the_hub(client, clock, monkeypatch):
    import teamhub_nav
    make_leader(client)
    monkeypatch.setattr(teamhub_nav, "hub_up", lambda: False)
    monkeypatch.setattr(hub_db, "to_read", lambda viewer: 1 / 0)   # never asked
    login_leader(client)
    page = today_page(client)
    assert "Systems &amp; Procedures" not in section(page, "tiles-head")
    assert "Checklists" in section(page, "tiles-head")


def test_to_read_failure_never_breaks_today(client, clock, monkeypatch):
    make_leader(client)
    monkeypatch.setattr(hub_db, "to_read", lambda viewer: 1 / 0)
    login_leader(client)
    page = today_page(client)
    assert "Up to date" in section(page, "tiles-head")


def test_goal_text_is_capped_and_escaped(client, clock):
    maya = make_leader(client)
    login_leader(client)
    client.post("/shift/today/goals", data={
        "leadership": "<script>alert(1)</script>" + "x" * 500, "operational": "ok",
        "area": "y" * 100})
    saved = shift_db.daily_goals(maya, FRI)
    assert len(saved["leadership"]["body"]) == shift_db.DAILY_GOAL_MAX
    assert len(saved["operational"]["area"]) == shift_db.AREA_MAX
    page = today_page(client)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_team_members_cannot_set_goals(client, clock):
    shift_db.add_member("Sam Patel")
    member = next(m for m in shift_db.roster() if m["name"] == "Sam Patel")
    assert hub_db.grant(member["id"], "1042", "Operator") is None
    client.post("/hub/login", data={"name": "Sam", "code": "1042"})
    resp = client.post("/shift/today/goals", data={"leadership": "x", "operational": "y",
                                                   "area": "z"})
    assert resp.status_code == 302 and "/shift/" not in resp.headers["Location"]
    with shift_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_goals").fetchone()[0] == 0


def test_double_tapped_first_save_never_errors(client, clock):
    """Two saves racing for an empty day both succeed (no unique-index 500)."""
    import threading
    maya = make_leader(client)
    errors, barrier = [], threading.Barrier(2)

    def save(text):
        barrier.wait()
        try:
            shift_db.save_daily_goals(maya, FRI, text, "Ops", "BOH")
        except Exception as e:          # pragma: no cover - the failure being tested
            errors.append(e)
    for _ in range(10):
        shift_db.save_daily_goals(maya, FRI, "", "", "")
        threads = [threading.Thread(target=save, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
    assert shift_db.daily_goals(maya, FRI)["leadership"]["body"] in ("A", "B")


def test_cleared_area_stays_cleared(client, clock):
    maya = make_leader(client)
    login_leader(client)
    client.post("/shift/today/goals", data={"leadership": "L", "operational": "O",
                                            "area": "Back of House"})
    client.post("/shift/today/goals", data={"leadership": "L", "operational": "O", "area": ""})
    assert shift_db.get_leader(maya)["area"] is None
    goals = goals_section(client.get("/shift/?edit=goals").data.decode())
    assert 'id="g-area" name="area"' in goals and 'value=""' in goals
    assert "Back of House" not in goals.split("<datalist")[0]
    # ...and tomorrow starts blank too (no old goal's area comes back)
    clock["now"] = at("2026-10-24 09:00")
    goals = goals_section(today_page(client))
    assert 'value=""' in goals


def test_goals_tile_counts_todays_goals(client, clock):
    make_leader(client)
    login_leader(client)
    assert "None yet" in section(today_page(client), "tiles-head")
    client.post("/shift/today/goals", data={"leadership": "L", "operational": "", "area": ""})
    assert "1 of 2 set today" in section(today_page(client), "tiles-head")


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
