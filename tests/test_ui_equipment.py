"""Equipment & repairs (/shift/equipment): report a problem, mark service
called, mark it fixed (or reopen), and the count on Today's tile and in the
☰ menu. The clock is pinned so day counts are known."""

import re
from datetime import datetime

import pytest

import app as cfa_app
import hub
import shift_app
import shift_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SHIFT_HUB_FILES_DIR", str(tmp_path / "hub_files"))
    monkeypatch.setattr(shift_db, "now_local",
                        lambda: datetime(2026, 10, 23, 12, 7, tzinfo=shift_db.STORE_TZ))
    shift_db.init_db()
    shift_app._pin_fails.clear()
    hub._fails.clear()
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


def page(client):
    resp = client.get("/shift/equipment")
    assert resp.status_code == 200
    return resp.data.decode()


def card(html, issue_id):
    """One problem's card (or fixed row) on the page, by its anchor."""
    start = html.rindex("<", 0, html.index(f'id="eq-{issue_id}"'))
    ends = [i for i in (html.find("</article>", start),
                        html.find('class="row top done-row"', start + 30)) if i != -1]
    return html[start:min(ends, default=len(html))]


def report(client, **form):
    data = {"equipment": "Fryer 3", "area": "kitchen", "impact": "down",
            "problem": "Not holding 350°, using fryer 2"}
    data.update(form)
    return client.post("/shift/equipment", data=data)


def newest_id():
    with shift_db.closing(shift_db.connect()) as conn:
        return conn.execute("SELECT MAX(id) FROM equipment_issues").fetchone()[0]


def test_empty_page_and_the_report_form(client):
    make_leader(client)
    login_leader(client)
    html = page(client)
    assert "Everything&#39;s working" in html or "Everything's working" in html
    assert "<b>0</b> to call in · <b>0</b> awaiting repair · <b>0</b> out of service" in html
    assert 'action="/shift/equipment"' in html and 'name="equipment"' in html
    assert '<option value="Ice machine">' in html                # suggestions
    for value in shift_db.EQUIPMENT_AREAS + shift_db.EQUIPMENT_IMPACTS:
        assert f'value="{value}"' in html
    assert "Nothing reported" in html and 'id="fixed"' not in html and 'id="radar"' not in html


def test_report_then_service_called_then_fixed(client):
    make_leader(client)
    make_leader(client, "Daniel", "1111")
    login_leader(client)
    resp = report(client, food_safety="1")
    eid = newest_id()
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"#eq-{eid}")
    html = page(client)
    assert "Reported. Thanks for flagging it." in html
    assert "1 to call in</h1>" in html and "<b>1</b> out of service" in html
    c = card(html, eid)
    assert 'class="card eq alert"' in c and "Fryer 3" in c and "Food safety" in c
    assert '<span class="badge b-red">Out of service</span>' in c and "Kitchen" in c
    assert "Not holding 350°" in c and "Reported by Maya · Oct 23 · 12:07" in c
    assert "Delete" not in c                                    # a lead can't delete

    client.post(f"/shift/equipment/{eid}/called",
                data={"called": "1", "note": "Hobart, Thu AM, #4471"})
    html = page(client)
    assert "1 awaiting repair</h1>" in html
    c = card(html, eid)
    assert "<b>Service:</b> Hobart, Thu AM, #4471" in c and "Called in Oct 23 · 12:07 by Maya" in c
    client.post("/shift/logout")
    login_leader(client, "Daniel", "1111")                      # a stale second tap
    client.post(f"/shift/equipment/{eid}/called", data={"called": "1", "note": "dup"})
    html = page(client)
    assert "Already called in by Maya" in html and "dup" not in html

    client.post(f"/shift/equipment/{eid}/fixed", data={"fixed": "1", "note": "New thermostat"})
    html = page(client)
    assert "Everything&#39;s working" in html or "Everything's working" in html
    c = card(html, eid)
    assert "New thermostat" in c and "fixed Oct 23 · 12:07 by Daniel" in c
    assert "service: Hobart, Thu AM, #4471" in c and "Reopen" in c
    assert '<span class="badge b-tan">Out of service</span>' in c   # no red once fixed
    client.post(f"/shift/equipment/{eid}/fixed", data={"fixed": "1"})
    assert "Already marked fixed by Daniel" in page(client)
    # Reopened: back with its service call, fix stamp gone
    client.post(f"/shift/equipment/{eid}/fixed", data={"fixed": "0"})
    html = page(client)
    assert "1 awaiting repair</h1>" in html and "New thermostat" not in html
    # Undo the call: back to To call in
    client.post(f"/shift/equipment/{eid}/called", data={"called": "0"})
    assert "1 to call in</h1>" in page(client)


