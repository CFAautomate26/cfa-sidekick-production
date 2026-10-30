"""The Operator's six one-tap checklists (Oct 30, 2026): FOH/BOH Opening,
Changeover and Closing, each a single check-off seen by every leader, shown
as one list for the day and still measured by the scorecard. The live
database is converted once on deploy; a fresh install keeps the detailed
demo lists. The clock is pinned to a known Friday lunch."""

import re
from datetime import datetime

import pytest

import app as cfa_app
import shift_app
import shift_db

THURSDAY_LUNCH = datetime(2026, 10, 22, 12, 7)
FRIDAY_LUNCH = datetime(2026, 10, 23, 12, 7)
SATURDAY_LUNCH = datetime(2026, 10, 24, 12, 7)
SIX = ["FOH Opening", "BOH Opening", "FOH Changeover", "BOH Changeover",
       "FOH Closing", "BOH Closing"]


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


def convert():
    """What the deploy does to the live store's (not fresh) database."""
    with shift_db.closing(shift_db.connect()) as conn, conn:
        conn.execute("DELETE FROM meta WHERE key=?", (shift_db.SIGN_OFF_KEY,))
    shift_db.init_db()


def login(client, name="Operator", pin="9999"):
    client.post("/shift/logout")
    return client.post("/shift/login", data={"name": name, "pin": pin})


def leader(client, name="Grace", pin="2222", view="foh"):
    login(client)
    client.post("/shift/admin/leaders/add", data={"name": name, "pin": pin})
    lid = next(l["id"] for l in shift_db.leaders() if l["name"] == name)
    shift_db.set_leader_checklist_view(lid, view)
    login(client, name, pin)


def page(client, url="/shift/checklists"):
    resp = client.get(url)
    assert resp.status_code == 200, url
    return resp.data.decode()


def row(html, name):
    """The sign-off row for one list."""
    start = html.index(f'<span class="row-title">{name}</span>')
    start = html.rindex('<div class="row"', 0, start)
    return html[start:html.index("</div>", start)]


def tick_form(html, name, done="1"):
    m = re.search(r'action="(/shift/item/\d+/toggle)">\s*<input type="hidden" name="done" '
                  rf'value="{done}">', row(html, name))
    return m.group(1) if m else None


def test_the_live_lists_become_six_one_tap_sign_offs(client, capsys):
    before = {t["name"]: t["id"] for t in shift_db.all_templates()}
    convert()
    active = shift_db.all_templates()
    assert [t["name"] for t in active] == SIX
    for t in active:
        _, items = shift_db.template_with_items(t["id"])
        assert [i["label"] for i in items] == [shift_db.SIGN_OFF_ITEM]
        assert (t["side"], t["frequency"]) == ("both", "daily")
    kept = {t["name"]: t["id"] for t in active}
    for name in ("FOH Opening", "BOH Opening", "FOH Closing", "BOH Closing"):
        assert kept[name] == before[name]                    # history and scorecard carry on
    retired = [t for t in shift_db.all_templates(include_inactive=True) if not t["active"]]
    assert len(retired) == len(before) - 4
    log = capsys.readouterr().out
    assert ("kept: FOH Opening, BOH Opening, FOH Closing, BOH Closing; "
            "added: FOH Changeover, BOH Changeover; retired (") in log
    assert shift_db.sign_off_mode()
    shift_db.init_db()                                       # once only
    assert [t["name"] for t in shift_db.all_templates()] == SIX


def test_a_fresh_install_keeps_the_detailed_lists(client):
    assert not shift_db.sign_off_mode()
    foh = next(t for t in shift_db.all_templates() if t["name"] == "FOH Opening")
    assert foh["item_count"] > 1
    with shift_db.closing(shift_db.connect()) as conn:
        assert conn.execute("SELECT 1 FROM meta WHERE key=?", (shift_db.SIGN_OFF_KEY,)).fetchone()


def test_the_operators_later_changes_stand(client):
    convert()
    old = next(t for t in shift_db.all_templates(include_inactive=True)
               if t["name"] == "Drive Thru — Daily")
    shift_db.set_template_active(old["id"], True)            # restored in Admin
    shift_db.init_db()
    assert any(t["name"] == "Drive Thru — Daily" for t in shift_db.all_templates())
    assert not shift_db.sign_off_mode()                      # a detailed list is back


