"""Systems & Procedures in the Team Hub look: the member Home (required
reading card), the /hub/library landing, and the restyled Hub pages —
real data only, nothing a viewer may not see, and no scripts."""

import re
from html import unescape

import pytest
from markupsafe import escape

import app as cfa_app
import hub
import hub_db
import shift_app
import shift_db

CHECKS = {"check_people": "1", "check_law": "1", "check_counsel": "1"}


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


def make_leader(client, name="Maya", pin="4721", role="lead"):
    login_operator(client)
    client.post("/shift/admin/leaders/add", data={"name": name, "pin": pin, "role": role})
    client.post("/shift/logout")


def add_member(name="Sam Patel", code="1042"):
    shift_db.add_member(name)
    member = next(m for m in shift_db.roster() if m["name"] == name)
    assert hub_db.grant(member["id"], code, "Operator") is None
    return member


def login_member(client, name="Sam", code="1042"):
    return client.post("/hub/login", data={"name": name, "code": code})


def category_id(name):
    return next(c["id"] for c in hub_db.categories() if c["name"] == name)


def rev(page_id):
    return str(hub_db.get_page(page_id)["rev"])


def new_page(client, category="FOH Operations", title="Drive-thru basics",
             body="# Steps\n- Greet the guest", publish=True, section="Training", **extra):
    data = {"category_id": category_id(category), "title": title, "body": body,
            "summary": "", "section": section, **extra}
    resp = client.post("/hub/new", data=data)
    assert resp.status_code == 302, resp.data
    page_id = int(resp.headers["Location"].rsplit("/", 1)[1])
    if publish:
        client.post(f"/hub/page/{page_id}/publish", data=dict(CHECKS, rev=rev(page_id)))
    return page_id


def member_viewer(member):
    return hub_db.Viewer("member", member["id"], member["name"])


def text(resp):
    return resp.data.decode()


# --- required_progress ----------------------------------------------------------

def test_required_progress_counts_what_each_person_can_see(client):
    login_operator(client)
    a = new_page(client, title="Sanitizer", required="1")
    b = new_page(client, category="HR", title="Uniform", required="1")
    new_page(client, title="Cash drawer", required="1", leaders_only="1")   # leaders only
    new_page(client, title="Not required")
    new_page(client, title="Draft", required="1", publish=False)              # not live
    client.post("/shift/logout")
    sam = add_member()
    viewer = member_viewer(sam)
    assert hub_db.required_progress(viewer) == (0, 2)
    assert hub_db.ack(viewer, hub_db.get_page(a))
    assert hub_db.required_progress(viewer) == (1, 2)
    done, total = hub_db.required_progress(viewer)
    assert total - done == len(hub_db.to_read(viewer))
    # a leader also has the leaders-only page; the Operator confirms nothing
    assert shift_db.add_leader("Ava", "5555", "lead") is None
    leader_id = next(l["id"] for l in shift_db.leaders() if l["name"] == "Ava")
    assert hub_db.required_progress(hub_db.Viewer("leader", leader_id, "Ava", "lead")) == (0, 3)
    assert hub_db.required_progress(hub_db.Viewer("operator", None, "Operator", "admin")) == (0, 0)
    # a change everyone must re-read takes the page off "done"
    login_operator(client)
    page = hub_db.get_page(a)
    client.post(f"/hub/page/{a}/edit", data={
        "category_id": page["category_id"], "title": page["title"], "body": "New way.",
        "section": page["section"], "required": "1", "reread": "1", "rev": rev(a)})
    assert hub_db.required_progress(viewer) == (0, 2)
    assert {p["id"] for p in hub_db.to_read(viewer)} == {a, b}


# --- member Home ----------------------------------------------------------------

def test_member_home_reading_card_from_real_data(client):
    login_operator(client)
    first = new_page(client, title="Taking an order", required="1")
    second = new_page(client, category="HR", title="Calling in sick", section="How-tos",
                      required="1")
    new_page(client, category="BOH Operations", title="Filtering the fryers", section="Cooking")
    new_page(client, title="Leaders cash page", required="1", leaders_only="1")
    client.post("/shift/logout")
    sam = add_member()
    hub_db.ack(member_viewer(sam), hub_db.get_page(second))
    login_member(client)
    html = text(client.get("/hub/"))
    assert "Hi Sam. Here's how we do it." in html
    assert "Your required reading" in html and "1 of 2 done" in html
    assert "Up next" in html
    # the next page is the top of To read, and the summary row names what's read
    up_next = hub_db.to_read(member_viewer(sam))[0]
    assert up_next["id"] == first
    assert f'class="btn btn-primary block" href="/hub/page/{first}">Read the next page' in html
    assert "1 already read: Calling in sick" in html
    assert ("Required reading counts as paid time. Read it on shift, or tell a leader how "
            "long it took you.") in html
    # tiles with drawings, a Search tile, recently updated, the lead card
    assert 'href="/hub/search"' in html and "Search all pages" in html
    assert "Filtering the fryers" in html and "BOH Operations · Cooking" in html
    assert 'href="/apply"' in html and "Want to lead one day?" in html
    # never a leaders-only page, never Leadership Development, no reading minutes
    assert "Leaders cash page" not in html and "Leadership Development" not in html
    assert "min read" not in html and "minutes" not in html