def test_report_already_called_or_fixed_from_the_form(client):
    login_operator(client)
    report(client, equipment="Ice machine", area="front-counter", impact="limited",
           service_note="Reiss, Monday")
    report(client, equipment="Hand sink", impact="down", fixed_now="1")
    html = page(client)
    assert "1 awaiting repair</h1>" in html and "<b>Service:</b> Reiss, Monday" in html
    assert re.search(r'id="fixed".*Hand sink', html, re.S)


def test_report_needs_what_is_broken_and_bad_values_fall_back(client):
    login_operator(client)
    report(client, equipment="   ")
    html = page(client)
    assert "Say what&#39;s broken first." in html or "Say what's broken first." in html
    assert newest_id() is None
    report(client, area="moon", impact="exploded")
    e = shift_db.get_equipment_issue(newest_id())
    assert (e["area"], e["impact"]) == ("other", "limited")


def test_problem_text_is_escaped(client):
    login_operator(client)
    report(client, equipment="<b>Grill</b>", problem="<script>alert(1)</script>")
    html = page(client)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;Grill&lt;/b&gt;" in html


def test_missing_issue_flashes(client):
    login_operator(client)
    for action, data in (("called", {"called": "1"}), ("fixed", {"fixed": "1"})):
        resp = client.post(f"/shift/equipment/999/{action}", data=data, follow_redirects=True)
        assert "That problem isn&#39;t on the list any more." in resp.data.decode() or \
            "That problem isn't on the list any more." in resp.data.decode()


def test_only_admins_delete(client):
    make_leader(client)
    login_leader(client)
    report(client)
    eid = newest_id()
    resp = client.post(f"/shift/equipment/{eid}/delete")
    assert resp.status_code == 302 and shift_db.get_equipment_issue(eid)
    client.post("/shift/logout")
    login_operator(client)
    assert "Delete" in card(page(client), eid)
    client.post(f"/shift/equipment/{eid}/delete")
    assert shift_db.get_equipment_issue(eid) is None


def test_repeat_repairs_radar(client):
    login_operator(client)
    for name in ("Fryer 3", "fryer 3"):
        report(client, equipment=name)
    report(client, equipment="Ice machine")
    html = page(client)
    radar = html[html.index('id="radar"'):]
    assert re.search(r"fryer 3</span><b>2</b>", radar) and "Ice machine</span><b>1</b>" in radar


def tile(client):
    html = client.get("/shift/").data.decode()
    m = re.search(r'<a class="tile" href="/shift/equipment">.*?</a>', html, re.S)
    assert m, "no Equipment tile"
    return m.group(0)


def menu_row(client):
    html = client.get("/shift/more").data.decode()
    i = html.index("Equipment &amp; repairs")
    return html[i:i + 200]


def test_today_tile_and_menu_badge(client):
    make_leader(client)
    login_leader(client)
    t = tile(client)
    assert "Equipment" in t and "All working" in t and 'class="count"' not in t
    assert "to call in" not in menu_row(client)
    report(client)
    first = newest_id()
    report(client, equipment="Ice machine", service_note="Reiss")
    t = tile(client)
    assert '<span class="count" aria-hidden="true">1</span>' in t
    assert "1 to call in · 1 awaiting repair" in t
    assert "1 to call in" in menu_row(client) and "b-red" in menu_row(client)
    client.post(f"/shift/equipment/{first}/called", data={"called": "1", "note": "Hobart"})
    t = tile(client)
    assert 'class="count"' not in t and "2 awaiting repair" in t
    assert "2 awaiting repair" in menu_row(client) and "b-tan" in menu_row(client)
    for eid in (first, newest_id()):
        client.post(f"/shift/equipment/{eid}/fixed", data={"fixed": "1"})
    assert "All working" in tile(client)
    client.post("/shift/logout")
    login_operator(client)
    assert "All working" in tile(client)                        # the Operator has it too
