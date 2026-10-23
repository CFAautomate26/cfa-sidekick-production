"""Team Hub redesign: the Checklists screens (overview with the daypart rail
and the read-only "Open now" card, the run page, scorecard, history and the
checklist template admin). The clock is pinned so every daypart/weekday rule
is tested on a known day, not on whatever day the suite happens to run."""

import re
from datetime import datetime
from html import escape

import pytest

import app as cfa_app
import shift_app
import shift_db

FRIDAY_LUNCH = datetime(2026, 10, 23, 12, 7)        # Fri Oct 23, 12:07


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": FRIDAY_LUNCH}
    monkeypatch.setattr(shift_db, "now_local", lambda: now["t"])
    return now


@pytest.fixture()
def client(tmp_path, monkeypatch, clock):
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


def run_named(name, day="2026-10-23"):
    return next(r for r in shift_db.runs_for_date(day) if r["name"] == name)


def tick(name, count=None, skip_critical=False, day="2026-10-23", by="Maya"):
    """Check off a run's items in order (optionally leaving critical ones)."""
    _, items = shift_db.run_with_items(run_named(name, day)["id"])
    picked = [i for i in items if not (skip_critical and i["critical"])]
    for i in picked[:count]:
        shift_db.set_item_done(i["id"], True, by)


# The seeds' one lunch list (sanitizer & spindles) is all critical items;
# tests about a mixed list add this one: nine items, the second critical.
LUNCH_RUSH = [("Menu boards + kiosks on lunch by 10:30", False),
              ("Breakfast product pulled and waste logged", True),
              ("Fry station switched to waffle fries", False),
              ("Breading station set for lunch volume", False),
              ("Boards stocked for the rush", False),
              ("Sauces, lids + bags restocked front and DT", False),
              ("Dining room reset before 11:30", False),
              ("Lunch lineup reviewed + breaks planned", False),
              ("Headset batteries swapped", False)]
SANITIZER_LUNCH = "Sanitizer & spindles — 10:30 / 12:30"


def add_lunch_rush():
    return shift_db.create_template("Lunch Rush", "lunch", "All", LUNCH_RUSH)


def section(page, heading):
    """The HTML of one <section> on a page, found by its <h2> heading."""
    start = page.index(f"<h2>{heading}")
    end = page.find("</section>", start)
    return page[start:end]


def attr_count(page, attr):
    """How many elements carry `attr` (the base script mentions the hooks
    as "[data-progress]" selectors, which this doesn't count)."""
    return len(re.findall(rf"\s{attr}[\s>=]", page))


# --- the overview ---------------------------------------------------------

def test_hero_rail_and_daypart_param(client, clock):
    login_operator(client)
    page = client.get("/shift/checklists").data.decode()
    assert "Checklists · Fri, Oct 23" in page
    assert '<h1 class="headline">Lunch checklists</h1>' in page
    rail = page[page.index('<nav class="seg fit"'):]
    rail = rail[:rail.index("</nav>")]
    for dp in shift_db.DAYPARTS:                        # four plain links
        assert f'href="/shift/checklists?daypart={dp}"' in rail
    assert rail.count("<a ") == 4 and rail.count('class="live"') == 1
    on = rail[rail.index('class="on"'):]
    assert on.startswith('class="on" aria-current="page">Lunch') and 'class="live"' in on[:120]

    dinner = client.get("/shift/checklists?daypart=DINNER").data.decode()
    assert "Dinner checklists" in dinner and "Later today (5–close)" in dinner
    for bad in ["bogus", "allday", "", "lunch%00"]:     # validated, falls back to now
        page = client.get(f"/shift/checklists?daypart={bad}").data.decode()
        assert "Lunch checklists" in page, bad