def test_member_home_all_caught_up_and_nothing_required(client):
    login_operator(client)
    pid = new_page(client, title="Sanitizer", required="1")
    client.post("/shift/logout")
    sam = add_member()
    login_member(client)
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    html = text(client.get("/hub/"))
    assert "You're all caught up" in unescape(html)
    assert "1 of 1 done" in html and "Read the next page" not in html
    # with nothing required at all there's no card
    login_operator(client)
    client.post(f"/hub/page/{pid}/status", data={"status": "retired"})
    client.post("/shift/logout")
    login_member(client)
    html = text(client.get("/hub/"))
    assert "Your required reading" not in html and " done</span>" not in html
    assert "Read the next page" not in html and sam


# --- /hub/library ---------------------------------------------------------------

def test_library_tiles_lock_line_and_one_room_for_leadership(client):
    login_operator(client)
    new_page(client, category="HR", title="Uniform", section="Policies", required="1")
    new_page(client, category="Leadership Development", title="Coaching", section="Leadership")
    client.post("/shift/logout")
    make_leader(client)
    client.post("/shift/login", data={"name": "Maya", "pin": "4721"})
    html = text(client.get("/hub/library"))
    assert "Here's how we do it." in html
    assert 'placeholder="Search 2 pages"' in html
    assert "1 page · policy" in html                               # HR tile
    assert "1 to read" in html
    # Leadership Development: cool tile, lock badge, opens the course + library
    ld = re.search(r'<a class="tile" href="([^"]+)"><span class="tile-art cool">.*?'
                   r'Leadership Development', html, re.S)
    assert ld and ld.group(1) == "/shift/development"
    assert "1 page + your course" in html
    assert ("Team members see HR, FOH Operations and BOH Operations. Leadership Development "
            "and leaders-only pages stay with leaders.") in html
    assert "Editor tools" not in html                              # a lead isn't an editor
    client.post("/shift/logout")

    sam = add_member()
    login_member(client)
    html = text(client.get("/hub/library"))
    assert "Leadership Development" not in html and "/shift/" not in html
    assert "stay with leaders" not in html                          # members never see locks
    assert "Coaching" not in html and "Leadership" not in html.split("Browse by section")[-1]
    assert sam


def test_library_sections_only_from_pages_the_viewer_can_open(client):
    login_operator(client)
    new_page(client, title="Greeting guests", section="Hospitality")
    new_page(client, title="Cash drawer", section="Money room", leaders_only="1")
    new_page(client, title="Unpublished", section="Secret plans", publish=False)
    client.post("/shift/logout")
    add_member()
    login_member(client)
    html = text(client.get("/hub/library"))
    assert "Hospitality" in html
    assert "Money room" not in html and "Secret plans" not in html
    # a section's pages across the categories; a made-up section is ignored
    picked = text(client.get("/hub/library?section=Hospitality"))
    assert 'class="chip on"' in picked and "Greeting guests" in picked
    assert 'class="chip on"' not in text(client.get("/hub/library?section=Money+room"))
    assert "Cash drawer" not in text(client.get("/hub/library?section=Money+room"))
    assert "Greeting guests" in text(client.get("/hub/library?view=updated"))


def test_library_editor_tools_for_editors_only(client):
    make_leader(client, "Ava", "5555", role="admin")
    client.post("/shift/login", data={"name": "Ava", "pin": "5555"})
    new_page(client, title="Draft one", publish=False)
    html = text(client.get("/hub/library"))
    assert "Editor tools" in html and "Manage pages" in html
    for anchor in ("#drafts", "#reviews", "#import"):
        assert f'href="/hub/manage{anchor}"' in html
    assert "1 waiting" in html
    assert "Only the Operator publishes HR policy. Each page shows who has read it." in html
    manage = text(client.get("/hub/manage"))
    for anchor in ('id="drafts"', 'id="reviews"', 'id="counsel"', 'id="import"',
                   'id="categories"'):
        assert anchor in manage
    # the Import a Word doc card posts what /hub/import reads
    form = re.search(r'<form method="post" action="/hub/import"[^>]*>(.*?)</form>', manage, re.S)
    assert form and 'name="category_id"' in form.group(1) and 'name="file"' in form.group(1)
    assert 'name="section"' in form.group(1)


