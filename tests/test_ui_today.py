"""Team Hub Today (/shift/): the pure Due-now builder (_due_now) and the
page a leader lands on — triage headline, announcement card, Due now,
Also needs you, lineup, shift log, goals and the section tiles."""

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


def due_rows(page):
    """{title: row html} for the Due now card."""
    rows = {}
    for chunk in section(page, "due-head").split('<a class="row"')[1:]:
        title = chunk.split('<span class="row-title">', 1)[1].split("</span>", 1)[0]
        rows[title] = chunk
    return rows


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
    page = today_page(client)
    assert "Dinner is in session · Fri, Oct 23 · 3:59" in page
    assert "FOH Closing" in due_rows(page)
    clock["now"] = at("2026-10-24 04:00")
    page = today_page(client)
    assert "Breakfast is in session · Sat, Oct 24 · 4:00" in page
    rows = due_rows(page)
    assert "FOH Closing" not in rows and "FOH Opening" in rows
    saturday = {r["id"] for r in shift_db.runs_for_date("2026-10-24")}
    assert any(f"/shift/checklists/run/{rid}" in rows["FOH Opening"] for rid in saturday)


# --- the page ---------------------------------------------------------------

def test_today_headline_counts_what_needs_the_leader(client, clock):
    make_leader(client)
    login_operator(client)
    client.post("/shift/announcements", data={"body": "Pretzel launch Monday"})
    shift_db.add_recovery("Linda R.", "519-555-0142", "", "order-error", "free-entree-card",
                          "Missing 8-ct nuggets", follow_up=True, by="Daniel")
    client.post("/shift/logout")
    login_leader(client)
    page = today_page(client)
    critical = sum(1 for r in shift_app._due_now(
        shift_db.runs_for_date(FRI), "lunch", FRI) if r["critical"])
    assert critical >= 2
    assert "<title>Team Hub</title>" in page
    assert "Lunch is in session · Fri, Oct 23 · 12:07" in page
    assert f"{critical + 2} things need you, Maya." in page
    assert f"<b>{critical}</b> critical lists · <b>1</b> guest · <b>1</b> announcement" in page
    assert 'class="hero compact"' in page


def test_today_all_caught_up(client, clock):
    make_leader(client)
    login_leader(client)
    today_page(client)
    for r in shift_db.runs_for_date(FRI):
        tick_all(r["name"], FRI, critical_only=True)
    page = today_page(client)
    assert "You&#39;re all caught up, Maya." in page or "You're all caught up, Maya." in page
    assert "Nothing critical left" in page
    assert "critical</span>" not in section(page, "due-head")
    for r in shift_db.runs_for_date(FRI):
        tick_all(r["name"], FRI)
    page = today_page(client)
    assert "Nothing due right now" in section(page, "due-head")


def test_today_operator_headline_has_no_name(client, clock):
    login_operator(client)
    page = today_page(client)
    assert "things need you." in page and "need you, Operator" not in page


def test_due_now_rows_link_and_ring(client, clock):
    login_operator(client)
    page = today_page(client)
    rows = due_rows(page)
    sheets = rows["Station cleaning sheets"]
    assert 'href="/shift/checklists#allday"' in sheets
    assert "all-day lists" in sheets
    transition = rows["Breakfast → Lunch Transition"]
    run_id = next(r["id"] for r in shift_db.runs_for_date(FRI)
                  if r["name"] == "Breakfast → Lunch Transition")
    assert f'href="/shift/checklists/run/{run_id}"' in transition
    assert 'class="ring"' in transition and "1 critical" in transition
    assert "From breakfast" in rows["BOH Opening"]
    # Critical lists come first: no critical row after a calm one
    flags = ["critical</span>" in html for html in rows.values()]
    assert flags == sorted(flags, reverse=True)
    assert 'href="/shift/checklists">All checklists' in page