def test_open_now_is_a_read_only_preview(client, clock):
    login_operator(client)
    add_lunch_rush()
    client.get("/shift/checklists")
    tick(SANITIZER_LUNCH)                                    # done: Lunch Rush is open now
    tick("Lunch Rush", count=7, skip_critical=True)
    page = client.get("/shift/checklists").data.decode()
    card = page[page.index('aria-label="Open now"'):page.index("</article>")]
    run = run_named("Lunch Rush")
    assert "Lunch Rush" in card and "7<small>of 9</small>" in card
    _, items = shift_db.run_with_items(run["id"])
    left = [i for i in items if not i["done"]]
    critical = next(i for i in left if i["critical"])
    # Still to do: critical first, then the rest (at most three)
    assert card.index(escape(critical["label"])) < card.index(
        escape(next(i for i in left if not i["critical"])["label"]))
    assert card.count("<li") == len(left) <= 3
    assert f'href="/shift/checklists/run/{run["id"]}"' in card and "Open the list" in card
    # Ticking stays on the run page: no item forms, no progress hooks here
    assert 'class="item"' not in page and "/shift/item/" not in page
    assert attr_count(page, "data-progress") == 0 and attr_count(page, "data-critical") == 0


def test_open_now_lists_more_than_three_with_a_count(client, clock):
    login_operator(client)
    client.get("/shift/checklists")
    page = client.get("/shift/checklists").data.decode()
    card = page[page.index('aria-label="Open now"'):page.index("</article>")]
    assert escape(SANITIZER_LUNCH) in card
    assert card.count("<li") == 4 and "and 1 more" in card     # 4 items, none done


def test_also_due_now_critical_first_with_breakfast_leftovers(client, clock):
    login_operator(client)
    add_lunch_rush()
    client.get("/shift/checklists")
    for name in ["FOH Opening", "Food Safety Commitment — AM"]:
        tick(name)                                           # breakfast done...
    tick("BOH Opening", skip_critical=True)                  # ...except BOH's critical items
    page = client.get("/shift/checklists").data.decode()
    also = section(page, "Also due now")
    assert "BOH Opening" in also and "From breakfast" in also
    assert "FOH Opening" not in also                         # finished lists never nag
    assert "Lunch Rush" in also
    card = page[page.index('aria-label="Open now"'):page.index("</article>")]
    assert escape(SANITIZER_LUNCH) in card                   # first lunch list, critical
    assert "Drive Thru — Weekly" not in also                 # weekly due Saturday, it's Friday
    boh = run_named("BOH Opening")
    lunch = [r for r in shift_db.runs_for_date("2026-10-23")
             if r["frequency"] == "daily" and r["daypart"] == "lunch"]
    lists = len(lunch) + 1
    critical = sum(r["critical_remaining"] for r in lunch) + boh["critical_remaining"]
    assert f"<b>{lists}</b> lists due now · <b>{critical}</b> critical items left" in page


def test_weekly_lists_are_due_on_saturday_and_stay_in_the_week_fold(client, clock):
    clock["t"] = datetime(2026, 10, 24, 12, 0)               # Saturday lunch
    login_operator(client)
    client.get("/shift/checklists")
    page = client.get("/shift/checklists").data.decode()
    also = section(page, "Also due now")
    assert "Drive Thru — Weekly" in also and "Due today" in also
    period = section(page, "Weekly &amp; monthly")
    assert "This week · Week of Oct 19" in period and "This month · October 2026" in period
    assert "Drive Thru — Weekly" in period                    # the fold holds the whole week
    assert 'id="period"' in page


def test_monthly_audits_due_in_the_last_week(client, clock):
    clock["t"] = datetime(2026, 10, 27, 12, 0)               # Tuesday, 4 days left
    login_operator(client)
    client.get("/shift/checklists")
    page = client.get("/shift/checklists").data.decode()
    also = section(page, "Also due now")
    assert "Drive Thru — Monthly audit" in also and "4 days left" in also


