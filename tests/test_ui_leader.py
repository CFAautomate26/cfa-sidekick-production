"""Team Hub leader pages in the new look: Leadership Development (course +
the Hub's Library), Lineups & Setups (open positions first), and the
restyled logbooks and Admin. These check what the redesign promises —
real data, the compact hero, privacy kept — not pixel details."""

import os
import re

import pytest

import app as cfa_app
import hub
import hub_db
import shift_app
import shift_db
import teamhub_nav

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def login_operator(client):
    return client.post("/shift/login", data={"name": "Operator", "pin": "9999"})


def login_leader(client, name="Maya", pin="4721"):
    return client.post("/shift/login", data={"name": name, "pin": pin})


def leader_id(name):
    return next(l["id"] for l in shift_db.leaders() if l["name"] == name)


OPERATOR = hub_db.Viewer("operator", None, "Operator", "admin")


def category_id(name):
    return next(c["id"] for c in hub_db.categories(include_inactive=True) if c["name"] == name)


def hub_page(category, title, section="", required=0, publish=True, summary=""):
    pid = hub_db.create_page(OPERATOR, {
        "category_id": category_id(category), "section": section, "title": title,
        "summary": summary, "keywords": "", "body": "Steps.", "leaders_only": 0,
        "required": required, "annual_review": 0})
    if publish:
        assert hub_db.publish(OPERATOR, pid, hub_db.get_page(pid)["rev"], "", True) is None
    return pid


def hero(html):
    """(eyebrow, headline) of the page's compact hero."""
    m = re.search(r'<section class="hero compact">\s*<p class="eyebrow">(.*?)</p>\s*'
                  r'<h1 class="headline">(.*?)</h1>', html, re.S)
    assert m, "no compact hero"
    return m.group(1).strip(), m.group(2).strip()


# --- Leadership Development: the course ------------------------------------

def test_development_admin_index_shows_every_leader_and_the_stages(client):
    shift_db.add_leader("Maya", "4721", "admin")
    shift_db.add_leader("Daniel", "1234", "lead")
    lessons = [l for m in shift_db.course_overview(leader_id("Daniel")) for l in m["lessons"]]
    for l in lessons[:15]:                                   # all of Mindset 101
        shift_db.set_lesson_done(l["id"], leader_id("Daniel"), "", "Operator")
    login_leader(client)
    page = client.get("/shift/development").data.decode()
    assert hero(page) == ("Leadership Development", "Course progress")
    for stage in ("Mindset 101", "Leading Others", "Leading Teams", "Leading Organization"):
        assert stage in page
    assert "<b>48</b> lessons in 4 stages" in page
    # Maya (an admin leader) gets her own course card first, linking to her page
    assert "Your course" in page
    assert page.index('class="row-title">Maya') < page.index('class="row-title">Daniel')
    assert f'href="/shift/development/{leader_id("Maya")}"' in page
    # Daniel is on Leading Others with 15 done; one leader finished stage 1
    assert "on Leading Others" in page and "<b>15</b> of 48" in page
    assert "1 of 2 leaders finished" in page


def test_development_operator_has_no_own_course_card(client):
    shift_db.add_leader("Maya", "4721", "lead")
    login_operator(client)
    page = client.get("/shift/development").data.decode()
    assert "Your course" not in page and "Every leader" in page
    assert "Maya" in page


def test_development_leader_page_opens_the_current_stage(client):
    shift_db.add_leader("Maya", "4721", "lead")
    maya = leader_id("Maya")
    modules = shift_db.course_overview(maya)
    for l in modules[0]["lessons"]:
        shift_db.set_lesson_done(l["id"], maya, "", "Operator")
    login_leader(client)
    page = client.get(f"/shift/development/{maya}").data.decode()
    assert hero(page)[1] == "Your leadership course"
    assert "<b>15</b> of <b>48</b> lessons done · up next: Waste 101" in page
    # Stage 1 is done (folded), stage 2 is being worked on (open)
    assert '<details class="fold stage" id="stage-1">' in page
    assert '<details class="fold stage" id="stage-2" open>' in page
    assert "Up next" in page
    # The lesson forms still post the same fields
    first = modules[1]["lessons"][0]
    assert f'action="/shift/development/{maya}/lesson/{first["id"]}"' in page
    assert 'name="action" value="complete"' in page and 'name="note"' in page