def test_announcement_card_got_it_and_page_link(client, clock):
    make_leader(client)
    login_operator(client)
    category = next(c["id"] for c in hub_db.categories() if c["name"] == "FOH Operations")
    op = hub_db.Viewer("operator", None, "Operator", "admin")
    pid = hub_db.create_page(op, {"category_id": category, "section": "Training",
                                  "title": "Building the Pretzel Sandwich", "summary": "",
                                  "keywords": "", "body": "# Build", "leaders_only": 0,
                                  "required": 1, "annual_review": 0})
    assert hub_db.publish(op, pid, hub_db.get_page(pid)["rev"], "First", True) is None
    client.post("/shift/announcements", data={"body": "Older news"})
    clock["now"] = at("2026-10-23 09:02")
    client.post("/shift/announcements", data={
        "body": f"Pretzel Sandwich launches Monday. Read https://x.onrender.com/hub/page/{pid}."})
    clock["now"] = at("2026-10-23 12:07")
    page = today_page(client)
    assert "From you · 9:02" in page                         # the Operator's own post
    client.post("/shift/logout")
    login_leader(client)
    page = today_page(client)
    card = page[page.index('aria-label="Announcement"'):]
    card = card[:card.index("</article>")]
    newest = shift_db.active_announcements()[0]
    assert "From the Operator · 9:02" in card
    assert "Pretzel Sandwich launches Monday. Read." in card and "onrender" not in card
    assert f'href="/hub/page/{pid}">Open&nbsp;the&nbsp;page' in card
    assert f'action="/shift/announcements/{newest["id"]}/ack"' in card
    assert 'name="next" value="/shift/"' in card
    assert "1 more announcement to read" in card
    resp = client.post(f"/shift/announcements/{newest['id']}/ack", data={"next": "/shift/"})
    assert resp.headers["Location"].endswith("/shift/")
    page = today_page(client)
    assert "Older news" in page and "more announcement" not in page
    client.post(f"/shift/announcements/{shift_db.active_announcements()[1]['id']}/ack",
                data={"next": "/shift/"})
    assert 'aria-label="Announcement"' not in today_page(client)


def test_also_needs_you_guests_todos_and_reading(client, clock):
    maya = make_leader(client)
    daniel = make_leader(client, "Daniel", "1111")
    shift_db.add_recovery("Marcus T.", "519-555-0199", "", "wait-time", "free-dessert-drink",
                          "", follow_up=False, by="Priya")
    shift_db.add_recovery("Linda R.", "519-555-0142", "linda@example.com", "order-error",
                          "free-entree-card", "Missing 8-ct nuggets", follow_up=True, by="Daniel")
    rid = shift_db.add_recovery("Sam K.", "", "", "order-error", "free-entree-card", "",
                                follow_up=False, by="Daniel")
    shift_db.set_recovery_contacted(rid, True, "coming in after work", "Daniel")
    shift_db.add_recovery("Jo P.", "", "", "service", "free-dessert-drink", "", False,
                          "Daniel", contacted_now=True)
    shift_db.add_task(maya, "Order DT headset batteries", "", FRI, "Operator")
    shift_db.add_task(daniel, "Daniel's own to-do", "", "", "Operator")
    category = next(c["id"] for c in hub_db.categories() if c["name"] == "HR")
    op = hub_db.Viewer("operator", None, "Operator", "admin")
    pid = hub_db.create_page(op, {"category_id": category, "section": "Policies",
                                  "title": "Workplace harassment policy", "summary": "",
                                  "keywords": "", "body": "Respect", "leaders_only": 0,
                                  "required": 1, "annual_review": 0})
    assert hub_db.publish(op, pid, hub_db.get_page(pid)["rev"], "First", True) is None
    login_leader(client)
    also = section(today_page(client), "also-head")
    assert "Follow up with Marcus T." in also
    assert "Call Linda R. back" in also and "Missing 8-ct nuggets" in also
    assert "Sam K. is coming back" in also and "Hand over what was promised" in also
    assert "Jo P." not in also                                  # capped at 3 rows...
    assert "+1 more · 2 guests waiting, 2 coming back" in also  # ...and still counted
    for contact in ("519-555", "linda@example.com"):           # contact stays on Recovery
        assert contact not in also
    task = shift_db.tasks_for_leader(maya)[0][0]
    assert f'action="/shift/todo/task/{task["id"]}/toggle"' in also
    assert 'name="done" value="1"' in also and "Due today" in also
    assert "from the Operator" in also
    assert "Daniel&#39;s own to-do" not in also and "Daniel's own to-do" not in also
    assert "1 page to read" in also and "Workplace harassment policy" in also
    assert 'href="/hub/to-read"' in also
    resp = client.post(f"/shift/todo/task/{task['id']}/toggle", data={"done": "1"})
    assert resp.status_code == 302
    assert "Order DT headset batteries" not in today_page(client)