def test_everyone_sees_all_six_as_one_list(client):
    convert()
    leader(client, view="foh")                               # set to front of house earlier
    html = page(client)
    assert [n for n in SIX if f'<span class="row-title">{n}</span>' in html] == SIX
    assert all(html.index(a) < html.index(b) for a, b in zip(SIX, SIX[1:]))
    assert "Today&#39;s checklists" in html or "Today's checklists" in html
    assert "<b>0</b> of <b>6</b> checked off." in html
    assert "Check each one off once the full checklist in the restaurant is done." in html
    assert 'class="seg fit"' not in html                     # no daypart rail
    assert 'class="viewline"' not in html                    # nothing is hidden from anyone
    assert "Breakfast ·" in row(html, "FOH Opening")
    assert "Not checked off" in row(html, "FOH Opening")     # breakfast is over at 12:07
    assert "Not done yet" in row(html, "FOH Changeover")
    assert "Not done yet" in row(html, "BOH Closing")
    leader(client, "Jabina", "3333", view="boh")
    assert [n for n in SIX if f'<span class="row-title">{n}</span>' in page(client)] == SIX


def test_check_off_in_one_tap_and_undo(client):
    convert()
    leader(client)
    action = tick_form(page(client), "FOH Changeover")
    resp = client.post(action, data={"done": "1", "back": "checklists"})
    assert resp.status_code == 302
    run = next(r for r in shift_db.runs_for_date("2026-10-23") if r["name"] == "FOH Changeover")
    assert resp.headers["Location"].endswith(f"/shift/checklists#run-{run['id']}")
    html = page(client)
    assert "Done by Grace · 12:07" in row(html, "FOH Changeover")
    assert "<b>1</b> of <b>6</b> checked off." in html
    assert tick_form(html, "FOH Changeover") is None         # no second tick
    undo = tick_form(html, "FOH Changeover", done="0")
    assert undo == action
    client.post(undo, data={"done": "0", "back": "checklists"})
    html = page(client)
    assert "Not done yet" in row(html, "FOH Changeover") and "<b>0</b> of <b>6</b>" in html


def test_all_six_done(client):
    convert()
    leader(client)
    for name in SIX:
        client.post(tick_form(page(client), name), data={"done": "1", "back": "checklists"})
    assert "All <b>6</b> checked off." in page(client)


def test_a_past_day_is_locked_except_for_the_operator(client, clock):
    clock["t"] = THURSDAY_LUNCH
    convert()
    leader(client)
    client.post(tick_form(page(client), "FOH Opening"), data={"done": "1", "back": "checklists"})
    clock["t"] = FRIDAY_LUNCH
    thursday = page(client, "/shift/checklists?date=2026-10-22")
    assert "Done by Grace · 12:07" in row(thursday, "FOH Opening")
    assert "Not checked off" in row(thursday, "BOH Closing")
    assert tick_form(thursday, "BOH Closing") is None and tick_form(thursday, "FOH Opening", "0") is None
    item = shift_db.run_with_items(next(r["id"] for r in shift_db.runs_for_date("2026-10-22")
                                        if r["name"] == "BOH Closing"))[1][0]
    resp = client.post(f"/shift/item/{item['id']}/toggle", data={"done": "1", "back": "checklists"},
                       follow_redirects=True)
    assert "That list is closed" in resp.data.decode() and not shift_db.run_with_items(
        item["run_id"])[1][0]["done"]
    login(client)                                            # the Operator can correct it
    action = tick_form(page(client, "/shift/checklists?date=2026-10-22"), "BOH Closing")
    resp = client.post(action, data={"done": "1", "back": "checklists"})
    assert f"/shift/checklists?date=2026-10-22#run-{item['run_id']}" in resp.headers["Location"]
    assert shift_db.run_with_items(item["run_id"])[1][0]["done_by"] == "Operator"


def test_lists_started_before_the_change_keep_their_progress(client):
    leader(client)
    page(client)                                             # today's detailed lists start
    foh = next(r for r in shift_db.runs_for_date("2026-10-23") if r["name"] == "FOH Opening")
    _, items = shift_db.run_with_items(foh["id"])
    for i in items[:2]:
        shift_db.set_item_done(i["id"], True, "Grace")
    convert()
    html = page(client)
    assert f"2 of {len(items)} done" in row(html, "FOH Opening")
    assert tick_form(html, "FOH Opening") is None            # its own page has the items
    assert f'href="/shift/checklists/run/{foh["id"]}"' in row(html, "FOH Opening")
    assert "Drive Thru — Daily" not in html                  # retired, nothing checked
    assert "FOH Changeover" in html and tick_form(html, "FOH Changeover")


