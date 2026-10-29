"""Checklists by side of the store: each list is front of house, back of
house or both; each leader sees front, back or every list (the Operator
always sees every list) — set for every leader at once on Admin › Who sees
which checklists. Not a lock: "Show all" is one tap away, and a list's own
page always opens."""

import re
from datetime import datetime

import pytest

import app as cfa_app
import shift_app
import shift_db

FRIDAY_LUNCH = datetime(2026, 10, 23, 12, 7)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(shift_db, "now_local", lambda: FRIDAY_LUNCH)
    shift_db.init_db()
    shift_app._pin_fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    cfa_app.app.config["TESTING"] = True
    with cfa_app.app.test_client() as c:
        yield c


def login(client, name="Operator", pin="9999"):
    return client.post("/shift/login", data={"name": name, "pin": pin})


@pytest.fixture()
def team(client):
    """Neha (director, every list), Grace (front), Jabina (back)."""
    login(client)
    ids = {}
    for name, pin, role in (("Neha", "1111", "admin"), ("Grace", "2222", "lead"),
                            ("Jabina", "3333", "lead")):
        client.post("/shift/admin/leaders/add", data={"name": name, "pin": pin, "role": role})
        ids[name] = next(l["id"] for l in shift_db.leaders() if l["name"] == name)
    client.post(WHO, data={f"view-{ids['Grace']}": "foh", f"view-{ids['Jabina']}": "boh"})
    client.post("/shift/logout")
    return ids


WHO = "/shift/admin/checklist-views"


def views():
    return {l["name"]: l["checklist_side"] for l in shift_db.leaders(include_inactive=True)}