def test_today_without_the_hub(client, clock, monkeypatch):
    import teamhub_nav
    make_leader(client)
    login_operator(client)
    client.post("/shift/announcements", data={"body": "See /hub/page/1 before Monday"})
    client.post("/shift/logout")
    monkeypatch.setattr(teamhub_nav, "hub_up", lambda: False)
    monkeypatch.setattr(hub_db, "to_read", lambda viewer: 1 / 0)   # never asked
    login_leader(client)
    page = today_page(client)
    assert "/hub/" not in page.replace("/hub/page/1", "")
    assert "Open&nbsp;the&nbsp;page" not in page
    assert "Systems &amp; Procedures" not in section(page, "tiles-head")
    assert "Checklists" in section(page, "tiles-head")


def test_to_read_failure_never_breaks_today(client, clock, monkeypatch):
    make_leader(client)
    monkeypatch.setattr(hub_db, "to_read", lambda viewer: 1 / 0)
    login_leader(client)
    page = today_page(client)
    assert "to read" not in page.split('id="tiles-head"')[0]     # no reading row
    assert "Up to date" in section(page, "tiles-head")


def test_lineup_open_positions_first_capped(client, clock):
    login_operator(client)
    page = today_page(client)
    lineup = section(page, "lineup-head")
    assert "No lunch lineup yet" in lineup and 'href="/shift/lineup"' in lineup
    positions = shift_db.active_positions()
    for p in positions[:-2]:
        shift_db.assign_position(FRI, "lunch", p["id"], "Kayla")
    shift_db.assign_position(FRI, "breakfast", positions[-1]["id"], "Noah")  # other daypart
    lineup = section(today_page(client), "lineup-head")
    assert "Lunch lineup" in lineup and ">2 open</span>" in lineup
    cells = lineup.split('<div class="lineup">', 1)[1].split('<div class="row compact">')[0]
    assert cells.count("<div class=\"pos\">") == 8
    assert cells.count('class="open"') == 2
    first_two = cells.split('<div class="pos">')[1:3]
    assert escape(positions[-2]["name"]) in first_two[0] + first_two[1]
    assert escape(positions[-1]["name"]) in first_two[0] + first_two[1]
    assert "Open — fill" in cells
    assert f"All {len(positions)} positions · {len(positions) - 2} filled" in lineup


def test_shift_log_goals_and_tiles(client, clock):
    maya = make_leader(client)
    make_leader(client, "Daniel", "1111")
    shift_db.add_note(FRI, "lunch", "equipment", "Fryer 3 slow to recover", "Daniel")
    shift_db.add_shoutout("Kayla", "2nd-mile-service", "Walked an umbrella out", "Operator")
    g1 = shift_db.create_goal("Drive-thru OTAT under 4:30", "", "OTAT", "sec", 270, "down",
                              "weekly", "", "Operator")
    shift_db.add_goal_update(g1, 282, "", "Maya")
    shift_db.create_goal("OSAT 80%", "", "OSAT", "%", 80, "up", "monthly", "", "Operator")
    shift_db.create_goal("Catering accounts", "", "", "", None, "up", "monthly", "",
                         "Operator")
    shift_db.create_goal("Maya's personal goal", "", "", "", None, "up", "monthly", "",
                         "Operator", leader_id=maya)
    login_leader(client, "Daniel", "1111")
    page = today_page(client)
    log = section(page, "log-head")
    assert "Fryer 3 slow to recover" in log and "Equipment · Daniel · 12:07" in log
    assert "Kayla · 2nd-mile service" in log and "from the Operator" in log
    assert 'href="/shift/notes#n-body"' in log and 'href="/shift/recovery#r-name"' in log
    assert 'href="/shift/shoutouts#s-name"' in log
    goals = section(page, "goals-head")
    assert goals.count('class="row goal-row"') == 2               # capped at 2
    assert "Drive-thru OTAT under 4:30" in goals and "12 sec to go this week" in goals
    assert "Maya&#39;s personal goal" not in page and "Maya's personal goal" not in page
    tiles = section(page, "tiles-head")
    for href in ("/hub/library", "/shift/checklists", "/shift/lineup", "/shift/goals",
                 "/shift/oneonone", "/shift/development"):
        assert f'href="{href}"' in tiles
    assert "3 active" in tiles                                   # Daniel can't see Maya's
    assert "0 of " in tiles and "lessons" in tiles
    client.post("/shift/logout")
    login_leader(client)
    assert "4 active" in section(today_page(client), "tiles-head")  # her own counts