def test_station_cleaning_coming_up_and_links(client, clock):
    make_leader(client)
    login_leader(client)
    client.get("/shift/checklists")
    page = client.get("/shift/checklists").data.decode()
    allday = [r for r in shift_db.runs_for_date("2026-10-23")
              if r["frequency"] == "daily" and r["daypart"] == "allday"]
    station = section(page, "Station cleaning · all day")
    assert page.count('id="allday"') == 1
    assert f"0 of {sum(r['total'] for r in allday)} done across {len(allday)} sheets" in station
    assert all(escape(r["name"]) in station for r in allday)
    coming = section(page, "Coming up")
    assert "Afternoon Reset" in coming and "FOH Closing" in coming and "Dinner ·" in coming
    assert escape(SANITIZER_LUNCH) not in coming             # it's due now, not coming
    assert 'href="/shift/checklists/scorecard"' in page and 'href="/shift/history"' in page
    assert "Edit checklist templates" not in page            # a lead isn't an admin
    client.post("/shift/logout")
    login_operator(client)
    assert 'href="/shift/admin/templates"' in client.get("/shift/checklists").data.decode()


def test_nothing_due_and_the_dinner_view(client, clock):
    login_operator(client)
    client.get("/shift/checklists")
    tick(SANITIZER_LUNCH)
    for name in ["FOH Opening", "BOH Opening", "Food Safety Commitment — AM"]:
        tick(name)
    page = client.get("/shift/checklists").data.decode()
    assert "Nothing due right now" in page and "Every lunch list is done." in page
    assert "<h2>Also due now</h2>" not in page and "<h2>Finished · lunch</h2>" in page
    dinner = client.get("/shift/checklists?daypart=dinner").data.decode()
    assert 'aria-label="Open now"' not in dinner and "FOH Closing" in dinner and "Coming up" not in dinner


def test_a_past_day(client, clock):
    login_operator(client)
    add_lunch_rush()
    shift_db.ensure_runs_for_date("2026-10-21")
    tick("Lunch Rush", count=2, day="2026-10-21")
    page = client.get("/shift/checklists?date=2026-10-21&daypart=lunch").data.decode()
    assert "Checklists · Wed, Oct 21" in page and "Looking back at Wed, Oct 21" in page
    assert 'aria-label="Open now"' not in page and 'class="live"' not in page
    assert 'href="/shift/checklists?daypart=breakfast&amp;date=2026-10-21"' in page
    assert "critical missed" in page and "nothing checked" in page
    assert "<b>0</b> of <b>2</b> lists finished · <b>4</b> critical items missed" in page
    assert 'name="date" value="2026-10-21"' in page          # the day picker keeps working
    empty = client.get("/shift/checklists?date=2026-09-01").data.decode()
    assert "Nobody opened the app on Tue, Sep 1" in empty


def test_checklists_still_need_a_leader(client, clock):
    resp = client.get("/shift/checklists?daypart=dinner")
    assert resp.status_code == 302 and "/hub/login" in resp.headers["Location"]


# --- the run page ---------------------------------------------------------

def test_run_page_keeps_the_script_hooks_and_fields(client, clock):
    login_operator(client)
    add_lunch_rush()
    client.get("/shift/checklists")
    run = run_named("Lunch Rush")
    tick("Lunch Rush", count=1)
    page = client.get(f"/shift/checklists/run/{run['id']}").data.decode()
    assert attr_count(page, "data-progress") == 1
    assert attr_count(page, "data-progress-text") == 1 and attr_count(page, "data-critical") == 1
    assert re.search(r'<span class="bar ?[^"]*"><span style="width: \d+', page)
    _, items = shift_db.run_with_items(run["id"])
    assert page.count('<form class="item" method="post"') == len(items)
    for i in items:
        assert f'action="/shift/item/{i["id"]}/toggle" id="item-{i["id"]}"' in page
    assert page.count('name="done" value="0"') == 1          # the one done item undoes
    for hook in ['class="itemrow done"', 'class="itemrow "', 'class="checkmark"',
                 'class="label"', 'class="meta"', 'class="crit"']:
        assert hook in page, hook
    assert "done by Maya · 12:07" in page
    assert f'action="/shift/checklists/run/{run["id"]}/owner"' in page and 'name="owner"' in page
    assert f'action="/shift/checklists/run/{run["id"]}/verify"' in page
    assert 'name="verified" value="1"' in page and "I checked this station" in page
    assert "Checklists · Today" in page
    assert 'href="/shift/checklists?daypart=lunch"' in page    # back to its daypart