def test_the_scorecard_measures_the_sign_offs(client, clock):
    clock["t"] = THURSDAY_LUNCH
    convert()                                                # changeovers added on Thursday
    leader(client)
    page(client)
    clock["t"] = FRIDAY_LUNCH
    for name in SIX:
        client.post(tick_form(page(client), name), data={"done": "1", "back": "checklists"})
    clock["t"] = SATURDAY_LUNCH
    cards = {c["name"]: c for c in shift_db.checklist_scorecard()}
    assert sorted(cards) == sorted(SIX)
    for name in SIX:
        friday = next(h for h in cards[name]["history"] if h["start"] == "2026-10-23")
        assert (friday["done"], friday["total"], friday["complete"]) == (1, 1, True)
    # Added part-way through Thursday: not scored as missing it
    assert [h["start"] for h in cards["FOH Changeover"]["history"]] == ["2026-10-23"]
    thursday = next(h for h in cards["FOH Opening"]["history"] if h["start"] == "2026-10-22")
    assert (thursday["done"], thursday["total"]) == (0, 1)   # owed, and missed
    assert "Checklists checked off over the last" in page(client)


def test_sign_off_mode_needs_every_list_to_be_one_tap(client):
    convert()
    assert shift_db.sign_off_mode()
    tid = shift_db.create_template("Patio sweep", "lunch", "Dining Room", [("Sweep", False)],
                                   frequency="weekly")
    assert not shift_db.sign_off_mode()                      # a weekly list
    shift_db.set_template_active(tid, False)
    foh = next(t for t in shift_db.all_templates() if t["name"] == "FOH Opening")
    shift_db.update_template(foh["id"], "FOH Opening", "breakfast", "Front Counter",
                             [("Doors", False), ("Tills", False)])
    assert not shift_db.sign_off_mode()                      # a detailed list


def test_a_same_day_change_to_a_new_list_supersedes_its_start_tomorrow(client, clock):
    clock["t"] = THURSDAY_LUNCH
    convert()
    boh = next(t for t in shift_db.all_templates() if t["name"] == "BOH Changeover")
    shift_db.update_template(boh["id"], "BOH Changeover", "lunch", "Kitchen",
                             [(shift_db.SIGN_OFF_ITEM, False)], frequency="weekly", due_weekday=4)
    with shift_db.closing(shift_db.connect()) as conn:
        log = [tuple(r) for r in conn.execute(
            "SELECT changed_on, frequency, active FROM checklist_schedule_log "
            "WHERE template_id=? ORDER BY changed_on, id", (boh["id"],))]
    assert log == [("2026-10-22", "weekly", 1)]


def test_the_score_counts_lists_not_old_items(client, clock):
    clock["t"] = datetime(2026, 10, 21, 12, 7)               # Wednesday, still detailed
    leader(client)
    page(client)
    wed = next(r for r in shift_db.runs_for_date("2026-10-21") if r["name"] == "FOH Opening")
    _, items = shift_db.run_with_items(wed["id"])
    for i in items:                                          # one long list, all done
        shift_db.set_item_done(i["id"], True, "Grace")
    clock["t"] = THURSDAY_LUNCH
    convert()
    page(client)                                             # Thursday: nothing signed off
    clock["t"] = FRIDAY_LUNCH
    html = page(client)
    # Wednesday 1 of 4 lists done + Thursday 0 of 4 = 1 of 8, not ~9 of 40 items
    assert '<span class="num">12<small>%</small></span>' in html
    card = next(c for c in shift_db.checklist_scorecard() if c["name"] == "FOH Opening")
    assert card["recent"]["pct"] == 50                       # Wed done, Thu missed: half


def test_retired_weekly_lists_stay_off_the_sign_off_page(client):
    leader(client)
    page(client)
    weekly = next(r for r in shift_db.runs_for_date("2026-10-23")
                  if r["name"] == "Drive Thru — Weekly")
    shift_db.set_item_done(shift_db.run_with_items(weekly["id"])[1][0]["id"], True, "Grace")
    convert()
    html = page(client)
    assert "Drive Thru — Weekly" not in html and "Weekly &amp; monthly" not in html


def test_show_all_still_appears_when_a_list_is_hidden(client):
    convert()
    shift_db.create_template("Patio check", "dinner", "Patio", [("Done", False)], side="foh")
    leader(client, "Jabina", "3333", view="boh")
    html = page(client)
    assert "Patio check" not in html and "Your back of house lists." in html
    html = page(client, "/shift/checklists?lists=all")
    action = tick_form(html, "Patio check")
    resp = client.post(action, data={"done": "1", "back": "checklists", "lists": "all"})
    assert "/shift/checklists?lists=all#run-" in resp.headers["Location"]