def page(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, url
    return resp.data.decode()


def as_(client, name):
    client.post("/shift/logout")
    login(client, name, {"Neha": "1111", "Grace": "2222", "Jabina": "3333"}.get(name, "9999"))


def test_front_of_house_leader_sees_front_and_shared_lists(client, team):
    as_(client, "Grace")
    breakfast = page(client, "/shift/checklists?daypart=breakfast")
    assert "FOH Opening" in breakfast and "Food Safety Commitment — AM" in breakfast
    assert "BOH Opening" not in breakfast
    lunch = page(client, "/shift/checklists")
    assert "Sanitizer &amp; spindles — 10:30 / 12:30" in lunch     # both sides
    assert "Dining Room &amp; Washrooms — Daily" in lunch and "Drive Thru — Daily" in lunch
    assert "Machines — Daily" not in lunch and "Breading &amp; Raw — Daily" not in lunch
    assert "Your front of house lists." in lunch
    assert 'href="/shift/checklists?daypart=lunch&amp;lists=all">Show all</a>' in lunch


def test_show_all_is_one_tap_and_sticks_across_dayparts(client, team):
    as_(client, "Grace")
    everything = page(client, "/shift/checklists?daypart=breakfast&lists=all")
    assert "BOH Opening" in everything and "FOH Opening" in everything
    assert "Showing every list." in everything
    assert 'href="/shift/checklists?daypart=breakfast">Just front of house</a>' in everything
    rail = everything[everything.index('class="seg fit"'):]
    rail = rail[:rail.index("</nav>")]
    assert all("lists=all" in href for href in re.findall(r'href="([^"]+)"', rail))
    assert 'name="lists" value="all"' in everything            # the day picker keeps it
    past = page(client, "/shift/checklists?date=2026-10-21&daypart=breakfast")
    assert "Your front of house lists." in past and "lists=all" in past


def test_back_of_house_leader_sees_back_and_shared_lists(client, team):
    as_(client, "Jabina")
    lunch = page(client, "/shift/checklists")
    assert "Machines — Daily" in lunch and "Boards — Daily" in lunch
    assert "Sanitizer &amp; spindles — 10:30 / 12:30" in lunch
    assert "Dining Room &amp; Washrooms — Daily" not in lunch and "Drive Thru — Daily" not in lunch
    assert "Your back of house lists." in lunch
    assert "FOH Opening" not in page(client, "/shift/checklists?daypart=breakfast")


@pytest.mark.parametrize("who", ["Neha", "Operator"])
def test_every_list_for_the_director_and_the_operator(client, team, who):
    as_(client, who)
    breakfast = page(client, "/shift/checklists?daypart=breakfast")
    assert "FOH Opening" in breakfast and "BOH Opening" in breakfast
    assert 'class="viewline"' not in breakfast and "Show all" not in breakfast


def test_a_hidden_list_still_opens(client, team):
    as_(client, "Grace")
    page(client, "/shift/checklists")                            # starts today's lists
    boh = next(r for r in shift_db.runs_for_date("2026-10-23") if r["name"] == "BOH Opening")
    assert "BOH Opening" in page(client, f"/shift/checklists/run/{boh['id']}")


def test_every_leader_on_one_page(client, team):
    login(client)
    who = page(client, WHO)
    for name in ("Neha", "Grace", "Jabina"):
        assert f'<legend class="sr">Checklists {name} sees</legend>' in who
    grace = who[who.index(f'id="who-{team["Grace"]}"'):]
    grace = grace[:grace.index("</fieldset>")]
    assert '<input type="radio" name="view-%d" value="foh" checked>' % team["Grace"] in grace
    assert grace.count(" checked") == 1
    flat = " ".join(who.split())
    assert "Front of house</span> <span class=\"row-meta\">Grace</span>" in flat
    assert "Back of house</span> <span class=\"row-meta\">Jabina</span>" in flat
    assert "Both</span> <span class=\"row-meta\">Neha</span>" in flat
    assert "the 10 front-of-house lists and the 5 shared ones" in flat
    assert "<li>Drive Thru — Daily</li>" in who and "<li>Machines — Daily</li>" in who
    assert "the 14 back-of-house lists" in flat


def test_one_save_sets_everyone(client, team):
    login(client)
    resp = client.post(WHO, data={f"view-{team['Neha']}": "foh", f"view-{team['Grace']}": "all",
                                  f"view-{team['Jabina']}": "boh"}, follow_redirects=True)
    assert "Saved: Grace → Both, Neha → Front." in resp.data.decode()           # Jabina didn't change
    assert views() == {"Neha": "foh", "Grace": "all", "Jabina": "boh"}
    resp = client.post(WHO, data={f"view-{team['Neha']}": "foh"}, follow_redirects=True)
    assert "Nothing changed." in resp.data.decode()
    client.post(WHO, data={f"view-{team['Neha']}": "kitchen", f"view-{team['Grace']}": ""})
    assert views() == {"Neha": "foh", "Grace": "all", "Jabina": "boh"}   # unknown: unchanged


def test_only_active_leaders_are_listed_or_set(client, team):
    login(client)
    client.post(f"/shift/admin/leaders/{team['Jabina']}/toggle", data={"active": "0"})
    assert "Jabina" not in page(client, WHO)
    client.post(WHO, data={f"view-{team['Jabina']}": "foh"})
    assert views()["Jabina"] == "boh"


def test_only_admins_set_who_sees_what(client, team):
    as_(client, "Grace")
    resp = client.get(WHO)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/shift/")
    client.post(WHO, data={f"view-{team['Grace']}": "all"})
    assert views()["Grace"] == "foh"
    assert "Who sees which checklists" not in page(client, "/shift/checklists")
    as_(client, "Neha")                                          # an admin leader may
    client.post(WHO, data={f"view-{team['Grace']}": "boh"})
    assert views()["Grace"] == "boh"
    assert 'href="/shift/admin/checklist-views"' in page(client, "/shift/checklists")
    admin = page(client, "/shift/admin")
    assert 'href="/shift/admin/checklist-views"' in admin
    assert (f'href="/shift/admin/checklist-views#who-{team["Grace"]}">Sees back of house</a>'
            in admin)


def test_names_are_escaped(client):
    login(client)
    client.post("/shift/admin/leaders/add", data={"name": "<b>Sam</b>", "pin": "4444"})
    who = page(client, WHO)
    assert "<b>Sam</b>" not in who and "&lt;b&gt;Sam&lt;/b&gt;" in who


def test_no_leaders_yet(client):
    login(client)
    assert "No active leaders yet" in page(client, WHO)


def test_each_list_has_a_side_in_the_template_editor(client, team):
    login(client)
    new = page(client, "/shift/admin/templates/new")
    assert '<option value="" selected>Work it out from the area</option>' in new
    client.post("/shift/admin/templates/new", data={
        "name": "Patio sweep", "daypart": "allday", "area": "Dining Room", "items": "Sweep"})
    patio = next(t for t in shift_db.all_templates() if t["name"] == "Patio sweep")
    assert patio["side"] == "foh"                                # worked out from the area
    client.post(f"/shift/admin/templates/{patio['id']}", data={
        "name": "Patio sweep", "daypart": "allday", "area": "Dining Room", "items": "Sweep",
        "side": "both"})
    assert shift_db.template_with_items(patio["id"])[0]["side"] == "both"
    edit = page(client, f"/shift/admin/templates/{patio['id']}")
    assert '<option value="both" selected>Front and back leaders</option>' in edit
    assert "Work it out from the area" not in edit
    listing = " ".join(page(client, "/shift/admin/templates").split())
    assert "Dining Room · Front and back · 1 items" in listing
    assert "Kitchen · Back of house · 10 items" in listing