def test_run_page_links_back_to_its_section(client, clock):
    login_operator(client)
    client.get("/shift/checklists")
    sheet = run_named("Drive Thru — Daily")
    weekly = run_named("Drive Thru — Weekly")
    assert 'href="/shift/checklists#allday"' in client.get(
        f"/shift/checklists/run/{sheet['id']}").data.decode()
    page = client.get(f"/shift/checklists/run/{weekly['id']}").data.decode()
    assert 'href="/shift/checklists#period"' in page and "Checklists · Week of Oct 19" in page


def test_locked_run_page_shows_the_lock_and_no_forms(client, clock):
    make_leader(client)
    shift_db.ensure_runs_for_date("2026-10-20")
    past = run_named("Drive Thru — Daily", "2026-10-20")
    login_leader(client)
    page = client.get(f"/shift/checklists/run/{past['id']}").data.decode()
    assert "This list closed when its day" in page and "Only the Operator can correct it" in page
    assert 'class="item"' not in page and 'name="owner"' not in page
    assert 'name="verified"' not in page and "Nobody was set" in page
    assert attr_count(page, "data-progress") == 1             # progress still shows
    assert 'href="/shift/checklists?date=2026-10-20#allday"' in page


# --- scorecard, history, template admin -------------------------------------

def test_scorecard_cards(client, clock):
    login_operator(client)
    for day in ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]:
        shift_db.ensure_runs_for_date(day)
        tick("FOH Opening", day=day, count=9)
    page = client.get("/shift/checklists/scorecard").data.decode()
    assert '<h1 class="headline">Checklist scorecard</h1>' in page
    card = page[page.index("FOH Opening"):]
    card = card[:card.index('<div class="card sc">')]
    assert "90<small>%</small>" in card and "last 4 open days" in card
    assert "<b>0 of 4</b> fully done" in card
    assert '<details class="missed">' in card and "Most often missed" in card
    assert "Daily list items checked off" not in page          # that line is on Checklists
    overview = client.get("/shift/checklists").data.decode()
    assert "Daily list items checked off over the last 4 open days" in overview


def test_history_page(client, clock):
    login_operator(client)
    client.get("/shift/checklists")
    tick("FOH Opening", count=2)
    page = client.get("/shift/history").data.decode()
    assert '<h1 class="headline">What happened today</h1>' in page
    assert 'href="/shift/history?date=2026-10-22"' in page and ">Yesterday<" in page
    assert "Maya · 12:07" in page and "No shift notes that day." in page
    page = client.get("/shift/history?date=2026-10-22").data.decode()
    assert "What happened yesterday" in page
    page = client.get("/shift/history?date=2026-09-01").data.decode()
    assert "What happened Sep 1" in page and "nobody opened the app that day" in page


def test_template_admin_pages(client, clock):
    make_leader(client, role="lead")
    login_leader(client)
    assert client.get("/shift/admin/templates").status_code == 302    # admins only
    client.post("/shift/logout")
    login_operator(client)
    page = client.get("/shift/admin/templates").data.decode()
    for heading in ["Daily", "Weekly", "Monthly"]:
        assert f"<h2>{heading} <span" in page
    assert "Retired" not in page and 'href="/shift/admin/templates/new"' in page
    t = shift_db.all_templates()[0]
    client.post(f"/shift/admin/templates/{t['id']}/toggle", data={"active": "0"})
    page = client.get("/shift/admin/templates").data.decode()
    retired = section(page, "Retired")
    assert escape(t["name"]) in retired and "Restore" in retired
    edit = client.get(f"/shift/admin/templates/{t['id']}").data.decode()
    for field in ['name="name"', 'name="frequency"', 'name="due_weekday"', 'name="daypart"',
                  'name="area"', 'name="items"']:
        assert field in edit, field
    assert f'action="/shift/admin/templates/{t["id"]}"' in edit
    new = client.get("/shift/admin/templates/new").data.decode()
    assert 'action="/shift/admin/templates/new"' in new and "New checklist" in new