def test_development_update_returns_to_the_lesson_open(client):
    shift_db.add_leader("Maya", "4721", "lead")
    maya = leader_id("Maya")
    lesson = shift_db.course_overview(maya)[2]["lessons"][3]     # a later stage
    login_leader(client)
    resp = client.post(f"/shift/development/{maya}/lesson/{lesson['id']}",
                       data={"action": "complete", "note": "Good talk"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(
        f"/shift/development/{maya}?lesson={lesson['id']}#lesson-{lesson['id']}")
    page = client.get(resp.headers["Location"].split("#")[0]).data.decode()
    assert f'id="lesson-{lesson["id"]}" open>' in page
    assert '<details class="fold stage" id="stage-3" open>' in page
    assert "Good talk" in page


def test_admin_viewing_a_leader_gets_their_name_and_a_way_back(client):
    shift_db.add_leader("Maya", "4721", "lead")
    login_operator(client)
    page = client.get(f"/shift/development/{leader_id('Maya')}").data.decode()
    assert hero(page)[1] == "Maya’s course"
    assert 'href="/shift/development"' in page and "All leaders" in page


# --- Leadership Development: the Library (Hub pages) -------------------------

def test_library_lists_published_ld_pages_only(client):
    shift_db.add_leader("Maya", "4721", "lead")
    coaching = hub_page("Leadership Development", "Coaching in the moment", "Coaching",
                        summary="Catch it, name it, fix it.")
    required = hub_page("Leadership Development", "Running a great 1:1", "Coaching", required=1)
    draft = hub_page("Leadership Development", "Delegation draft", publish=False)
    foh = hub_page("FOH Operations", "Drive-thru basics")
    login_leader(client)
    page = client.get(f"/shift/development/{leader_id('Maya')}").data.decode()
    assert 'id="library"' in page and 'href="#library"' in page
    assert f'href="/hub/page/{coaching}"' in page and "Coaching in the moment" in page
    assert "Catch it, name it, fix it." in page
    assert f'href="/hub/page/{required}"' in page
    assert ">To read<" in page                        # Maya hasn't confirmed it yet
    assert f'href="/hub/page/{draft}"' not in page   # drafts never listed
    assert f'href="/hub/page/{foh}"' not in page     # only the LD category
    assert "Add a page" not in page                   # a lead isn't an editor
    # Once she's read it, the To read flag goes
    viewer = hub_db.Viewer("leader", leader_id("Maya"), "Maya", "lead")
    hub_db.ack(viewer, hub_db.get_page(required))
    assert ">To read<" not in client.get(f"/shift/development/{leader_id('Maya')}").data.decode()


def test_library_for_editors_offers_a_new_page(client):
    hub_page("Leadership Development", "Coaching in the moment")
    login_operator(client)
    page = client.get("/shift/development").data.decode()
    ld = category_id("Leadership Development")
    assert "Coaching in the moment" in page
    assert f'href="/hub/new?category={ld}"' in page


def test_library_empty_state(client):
    login_operator(client)
    page = client.get("/shift/development").data.decode()
    assert 'id="library"' in page and "No Leadership Development pages yet." in page


def test_library_skipped_when_the_category_is_gone_or_off(client):
    hub_page("Leadership Development", "Coaching in the moment")
    ld = hub_db.get_category(category_id("Leadership Development"))
    hub_db.save_category(ld["id"], ld["name"], ld["icon"], ld["blurb"], True, False,
                         False, ld["sort"], "Operator")          # deactivated
    login_operator(client)
    page = client.get("/shift/development").data.decode()
    assert 'id="library"' not in page and "Coaching in the moment" not in page
    assert "Mindset 101" in page                                  # the course still shows


def test_library_never_breaks_the_page_when_the_hub_is_down(client, monkeypatch):
    hub_page("Leadership Development", "Coaching in the moment")
    shift_db.add_leader("Maya", "4721", "lead")
    login_leader(client)
    hub_up = teamhub_nav.hub_up
    monkeypatch.setattr(teamhub_nav, "hub_up", lambda: False)
    resp = client.get(f"/shift/development/{leader_id('Maya')}")
    assert resp.status_code == 200 and b'id="library"' not in resp.data
    assert b"Mindset 101" in resp.data
    monkeypatch.setattr(teamhub_nav, "hub_up", hub_up)

    def boom(*a, **k):
        raise RuntimeError("hub db trouble")
    monkeypatch.setattr(hub_db, "pages_for_category", boom)
    resp = client.get(f"/shift/development/{leader_id('Maya')}")
    assert resp.status_code == 200 and b'id="library"' not in resp.data


def test_library_respects_hub_visibility(client):
    # Retiring a page takes it off the Library like everywhere else
    pid = hub_page("Leadership Development", "Old way of closing")
    hub_db.set_status(OPERATOR, hub_db.get_page(pid), "retired")
    shift_db.add_leader("Maya", "4721", "lead")
    login_leader(client)
    page = client.get(f"/shift/development/{leader_id('Maya')}").data.decode()
    assert "Old way of closing" not in page


def test_lead_still_cannot_open_someone_elses_development(client):
    shift_db.add_leader("Maya", "4721", "lead")
    shift_db.add_leader("Devon", "8888", "lead")
    login_leader(client)
    resp = client.get(f"/shift/development/{leader_id('Devon')}")
    assert resp.status_code == 302


# --- Lineups & Setups ---------------------------------------------------------

def test_lineup_lists_open_positions_first_in_red(client):
    login_operator(client)
    day = shift_db.today_local()
    positions = shift_db.active_positions()
    filled = positions[0]
    shift_db.assign_position(day, "lunch", filled["id"], "Marcus")
    page = client.get(f"/shift/lineup?date={day}&daypart=lunch").data.decode()
    eyebrow, headline = hero(page)
    assert eyebrow.startswith("Lineups &amp; Setups · ") and headline == "Lunch lineup"
    open_count = len(positions) - 1
    assert f"<b>1</b> of <b>{len(positions)}</b> positions filled · <b>{open_count}</b> open" in page
    assert f'<span class="badge b-red">{open_count} open</span>' in page
    assert page.count('<div class="who">Open — fill</div>') == open_count
    # Open section comes before the filled one; Marcus sits under "Who's where"
    assert page.index('id="open"') < page.index('id="filled"') < page.index('class="assignee">Marcus')
    assert f'<div class="slot open" id="pos-{filled["id"]}">' not in page
    # Same forms, same fields
    assert 'name="member_name"' in page and 'name="position_id"' in page
    assert 'action="/shift/lineup/assign"' in page and 'action="/shift/lineup/unassign/' in page


def test_lineup_empty_daypart_still_offers_copies(client):
    login_operator(client)
    day = shift_db.today_local()
    pos = shift_db.active_positions()[0]
    shift_db.assign_position(day, "breakfast", pos["id"], "Marcus")
    page = client.get(f"/shift/lineup?date={day}&daypart=lunch").data.decode()
    assert "Nobody's on it yet" in page
    assert "Copy from breakfast" in page and 'action="/shift/lineup/copy"' in page
    assert 'id="filled"' not in page


def test_lineup_day_label_names_the_weekday(client):
    login_operator(client)
    page = client.get("/shift/lineup?date=2026-10-23&daypart=dinner").data.decode()
    assert "Lineups &amp; Setups · Fri, Oct 23" in page
    page = client.get("/shift/lineup?date=2024-02-29&daypart=dinner").data.decode()
    assert "Thu, Feb 29" in page


# --- every restyled page: compact hero, no emoji chrome -------------------------

# Emoji that used to be page chrome (shout-out values keep theirs: that's content)
CHROME_EMOJI = ["🐮", "📁", "🏆", "⧉", "📌", "🌟", "🎓", "✅", "⬇", "↻", "📊", "📝", "📄", "🎬", "👤"]


def test_restyled_pages_use_the_compact_hero(client):
    shift_db.add_leader("Maya", "4721", "admin")
    goal = shift_db.create_goal("OSAT 80%", "", "OSAT", "%", 80, "up", "monthly", "",
                                "Operator")
    login_operator(client)
    expected = {
        "/shift/lineup": "Lineups &amp; Setups",
        "/shift/goals": "Goals",
        f"/shift/goals/{goal}": "Goals",
        "/shift/notes": "Shift notes",
        "/shift/announcements": "Announcements",
        "/shift/recovery": "Guest recovery",
        "/shift/shoutouts": "Shout-outs",
        "/shift/roster": "Team roster",
        "/shift/admin": "Admin",
        "/shift/development": "Leadership Development",
        f"/shift/development/{leader_id('Maya')}": "Leadership Development",
    }
    for path, eyebrow in expected.items():
        resp = client.get(path)
        assert resp.status_code == 200, path
        page = resp.data.decode()
        assert hero(page)[0].startswith(eyebrow), path
        for e in CHROME_EMOJI:
            assert e not in page, (path, e)
        assert 'class="page"' not in page, path          # the old h1.page is gone


def test_more_template_is_gone(client):
    assert not os.path.exists(os.path.join(ROOT, "templates", "shift", "more.html"))
    shift_db.add_leader("Maya", "4721", "lead")
    login_leader(client)
    page = client.get("/shift/more").data.decode()
    assert 'class="menu-big"' in page                      # the shared menu sheet


# --- Admin ----------------------------------------------------------------------

def test_admin_backups_anchor_is_operator_only(client):
    shift_db.add_leader("Ada", "2222", "admin")
    login_operator(client)
    page = client.get("/shift/admin").data.decode()
    assert '<section class="sec" id="backups">' in page
    assert 'href="/shift/admin/export"' in page and 'action="/shift/admin/import"' in page
    client.post("/shift/logout")
    login_leader(client, "Ada", "2222")
    page = client.get("/shift/admin").data.decode()
    assert 'id="backups"' not in page and "/shift/admin/export" not in page
    assert 'name="pin"' not in page.split('id="leaders"')[1].split("Add a leader")[0]  # no PIN resets


def test_admin_keeps_every_leader_form(client):
    shift_db.add_leader("Ada", "2222", "admin")
    login_operator(client)
    page = client.get("/shift/admin").data.decode()
    ada = leader_id("Ada")
    for action in (f"/shift/admin/leaders/{ada}/reset-pin", f"/shift/admin/leaders/{ada}/role",
                   f"/shift/admin/leaders/{ada}/toggle", "/shift/admin/leaders/add"):
        assert f'action="{action}"' in page, action
    assert "Make lead" in page and 'href="/shift/admin/templates"' in page


# --- Goals ------------------------------------------------------------------------

def test_goals_split_store_and_personal_with_a_lock_line(client):
    shift_db.add_leader("Maya", "4721", "lead")
    shift_db.create_goal("Drive-thru under 4:30", "", "Avg DT time", "sec", 270, "down",
                         "weekly", "", "Operator")
    shift_db.create_goal("Solo breakfast open", "", "", "", None, "up", "one-time", "",
                         "Operator", leader_id=leader_id("Maya"))
    login_leader(client)
    page = client.get("/shift/goals").data.decode()
    assert "<b>1</b> store goal" in page and "<b>1</b> personal" in page
    assert page.index("Store goals") < page.index("Drive-thru under 4:30") \
        < page.index("Personal goals") < page.index("Solo breakfast open")
    assert "Only you and the Operator see these." in page
    for field in ("title", "why", "metric", "unit", "target_value", "direction", "period",
                  "due_date", "leader_id"):
        assert f'name="{field}"' in page, field


def test_goal_detail_shows_latest_number_against_target(client):
    login_operator(client)
    gid = shift_db.create_goal("OSAT 80%", "Guests first.", "OSAT", "%", 80, "up",
                               "monthly", "2026-11-14", "Operator")
    shift_db.add_goal_update(gid, 77, "", "Maya")
    page = client.get(f"/shift/goals/{gid}").data.decode()
    assert hero(page)[1] == "OSAT 80%"
    assert "due Nov 14" in page and "target <b>80%</b>" in page
    assert '<span class="num">77<small>%</small></span>' in page
    assert "logged" in page and "by Maya" in page
    assert 'name="value"' in page and 'name="note"' in page and 'name="status"' in page


# --- Logbooks -----------------------------------------------------------------------

def test_notes_group_by_day_with_category_badges(client):
    login_operator(client)
    today = shift_db.today_local()
    shift_db.add_note(today, "lunch", "equipment", "Fryer 3 slow", "Daniel")
    shift_db.add_note("2026-10-21", "", "win", "Hit 3:58 in DT", "Maya")
    page = client.get("/shift/notes").data.decode()
    assert "<b>1</b> today" in page
    assert ">Today</h3>" in page and ">Wed, Oct 21</h3>" in page
    assert page.index("Fryer 3 slow") < page.index("Hit 3:58 in DT")
    assert '<span class="badge b-red">Equipment</span>' in page
    assert '<span class="badge b-green">Win</span>' in page
    assert "data-draft" in page and 'name="category"' in page and 'name="daypart"' in page


def test_announcements_split_new_and_seen(client):
    shift_db.add_leader("Maya", "4721", "lead")
    shift_db.add_announcement("Pretzel launch Monday", "Operator", True, "")
    shift_db.add_announcement("Schedules due Nov 1", "Operator", False, "2026-11-01")
    seen = next(a for a in shift_db.active_announcements() if "Schedules" in a["body"])
    shift_db.ack_announcement(seen["id"], "Maya")
    login_leader(client)
    page = client.get("/shift/announcements").data.decode()
    assert hero(page)[1] == "1 new for you"
    assert page.index(">New</h2>") < page.index("Pretzel launch Monday") \
        < page.index(">Seen</h2>") < page.index("Schedules due Nov 1")
    assert "From the Operator" in page and "Pinned" in page and "Until Nov 1" in page
    assert "New announcement" not in page                  # leads don't post
    client.post(f"/shift/announcements/{shift_db.active_announcements()[0]['id']}/ack")
    assert hero(client.get("/shift/announcements").data.decode())[1] == "You're all caught up"


def test_recovery_headline_follows_the_queue(client):
    login_operator(client)
    assert hero(client.get("/shift/recovery").data.decode())[1] == "All guests taken care of"
    rid = shift_db.add_recovery("Linda R.", "519-555-0142", "", "order-error",
                                "free-entree-card", "", True, "Maya")
    page = client.get("/shift/recovery").data.decode()
    assert hero(page)[1] == "1 guest to take care of"
    assert "Call back</span>" in page and 'href="tel:5195550142"' in page
    shift_db.set_recovery_contacted(rid, True, "Coming Saturday", "Maya")
    page = client.get("/shift/recovery").data.decode()
    assert hero(page)[1] == "1 guest coming back"
    assert "Waiting to come back (1)" in page and "Coming Saturday" in page


def test_shoutouts_show_value_badge_and_slack_marker(client):
    login_operator(client)
    sid = shift_db.add_shoutout("Kayla", "speed", "Kept DT moving", "Daniel")
    page = client.get("/shift/shoutouts").data.decode()
    assert f'id="shout-{sid}"' in page and "Kept DT moving" in page
    assert '<span class="badge b-yellow">' in page and "from Daniel" in page
    assert "On Slack" not in page
    shift_db.mark_shoutout_delivered(sid)
    assert "On Slack" in client.get("/shift/shoutouts").data.decode()


def test_roster_rows_keep_the_one_on_one_link_and_toggle(client):
    shift_db.add_member("Aron")
    aron = next(m for m in shift_db.roster() if m["name"] == "Aron")
    login_operator(client)
    page = client.get("/shift/roster").data.decode()
    assert hero(page)[1] == f"{len(shift_db.roster())} on the team"
    assert f'href="/shift/oneonone/member/{aron["id"]}"' in page
    assert f'action="/shift/roster/{aron["id"]}/toggle"' in page
    assert "Pull team from Slack" in page and "🤝" not in page


# --- the date helper -----------------------------------------------------------------

@pytest.mark.parametrize("day, label", [
    ("2026-10-23", "Fri, Oct 23"), ("2026-10-21", "Wed, Oct 21"), ("2024-02-29", "Thu, Feb 29"),
    ("2000-01-01", "Sat, Jan 1"), ("2026-03-01", "Sun, Mar 1"), ("2026-12-31", "Thu, Dec 31"),
])
def test_day_label(day, label):
    with cfa_app.app.test_request_context():
        tmpl = cfa_app.app.jinja_env.from_string(
            '{% from "shift/_leader_ui.html" import day_label %}{{ day_label(d) }}')
        assert tmpl.render(d=day).strip() == label