# --- the page and the other Hub screens ------------------------------------------

def test_page_view_confirm_button_and_read_state(client):
    login_operator(client)
    pid = new_page(client, title="Sanitizer", required="1")
    client.post("/shift/logout")
    add_member()
    login_member(client)
    html = text(client.get(f"/hub/page/{pid}"))
    assert 'href="/hub/library">Procedures</a>' in html                       # crumbs
    assert f'href="/hub/c/{category_id("FOH Operations")}">FOH Operations</a>' in html
    assert '<section class="hero compact">' in html and 'class="card doc"' in html
    form = re.search(rf'<form method="post" action="/hub/page/{pid}/ack"[^>]*>(.*?)</form>',
                     html, re.S)
    assert form and 'name="version" value="1"' in form.group(1)
    assert 'class="btn btn-primary block" type="submit"' in form.group(1)
    assert str(escape(hub_db.ACK_WORDING)) in form.group(1)
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    html = text(client.get(f"/hub/page/{pid}"))
    assert "/ack" not in html and "You've read this" in html


def test_to_read_shows_progress(client):
    login_operator(client)
    a = new_page(client, title="One", required="1")
    new_page(client, title="Two", required="1")
    client.post("/shift/logout")
    add_member()
    login_member(client)
    client.post(f"/hub/page/{a}/ack", data={"version": "1"})
    html = text(client.get("/hub/to-read"))
    assert "1 page to read" in html and "1 of 2 done" in html
    assert "Required reading counts as paid time." in html


def test_hub_pages_run_no_scripts_and_carry_no_old_names(client):
    """Every Hub screen, as the Operator (who sees the most): no script, no
    inline handlers, no "Shift Lead", and no emoji used as buttons."""
    login_operator(client)
    pid = new_page(client, category="HR", title="Harassment policy", required="1",
                   annual_review="1")
    draft = new_page(client, title="Draft", publish=False)
    hub_db.add_attachment(hub_db.Viewer("operator", None, "Operator", "admin"),
                          hub_db.get_page(draft), "a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    file_id = hub_db.attachments(draft)[0]["id"]
    add_member()
    paths = ["/hub/library", "/hub/library?view=updated", "/hub/search", "/hub/search?q=policy",
             "/hub/to-read", "/hub/me", f"/hub/c/{category_id('HR')}", f"/hub/page/{pid}",
             f"/hub/page/{draft}", "/hub/new", f"/hub/page/{pid}/edit",
             f"/hub/page/{pid}/history", f"/hub/page/{pid}/v/1", f"/hub/page/{pid}/readers",
             "/hub/manage", "/hub/people", f"/hub/file/{file_id}/remove"]
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, path
        html = text(resp)
        assert "<script" not in html, path
        assert not re.search(r"\son[a-z]+=", html), path
        assert "Shift Lead" not in html, path
        # (category icons are the Operator's own content and still show)
        for emoji in ("✏️", "🕘", "👥", "📎", "🗑", "🗄", "♻️", "👀", "🛠", "🧢", "⬇"):
            assert emoji not in html, (path, emoji)
    client.post("/shift/logout")
    login_member(client)
    html = text(client.get("/hub/"))
    assert "<script" not in html and "Shift Lead" not in html


def test_restyled_forms_post_the_same_fields(client):
    login_operator(client)
    pid = new_page(client, category="HR", title="Harassment policy", required="1",
                   annual_review="1")
    html = text(client.get(f"/hub/page/{pid}"))
    upload = re.search(rf'action="/hub/page/{pid}/files"[^>]*>(.*?)</form>', html, re.S).group(1)
    for field in ('name="file"', 'name="alt"', 'name="rev"', 'name="change_note"',
                  'name="reread"', 'name="check_people"', 'name="check_law"',
                  'name="check_counsel"'):
        assert field in upload, field
    assert f'action="/hub/page/{pid}/review"' in html and f'action="/hub/page/{pid}/counsel"' in html
    assert f'action="/hub/page/{pid}/status"' in html
    edit = text(client.get(f"/hub/page/{pid}/edit"))
    for field in ("category_id", "section", "title", "summary", "body", "keywords",
                  "leaders_only", "required", "annual_review", "rev", "change_note", "reread"):
        assert f'name="{field}"' in edit, field
    assert 'name="action" value="preview"' in edit and 'name="action" value="save"' in edit
