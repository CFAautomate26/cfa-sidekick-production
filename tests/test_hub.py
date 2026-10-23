"""Team Hub (/hub): sign-in, who sees what, publishing, read confirmations,
files, people, and how the Hub rides along with backups."""

import io
import json
import os
import re
import zipfile

import pytest

import app as cfa_app
import hub
import hub_db
import hub_text
import shift_app
import shift_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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


def login_leader(client, name="Maya", pin="4721"):
    return client.post("/shift/login", data={"name": name, "pin": pin})


def add_member(name="Sam Patel", code="1042"):
    shift_db.add_member(name)
    member = next(m for m in shift_db.roster() if m["name"] == name)
    assert hub_db.grant(member["id"], code, "Operator") is None
    return member


def login_member(client, name="Sam", code="1042"):
    return client.post("/hub/login", data={"name": name, "code": code})


def category_id(name):
    return next(c["id"] for c in hub_db.categories() if c["name"] == name)


def new_page(client, category="FOH Operations", title="Drive-thru basics",
             body="# Steps\n- Greet the guest", publish=True, **extra):
    data = {"category_id": category_id(category), "title": title, "body": body,
            "summary": "", "section": "Training", **extra}
    resp = client.post("/hub/new", data=data)
    assert resp.status_code == 302, resp.data
    page_id = int(resp.headers["Location"].rsplit("/", 1)[1])
    if publish:
        client.post(f"/hub/page/{page_id}/publish", data=dict(CHECKS, rev=rev(page_id)))
    return page_id


CHECKS = {"check_people": "1", "check_law": "1", "check_counsel": "1"}


def rev(page_id):
    """The page revision a freshly loaded form would carry."""
    return str(hub_db.get_page(page_id)["rev"])


# --- boot / sign-in ----------------------------------------------------------

def test_seeded_categories(client):
    cats = hub_db.categories(include_inactive=True)
    assert [c["name"] for c in cats] == ["HR", "FOH Operations", "BOH Operations",
                                          "Leadership Development"]
    by_name = {c["name"]: c for c in cats}
    assert by_name["HR"]["policy"] == 1
    assert by_name["Leadership Development"]["leaders_only"] == 1
    shift_db.init_db()                      # seeded once, not again
    assert len(hub_db.categories(include_inactive=True)) == 4


def test_requires_sign_in(client):
    for path in ["/hub/", "/hub/c/1", "/hub/search?q=x", "/hub/to-read", "/hub/me",
                 "/hub/manage", "/hub/people", "/hub/new", "/hub/page/1", "/hub/file/1"]:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert "/hub/login" in resp.headers["Location"]
    assert client.get("/hub/login").status_code == 200
    assert client.get("/hub/manifest.webmanifest").status_code == 200
    assert client.get("/hub/icon.svg").status_code == 302


def test_member_sign_in_first_name_or_full_name(client):
    add_member("Sam Patel", "1042")
    assert login_member(client, "sam", "1042").status_code == 302
    assert client.get("/hub/").status_code == 200
    client.post("/hub/logout")
    assert login_member(client, "Sam Patel", " 10 42 ").status_code == 302
    client.post("/hub/logout")
    resp = login_member(client, "Sam", "9999")
    assert resp.status_code == 401
    assert b"don&#39;t match" in resp.data or b"don't match" in resp.data
    assert client.get("/hub/").status_code == 302


def test_member_cannot_use_shift_app(client):
    add_member()
    login_member(client)
    resp = client.get("/shift/", follow_redirects=True)
    assert resp.request.path == "/hub/" and b"That part of Team Hub is for leaders" in resp.data
    # every leader page, not just Today (walk the whole URL map)
    for rule in cfa_app.app.url_map.iter_rules():
        path = rule.rule
        if (not path.startswith("/shift") or "GET" not in rule.methods
                or rule.endpoint in ("shift.login", "shift.logout", "shift.manifest", "shift.icon")):
            continue
        path = re.sub(r"<(?:int:)?\w+>", "1", path)
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/"), path


def test_sign_in_throttle(client):
    add_member()
    for _ in range(5):
        assert login_member(client, "Sam", "0000").status_code == 401
    resp = login_member(client, "Sam", "1042")         # right number, still locked
    assert resp.status_code == 401 and b"Too many tries" in resp.data


def test_revoke_or_change_signs_member_out(client):
    member = add_member()
    login_member(client)
    assert client.get("/hub/").status_code == 200
    hub_db.grant(member["id"], "2001", "Operator")     # number changed
    assert client.get("/hub/").status_code == 302
    login_member(client, "Sam", "2001")
    assert client.get("/hub/").status_code == 200
    hub_db.revoke(member["id"], "Operator")
    assert client.get("/hub/").status_code == 302
    hub_db.grant(member["id"], "2001", "Operator")
    login_member(client, "Sam", "2001")
    shift_db.set_member_active(member["id"], False)    # left the roster
    assert client.get("/hub/").status_code == 302


def test_leader_session_carries_over_and_next_returns_to_hub(client):
    make_leader(client)
    resp = client.post("/shift/login?next=/hub/search", data={"name": "Maya", "pin": "4721"})
    assert resp.headers["Location"].endswith("/hub/search")
    assert client.get("/hub/").headers["Location"].endswith("/shift/")   # a leader's home is Today
    assert client.get("/hub/library").status_code == 200
    assert b"Maya" in client.get("/hub/me").data


def test_operator_pin_change_signs_operator_out(client, monkeypatch):
    login_operator(client)
    assert client.get("/hub/library").status_code == 200
    assert client.get("/shift/").status_code == 200
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "1234")
    assert "/hub/login" in client.get("/hub/library").headers["Location"]
    login_operator(client)
    assert client.get("/shift/").status_code == 302


def test_security_headers(client):
    resp = client.get("/hub/login")
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "script-src" not in resp.headers["Content-Security-Policy"]


# --- who sees what -------------------------------------------------------------

def test_drafts_and_leader_pages_are_hidden(client):
    login_operator(client)
    draft = new_page(client, title="Secret draft", publish=False)
    leaders = new_page(client, title="Leaders page", leaders_only="1")
    ldp = new_page(client, category="Leadership Development", title="LDP week 1")
    public = new_page(client, title="Everyone page")
    client.post("/shift/logout")

    add_member()
    login_member(client)
    assert client.get(f"/hub/page/{public}").status_code == 200
    for pid in (draft, leaders, ldp):
        assert client.get(f"/hub/page/{pid}").status_code == 404
    home = client.get("/hub/").data
    assert b"Leadership Development" not in home
    assert client.get(f"/hub/c/{category_id('Leadership Development')}").status_code == 404
    search = client.get("/hub/search?q=page").data
    assert b"Everyone page" in search and b"Leaders page" not in search
    client.post("/hub/logout")

    make_leader(client)
    login_leader(client)
    assert client.get(f"/hub/page/{leaders}").status_code == 200
    assert client.get(f"/hub/page/{ldp}").status_code == 200
    assert client.get(f"/hub/page/{draft}").status_code == 404     # leads aren't editors
    assert client.get("/hub/new").status_code == 302


def test_retired_page_hidden_but_kept(client):
    login_operator(client)
    pid = new_page(client, title="Old way")
    client.post(f"/hub/page/{pid}/status", data={"status": "retired"})
    client.post("/shift/logout")
    add_member()
    login_member(client)
    assert client.get(f"/hub/page/{pid}").status_code == 404
    assert hub_db.get_page(pid)["status"] == "retired"


# --- publishing ----------------------------------------------------------------

def test_hr_publish_is_operator_only_with_checks(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, category="HR", title="Dress code", publish=False)
    client.post(f"/hub/page/{pid}/publish", data=dict(CHECKS, rev=rev(pid)))
    assert hub_db.get_page(pid)["status"] == "draft"
    client.post("/shift/logout")

    login_operator(client)
    client.post(f"/hub/page/{pid}/publish", data={"check_people": "1", "rev": rev(pid)})
    assert hub_db.get_page(pid)["status"] == "draft"                     # checks missing
    client.post(f"/hub/page/{pid}/publish", data=dict(CHECKS, rev=rev(pid)))
    page = hub_db.get_page(pid)
    assert page["status"] == "published" and page["version"] == 1
    assert hub_db.versions(pid)[0]["reviewed_by"] == "Operator"
    client.post("/shift/logout")

    login_leader(client, "Ava", "5555")                 # admin can't edit it now
    resp = client.post(f"/hub/page/{pid}/edit", data={
        "category_id": page["category_id"], "title": "Dress code", "body": "changed",
        "rev": rev(pid)})
    assert resp.status_code == 302
    assert hub_db.get_page(pid)["body"] != "changed"
    assert b"Only the Operator" in client.get(f"/hub/page/{pid}").data


def test_admin_cannot_move_published_page_into_hr(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, title="Breaks")
    resp = client.post(f"/hub/page/{pid}/edit", data={
        "category_id": category_id("HR"), "title": "Breaks", "body": "# Steps\n- Greet the guest",
        "rev": rev(pid)})
    assert resp.status_code == 400
    assert hub_db.get_page(pid)["category_id"] == category_id("FOH Operations")


def test_counsel_flag_blocks_publishing_until_operator_clears(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, title="Scheduling rules", publish=False, needs_counsel="1")
    client.post(f"/hub/page/{pid}/publish", data={"rev": rev(pid)})
    assert hub_db.get_page(pid)["status"] == "draft"
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": "looked fine"})
    assert hub_db.get_page(pid)["needs_counsel"] == 1   # only the Operator clears
    client.post("/shift/logout")
    login_operator(client)
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": ""})
    assert hub_db.get_page(pid)["needs_counsel"] == 1   # a note is required
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": "Counsel OK 10/20"})
    client.post(f"/hub/page/{pid}/publish", data={"rev": rev(pid)})
    assert hub_db.get_page(pid)["status"] == "published"


def test_versions_and_rereads(client):
    login_operator(client)
    pid = new_page(client, title="Sanitizer", body="Use 200 ppm.", required="1")
    client.post("/shift/logout")
    add_member()
    login_member(client)
    assert b"Sanitizer" in client.get("/hub/to-read").data
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    assert b"all caught up" in client.get("/hub/to-read").data
    client.post("/hub/logout")

    login_operator(client)
    base = {"category_id": category_id("FOH Operations"), "title": "Sanitizer",
            "section": "Training", "required": "1"}
    client.post(f"/hub/page/{pid}/edit", data=dict(
        base, body="Use 200 ppm quat.", rev=rev(pid), reread="0", change_note="typo"))
    page = hub_db.get_page(pid)
    assert (page["version"], page["ack_version"]) == (2, 1)       # small fix
    stale_rev = rev(pid)
    client.post(f"/hub/page/{pid}/edit", data=dict(
        base, body="Use 400 ppm quat.", rev=rev(pid), reread="1",
        change_note="New strength"))
    page = hub_db.get_page(pid)
    assert (page["version"], page["ack_version"]) == (3, 3)
    stale = client.post(f"/hub/page/{pid}/edit", data=dict(
        base, body="Something else", rev=stale_rev, reread="0"))
    assert stale.status_code == 409 and hub_db.get_page(pid)["version"] == 3
    assert b"Something else" in stale.data                       # their text survives
    assert [v["version"] for v in hub_db.versions(pid)] == [3, 2, 1]
    assert hub_db.get_version(pid, 1)["body"] == "Use 200 ppm."
    client.post("/shift/logout")

    login_member(client)
    resp = client.get(f"/hub/page/{pid}")
    assert b"changed since you read it" in resp.data and b"New strength" in resp.data
    client.post(f"/hub/page/{pid}/ack", data={"version": "2"})     # stale tab
    assert b"Sanitizer" in client.get("/hub/to-read").data
    client.post(f"/hub/page/{pid}/ack", data={"version": "3"})
    assert b"all caught up" in client.get("/hub/to-read").data
    client.post("/hub/logout")

    login_operator(client)
    resp = client.get(f"/hub/page/{pid}/readers")
    assert b"Sam Patel" in resp.data and b"v3" in resp.data
    raw = client.get(f"/hub/page/{pid}/readers.csv").data
    assert raw.startswith(b"\xef\xbb\xbf")               # BOM: Excel reads "José" right
    csv_text = raw.decode("utf-8-sig")
    assert csv_text.count("Sam Patel") == 2 and hub_db.ACK_WORDING in csv_text


def test_settings_only_change_makes_no_version(client):
    login_operator(client)
    pid = new_page(client, title="Uniform", body="Hat on.")
    client.post(f"/hub/page/{pid}/edit", data={
        "category_id": category_id("FOH Operations"), "title": "Uniform", "body": "Hat on.",
        "section": "Training", "keywords": "hat cap", "rev": rev(pid)})
    page = hub_db.get_page(pid)
    assert page["version"] == 1 and page["keywords"] == "hat cap"


def test_delete_only_unpublished_drafts(client):
    login_operator(client)
    draft = new_page(client, title="Scratch", publish=False)
    live = new_page(client, title="Live")
    client.post(f"/hub/page/{live}/delete", data={"confirm": "1"})
    assert hub_db.get_page(live)
    client.post(f"/hub/page/{draft}/delete", data={"confirm": "1"})
    assert hub_db.get_page(draft) is None


def test_page_text_is_escaped(client):
    login_operator(client)
    pid = new_page(client, title="<b>Hi</b>", body="<script>alert(1)</script>\n[x](javascript:1)")
    resp = client.get(f"/hub/page/{pid}")
    assert b"<script>alert" not in resp.data and b"&lt;script&gt;" in resp.data
    assert b'href="javascript' not in resp.data
    assert b"<b>Hi</b>" not in resp.data


def test_search_ranks_and_marks(client):
    login_operator(client)
    new_page(client, title="Fryer cleaning", body="Drain the oil nightly.")
    new_page(client, title="Closing", body="Then clean the fryer <b>baskets</b>.")
    resp = client.get("/hub/search?q=fryer").data.decode()
    assert resp.index("Fryer cleaning") < resp.index("Closing")
    assert "<mark>fryer</mark>" in resp and "&lt;b&gt;baskets" in resp


def test_ack_needs_required_published_page(client):
    login_operator(client)
    pid = new_page(client, title="Optional read")
    client.post("/shift/logout")
    add_member()
    login_member(client)
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    with shift_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hub_acks").fetchone()[0] == 0


# --- files -----------------------------------------------------------------------

def _docx_bytes(body_xml):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types><Override ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>')
    return buf.getvalue()


def upload(client, pid, data, name, **form):
    return client.post(f"/hub/page/{pid}/files", data=dict(
        form, file=(io.BytesIO(data), name), rev=rev(pid)), content_type="multipart/form-data")


def test_upload_serve_and_remove_files_on_a_draft(client):
    login_operator(client)
    pid = new_page(client, title="Fryer", body="Hang it like this:\n[photo: 1]", publish=False)
    assert upload(client, pid, PNG, "basket.png", alt="Basket hung").status_code == 302
    upload(client, pid, PDF, "guide.pdf")
    bad = client.post(f"/hub/page/{pid}/files", data={
        "file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.png"), "rev": rev(pid)},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Only PDFs" in bad.data
    files = hub_db.attachments(pid)
    assert [f["kind"] for f in files] == ["png", "pdf"]
    page = client.get(f"/hub/page/{pid}").data.decode()
    assert f'<img src="/hub/file/{files[0]["id"]}" alt="Basket hung"' in page
    png = client.get(f"/hub/file/{files[0]['id']}")
    assert png.status_code == 200 and png.mimetype == "image/png"
    assert png.headers["X-Content-Type-Options"] == "nosniff"
    assert png.headers["Cache-Control"] == "private, no-store"
    path = hub_db.file_path(files[1])
    assert client.get(f"/hub/file/{files[1]['id']}/remove").status_code == 200
    client.post(f"/hub/file/{files[1]['id']}/remove", data={"rev": rev(pid)})
    assert len(hub_db.attachments(pid)) == 1
    assert not os.path.exists(path)                      # a draft's file just goes


def test_file_changes_on_a_live_page_make_versions(client):
    login_operator(client)
    pid = new_page(client, title="Harassment policy", category="HR", body="Policy text.",
                   required="1")
    upload(client, pid, PDF, "policy-2026.pdf")                 # policy page: checks needed
    assert hub_db.attachments(pid) == []
    upload(client, pid, PDF, "policy-2026.pdf", **CHECKS)
    page = hub_db.get_page(pid)
    assert (page["version"], page["ack_version"]) == (2, 1)      # a small fix: no re-read
    old = hub_db.attachments(pid)[0]
    client.post("/shift/logout")

    add_member()
    login_member(client)
    client.post(f"/hub/page/{pid}/ack", data={"version": "2"})
    client.post("/hub/logout")

    login_operator(client)
    stale_rev = rev(pid)
    client.post(f"/hub/file/{old['id']}/remove", data=dict(CHECKS, rev=stale_rev, reread="1",
                                                         change_note="New policy file"))
    upload(client, pid, PDF + b"%v2", "policy-2027.pdf", reread="1", **CHECKS)
    page = hub_db.get_page(pid)
    assert page["version"] == 4 and page["ack_version"] == 4
    assert hub_db.get_attachment(old["id"])["removed_at"]          # kept for History
    assert os.path.exists(hub_db.file_path(old))
    v2 = hub_db.get_version(pid, 2)
    assert [f["name"] for f in v2["files"]] == ["policy-2026.pdf"]
    assert v2["reviewed_by"] == "Operator"
    # the stale remove form is refused once the page has moved on
    resp = client.post(f"/hub/file/{hub_db.attachments(pid)[0]['id']}/remove",
                       data=dict(CHECKS, rev=stale_rev))
    assert len(hub_db.attachments(pid)) == 1
    client.post("/shift/logout")

    login_member(client)
    assert b"Harassment policy" in client.get("/hub/to-read").data     # re-read needed
    assert client.get(f"/hub/file/{old['id']}").status_code == 404     # History only
    client.post("/hub/logout")

    login_operator(client)
    assert client.get(f"/hub/file/{old['id']}").status_code == 200
    client.post(f"/hub/file/{old['id']}/purge", data={"confirm": "1"})
    assert not os.path.exists(hub_db.file_path(old))
    assert client.get(f"/hub/file/{old['id']}").status_code == 404
    assert b"deleted for good" in client.get(f"/hub/page/{pid}/v/2").data


def test_photo_numbers_never_shift(client):
    login_operator(client)
    pid = new_page(client, title="Basket", body="Step 1\n[photo: 1]\nStep 2\n[photo: 2]")
    upload(client, pid, PNG, "one.png", alt="first")
    upload(client, pid, PNG + b"2", "two.png", alt="second")
    first, second = hub_db.attachments(pid)
    assert (first["photo_no"], second["photo_no"]) == (1, 2)
    client.post(f"/hub/file/{first['id']}/remove", data={"rev": rev(pid)})
    html = client.get(f"/hub/page/{pid}").data.decode()
    assert 'alt="first"' not in html and 'alt="second"' in html
    assert html.index("Step 2") < html.index('alt="second"')        # still under step 2
    upload(client, pid, PNG + b"3", "three.png")
    assert hub_db.attachments(pid)[-1]["photo_no"] == 3              # never reused
    assert hub_db.get_page(pid)["version"] == 5          # each file change on a live page
    old_view = client.get(f"/hub/page/{pid}/v/3").data.decode()      # v3 had both photos
    assert 'alt="first"' in old_view and 'alt="second"' in old_view


def test_files_follow_page_visibility(client):
    login_operator(client)
    pid = new_page(client, title="Leaders file", leaders_only="1")
    upload(client, pid, PDF, "x.pdf")
    fid = hub_db.attachments(pid)[0]["id"]
    client.post("/shift/logout")
    add_member()
    login_member(client)
    assert client.get(f"/hub/file/{fid}").status_code == 404


def test_office_files_need_no_macros():
    good = _docx_bytes("<w:p><w:r><w:t>Hi</w:t></w:r></w:p>")
    assert hub_db.sniff_kind(good) == "docx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<Types ContentType="application/vnd.ms-word.'
                                          'document.macroEnabled.main+xml"/>')
        z.writestr("word/vbaProject.bin", "x")
    assert hub_db.sniff_kind(buf.getvalue()) is None
    assert hub_db.sniff_kind(b"MZ\x90\x00") is None


def test_word_import_makes_a_draft(client):
    login_operator(client)
    data = _docx_bytes(
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Opening the store</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Unlock the back door.</w:t></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden note</w:t></w:r></w:p>')
    resp = client.post("/hub/import", data={
        "category_id": str(category_id("BOH Operations")),
        "file": (io.BytesIO(data), "opening.docx")}, content_type="multipart/form-data")
    assert resp.status_code == 302 and "/edit" in resp.headers["Location"]
    page = hub_db.get_page(int(resp.headers["Location"].split("/")[-2]))
    assert page["title"] == "Opening the store" and page["status"] == "draft"
    assert page["body"] == "Unlock the back door."


# --- people ------------------------------------------------------------------------

def test_people_page_is_operator_only(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    assert client.get("/hub/people").status_code == 302
    client.post("/shift/logout")
    login_operator(client)
    assert client.get("/hub/people").status_code == 200


def test_grant_rejects_duplicate_numbers(client):
    add_member("Sam Patel", "1042")
    shift_db.add_member("Jordan Lee")
    jordan = next(m for m in shift_db.roster() if m["name"] == "Jordan Lee")
    assert "already set for Sam Patel" in hub_db.grant(jordan["id"], "1042", "Operator")
    assert hub_db.grant(jordan["id"], "12", "Operator")             # too short


def test_bulk_grant(client):
    shift_db.add_member("Jordan Lee")
    shift_db.add_member("Jordan Smith")
    shift_db.add_member("Priya Shah")
    out = hub_db.bulk_grant("Priya, 3001\nJordan, 3002\nJordan Smith\t3003\n"
                            "New Person, 3004\nbad line\nPriya Shah, x", "Operator",
                            add_missing=False)
    assert sorted(out["granted"]) == ["Jordan Smith", "Priya Shah"]
    assert out["ambiguous"] == ["Jordan"] and out["unmatched"] == ["New Person"]
    assert len(out["errors"]) == 2
    out = hub_db.bulk_grant("New Person, 3004", "Operator", add_missing=True)
    assert out["added"] == ["New Person"] and out["granted"] == ["New Person"]


def test_codes_never_echoed(client):
    login_operator(client)
    shift_db.add_member("Priya Shah")
    resp = client.post("/hub/people/bulk", data={"lines": "Priya Shah, 777001"},
                       follow_redirects=True)
    assert b"777001" not in resp.data
    with shift_db.connect() as conn:
        dump = "\n".join(conn.iterdump())
    assert "777001" not in dump


# --- backups ---------------------------------------------------------------------

def test_backup_carries_pages_not_access(client):
    login_operator(client)
    pid = new_page(client, title="Kept page")
    member = add_member()
    payload = json.loads(shift_db.export_json())
    assert any(p["title"] == "Kept page" for p in payload["hub_pages"])
    assert "hub_access" not in payload
    assert "1042" not in json.dumps(payload)
    assert shift_db.import_json(json.dumps(payload)) is None
    assert hub_db.get_page(pid)["title"] == "Kept page"
    assert hub_db.member_for_login("Sam", "1042")["id"] == member["id"]   # re-attached


def test_pre_hub_backup_leaves_hub_alone(client):
    login_operator(client)
    pid = new_page(client, title="Still here")
    payload = json.loads(shift_db.export_json())
    for table in shift_db.HUB_TABLES:
        payload.pop(table)
    assert shift_db.import_json(json.dumps(payload)) is None
    assert hub_db.get_page(pid)["title"] == "Still here"


def test_restore_rejects_bad_hub_file_names(client):
    payload = json.loads(shift_db.export_json())
    payload["hub_attachments"] = [{"id": 1, "page_id": 1, "filename": "x", "kind": "pdf",
                                   "stored_name": "../../app.py", "size": 1, "sha256": "",
                                   "uploaded_at": "", "uploaded_by": ""}]
    assert "hub_attachments" in shift_db.import_json(json.dumps(payload))


def test_restore_signs_members_out(client):
    add_member()
    login_member(client)
    assert client.get("/hub/").status_code == 200
    assert shift_db.import_json(shift_db.export_json()) is None
    assert client.get("/hub/").status_code == 302


# --- review findings (each reproduced first, then fixed) ---------------------------

def test_publish_uses_exactly_what_the_operator_reviewed(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, category="HR", title="Breaks", body="Reviewed text.", publish=False)
    client.post("/shift/logout")
    login_operator(client)
    seen = rev(pid)                                    # the Operator reads the draft
    client.post("/shift/logout")
    login_leader(client, "Ava", "5555")                # ...an admin changes it meanwhile
    client.post(f"/hub/page/{pid}/edit", data={
        "category_id": category_id("HR"), "title": "Breaks", "body": "UNREVIEWED.",
        "rev": rev(pid)})
    client.post("/shift/logout")
    login_operator(client)
    resp = client.post(f"/hub/page/{pid}/publish", data=dict(CHECKS, rev=seen),
                       follow_redirects=True)
    assert hub_db.get_page(pid)["status"] == "draft"
    assert b"changed since you opened it" in resp.data


def test_stale_form_cannot_undo_leaders_only(client):
    add_member()
    login_operator(client)
    pid = new_page(client, title="Cash handling", body="Steps.")
    stale = rev(pid)                                   # an editor opens the form
    form = {"category_id": category_id("FOH Operations"), "title": "Cash handling",
            "body": "Steps.", "section": "Training"}
    client.post(f"/hub/page/{pid}/edit", data=dict(form, leaders_only="1", rev=rev(pid)))
    resp = client.post(f"/hub/page/{pid}/edit", data=dict(form, keywords="till", rev=stale))
    assert resp.status_code == 409
    assert hub_db.get_page(pid)["leaders_only"] == 1


def test_regrant_or_reactivation_does_not_revive_old_phones(client):
    member = add_member()
    old_phone = cfa_app.app.test_client()
    login_member(old_phone)
    assert old_phone.get("/hub/").status_code == 200
    hub_db.revoke(member["id"], "Operator")
    hub_db.grant(member["id"], "1042", "Operator")     # same number back
    assert old_phone.get("/hub/").status_code == 302
    login_member(old_phone)
    shift_db.set_member_active(member["id"], False)
    shift_db.set_member_active(member["id"], True)
    assert old_phone.get("/hub/").status_code == 302


def test_moving_a_live_page_into_hr_needs_the_checks(client):
    login_operator(client)
    pid = new_page(client, title="Breaks", body="Take your break.")
    form = {"category_id": category_id("HR"), "title": "Breaks", "body": "Take your break."}
    resp = client.post(f"/hub/page/{pid}/edit", data=dict(form, rev=rev(pid)))
    assert resp.status_code == 400 and hub_db.get_page(pid)["category_policy"] == 0
    preview = client.post(f"/hub/page/{pid}/edit", data=dict(form, rev=rev(pid),
                                                            action="preview"))
    assert preview.status_code == 200                  # previewing never needs them
    client.post(f"/hub/page/{pid}/edit", data=dict(form, rev=rev(pid), **CHECKS))
    page = hub_db.get_page(pid)
    assert page["category_policy"] == 1 and page["version"] == 2
    assert hub_db.get_version(pid, 2)["reviewed_by"] == "Operator"


def test_one_alex_cannot_reset_the_lock_on_another(client):
    add_member("Alex Smith", "1111")
    add_member("Alex Jones", "2222")
    for _ in range(4):
        login_member(client, "Alex", "9999")
    assert login_member(client, "Alex", "1111").status_code == 302
    client.post("/hub/logout")
    login_member(client, "Alex", "9999")
    assert b"Too many tries" in login_member(client, "Alex", "2222").data


def test_unicode_digit_ids_are_not_a_crash(client):
    login_operator(client)
    assert client.post("/hub/categories", data={"category_id": "²", "name": "X"}
                       ).status_code == 302
    resp = client.post("/hub/import", data={"category_id": "²"},
                       content_type="multipart/form-data")
    assert resp.status_code == 302


def _link_slack(member_id, leader_name, slack_id="U123"):
    with shift_db.connect() as conn:
        conn.execute("UPDATE team_members SET slack_id=? WHERE id=?", (slack_id, member_id))
        conn.execute("UPDATE leaders SET slack_id=? WHERE name=?", (slack_id, leader_name))


def test_promoted_member_counts_once_and_consistently(client):
    member = add_member("Sam Patel", "1042")
    login_operator(client)
    pid = new_page(client, title="Allergens", required="1")
    client.post("/shift/logout")
    login_member(client)
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    client.post("/hub/logout")
    make_leader(client, "Sam Patel", "7777")           # promoted...
    _link_slack(member["id"], "Sam Patel")             # ...and the Slack pull links them
    hub_db.revoke(member["id"], "Operator")
    page = hub_db.get_page(pid)
    sams = [p for p in hub_db.readers(page) if p["name"] == "Sam Patel"]
    assert [p["state"] for p in sams] == ["current"]   # listed once, as read
    login_leader(client, "Sam Patel", "7777")
    assert b"all caught up" in client.get("/hub/to-read").data


def test_same_name_is_not_the_same_person(client):
    login_operator(client)
    pid = new_page(client, title="Harassment policy", category="HR", required="1")
    client.post("/shift/logout")
    make_leader(client, "Alex", "5151")
    login_leader(client, "Alex", "5151")
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    client.post("/shift/logout")
    leader = next(l for l in shift_db.leaders() if l["name"] == "Alex")
    shift_db.set_leader_active(leader["id"], False)    # Alex leaves...
    new_alex = add_member("Alex", "7070")               # ...a new hire shares the name
    viewer = hub_db.Viewer("member", new_alex["id"], "Alex")
    assert [p["id"] for p in hub_db.to_read(viewer)] == [pid]
    states = {p["name"]: p["state"] for p in hub_db.readers(hub_db.get_page(pid))}
    assert states["Alex"] == "none"


def test_restoring_an_older_backup_never_hands_confirmations_to_someone_else(client):
    login_operator(client)
    pid = new_page(client, title="Food safety", required="1")
    payload = json.loads(shift_db.export_json())       # before Kai was hired
    for table in shift_db.HUB_TABLES:
        payload.pop(table)
    kai = add_member("Kai Lee", "3333")
    client.post("/shift/logout")
    login_member(client, "Kai", "3333")
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    assert shift_db.import_json(json.dumps(payload)) is None
    shift_db.add_member("Lee Park")                    # may get Kai's old id
    lee = next(m for m in shift_db.roster() if m["name"] == "Lee Park")
    assert hub_db.grant(lee["id"], "4444", "Operator") is None
    assert lee["id"] == kai["id"]                      # the id Kai's confirmation used
    lee_viewer = hub_db.Viewer("member", lee["id"], "Lee Park")
    assert [p["id"] for p in hub_db.to_read(lee_viewer)] == [pid]


def test_deleted_draft_ids_are_never_reused(client):
    login_operator(client)
    first = new_page(client, title="Scratch", publish=False)
    client.post(f"/hub/page/{first}/delete", data={"confirm": "1"})
    assert new_page(client, title="Real page", publish=False) != first


def test_bulk_grant_rejects_extra_columns_and_numbers_in_names(client):
    shift_db.add_member("Sam Patel")
    out = hub_db.bulk_grant("Sam Patel\t1042\tFOH\nPatel, Sam, 5555\nSam 1042, 7", "Operator",
                            add_missing=True)
    assert out["granted"] == [] and out["added"] == [] and len(out["errors"]) == 3
    assert not any(m["name"][0].isdigit() or "," in m["name"] for m in shift_db.roster())


def test_turning_required_back_on_asks_everyone_again(client):
    add_member()
    login_operator(client)
    pid = new_page(client, title="Allergens", body="Old way.", required="1")
    client.post("/shift/logout")
    login_member(client)
    client.post(f"/hub/page/{pid}/ack", data={"version": "1"})
    client.post("/hub/logout")
    login_operator(client)
    form = {"category_id": category_id("FOH Operations"), "title": "Allergens",
            "section": "Training"}
    client.post(f"/hub/page/{pid}/edit", data=dict(form, body="New way.", rev=rev(pid)))
    client.post(f"/hub/page/{pid}/edit", data=dict(form, body="New way.", required="1",
                                                   rev=rev(pid)))
    client.post("/shift/logout")
    login_member(client)
    assert b"Allergens" in client.get("/hub/to-read").data


def _docx_raw(document_xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<Types><Override ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_hostile_word_files_are_refused_quickly(client):
    import time
    bomb = _docx_raw(b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "' + b"A" * 290
                     + b'">]><w:document xmlns:w="http://schemas.openxmlformats.org/'
                     b'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                     + b"&e;" * 200_000 + b"</w:t></w:r></w:p></w:body></w:document>")
    tiny = _docx_raw(b"<w:p/>" * 1_000_000)
    started = time.time()
    assert hub_text.docx_import(io.BytesIO(bomb)) == ("", [])
    assert hub_text.docx_import(io.BytesIO(tiny)) == ("", [])
    assert time.time() - started < 5
    login_operator(client)
    pid = new_page(client, title="Files", publish=False)
    corrupt = bytearray(_docx_bytes("<w:p><w:r><w:t>" + "x" * 5000 + "</w:t></w:r></w:p>"))
    corrupt[-400:-300] = b"\x00" * 100
    resp = upload(client, pid, bytes(corrupt), "broken.docx")
    assert resp.status_code == 302
    before = set(os.listdir(hub_db.files_dir())) if os.path.isdir(hub_db.files_dir()) else set()
    deep = _docx_raw(b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                     b'wordprocessingml/2006/main"><w:body>' + b"<w:sdt>" * 5000
                     + b"<w:p><w:r><w:t>x</w:t></w:r></w:p>" + b"</w:sdt>" * 5000
                     + b"</w:body></w:document>")
    assert upload(client, pid, deep, "deep.docx").status_code == 302
    after = set(os.listdir(hub_db.files_dir()))
    assert len(after - before) <= 1 and all(not n.endswith(".part") for n in after)


def test_manifest_allowed_by_csp(client):
    assert "manifest-src 'self'" in client.get("/hub/login").headers["Content-Security-Policy"]


def test_long_lines_render_fast():
    import time
    started = time.time()
    hub_text.render("**a _a " * 14_000)
    assert time.time() - started < 2


def test_word_import_table_text_boxes_and_visible_runs():
    ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')
    box = ('<w:r><mc:AlternateContent><mc:Choice><w:txbxContent><w:p><w:r><w:t>200 ppm</w:t>'
           '</w:r></w:p></w:txbxContent></mc:Choice><mc:Fallback><w:txbxContent><w:p><w:r>'
           '<w:t>200 ppm</w:t></w:r></w:p></w:txbxContent></mc:Fallback></mc:AlternateContent>'
           '</w:r>')
    xml = (f'<w:document {ns}><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Sanitizer</w:t></w:r>'
           f'{box}</w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:rPr><w:vanish w:val="0"/></w:rPr>'
           '<w:t>Wash hands.</w:t></w:r></w:p></w:body></w:document>')
    text, _ = hub_text.docx_import(io.BytesIO(_docx_raw(xml.encode())))
    assert text.count("200 ppm") == 1 and "Wash hands." in text


def test_word_import_cleans_titles_and_refuses_empty_files(client):
    login_operator(client)
    hidden = _docx_raw(b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                       b'wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>')
    before = len(hub_db.editor_queues()["drafts"])
    resp = client.post("/hub/import", data={
        "category_id": str(category_id("HR")), "file": (io.BytesIO(hidden), "x.docx")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"No text could be read" in resp.data
    assert len(hub_db.editor_queues()["drafts"]) == before
    plain = _docx_raw(b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                      b'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hi</w:t></w:r>'
                      b'</w:p></w:body></w:document>')
    resp = client.post("/hub/import", data={
        "category_id": str(category_id("HR")),
        "file": (io.BytesIO(plain), "Harassment ‮ycilop‬ policy​.docx")},
        content_type="multipart/form-data")
    page = hub_db.get_page(int(resp.headers["Location"].split("/")[-2]))
    assert page["title"] == "Harassment ycilop policy"


def test_sign_in_returns_to_the_page_not_a_post_url(client):
    resp = client.get("/hub/search?q=fryer")
    assert "/hub/search?q%3Dfryer" in resp.headers["Location"]
    resp = client.post("/hub/page/5/ack", headers={"Referer": "http://localhost/hub/page/5"})
    assert "next=/hub/page/5" in resp.headers["Location"]
    assert "ack" not in resp.headers["Location"]


def test_too_big_goes_back_to_the_page(client):
    login_operator(client)
    pid = new_page(client, title="Big", publish=False)
    resp = client.post(f"/hub/page/{pid}/files",
                       data={"file": (io.BytesIO(b"x" * (17 * 1024 * 1024)), "big.pdf")},
                       content_type="multipart/form-data",
                       headers={"Referer": f"https://localhost/hub/page/{pid}"})
    assert resp.headers["Location"].endswith(f"/hub/page/{pid}")


def test_emoji_joiners_survive():
    assert hub_text.clean("🧑‍🍳 cook") == "🧑‍🍳 cook"


def test_review_round_two(client):
    # next= with a line break is refused, not a 500 after signing in
    add_member()
    resp = client.post("/hub/login", data={"name": "Sam", "code": "1042",
                                           "next": "/hub/\r\nx"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/")
    client.post("/hub/logout")
    login_operator(client)
    pid = new_page(client, title="Cash", body="Steps.")
    stale = rev(pid)
    form = {"category_id": category_id("FOH Operations"), "title": "Cash", "body": "Steps.",
            "section": "Training"}
    client.post(f"/hub/page/{pid}/edit", data=dict(form, leaders_only="1", rev=rev(pid)))
    # previewing a stale copy keeps the stale rev in the form (and says so)
    preview = client.post(f"/hub/page/{pid}/edit", data=dict(form, rev=stale, action="preview"))
    assert f'name="rev" value="{stale}"'.encode() in preview.data
    assert b"changed since you opened it" in preview.data
    assert client.post(f"/hub/page/{pid}/edit", data=dict(form, rev=stale)).status_code == 409
    assert hub_db.get_page(pid)["leaders_only"] == 1
    # page text over the limit is refused with the text kept, never cut
    long_body = "word " * 20_001
    resp = client.post(f"/hub/page/{pid}/edit", data=dict(form, body=long_body, rev=rev(pid)))
    assert resp.status_code == 400 and b"over 100,000 characters" in resp.data
    assert hub_db.get_page(pid)["body"] == "Steps."


def test_draft_photo_numbers_are_not_reused(client):
    login_operator(client)
    pid = new_page(client, title="Draft photos", body="[photo: 1]", publish=False)
    upload(client, pid, PNG, "a.png")
    first = hub_db.attachments(pid)[0]
    client.post(f"/hub/file/{first['id']}/remove", data={"rev": rev(pid)})
    upload(client, pid, PNG + b"x", "b.png")
    assert hub_db.attachments(pid)[0]["photo_no"] == 2
    assert not os.path.exists(hub_db.file_path(first))


def test_word_import_utf16_bomb_moves_and_wrapped_rows():
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    bomb = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY e "' + "A" * 200
            + f'">]><w:document {w}><w:body><w:p><w:r><w:t>' + "&e;" * 5000
            + "</w:t></w:r></w:p></w:body></w:document>").encode("utf-16")
    assert hub_text.docx_import(io.BytesIO(_docx_raw(bomb))) == ("", [])
    moved = (f'<w:document {w}><w:body><w:p><w:moveFrom><w:r><w:t>Wash hands</w:t></w:r>'
             '</w:moveFrom></w:p><w:p><w:r><w:t>Gloves on</w:t></w:r></w:p><w:p><w:moveTo>'
             '<w:r><w:t>Wash hands</w:t></w:r></w:moveTo></w:p>'
             '<w:tbl><w:sdt><w:sdtContent><w:tr><w:tc><w:p><w:r><w:t>Below 41F</w:t></w:r>'
             '</w:p></w:tc><w:tc><w:p><w:r><w:t>OK</w:t></w:r></w:p></w:tc></w:tr>'
             '</w:sdtContent></w:sdt></w:tbl></w:body></w:document>')
    text, notes = hub_text.docx_import(io.BytesIO(_docx_raw(moved.encode())))
    assert text.count("Wash hands") == 1 and "Below 41F · OK" in text
    assert notes and "tracked changes" in notes[0]


def test_links_and_photo_lines():
    html = str(hub_text.render("Order at <https://www.cfahome.com/order>. **https://x.com**"))
    assert 'href="https://www.cfahome.com/order"' in html
    assert '<strong><a href="https://x.com"' in html
    assert "[photo: 3]" not in str(hub_text.render("Step\n[photo: 3]"))
    assert hub_text.clean("a\u2028[photo: 1]") == "a\n[photo: 1]"
    assert hub_db.safe_filename("Version 2.1 - Fryer cleaning procedure for the boh team "
                                "with lots of words here to be long enough.pdf", "pdf"
                                ).startswith("Version 2.1 - Fryer")


# --- one front door ------------------------------------------------------------------

def test_front_door_modes_and_redirects(client):
    resp = client.get("/shift/login?next=/shift/goals")
    assert resp.status_code == 302 and "as=leader" in resp.headers["Location"]
    assert "next=/shift/goals" in resp.headers["Location"]
    member_side = client.get("/hub/login").data.decode()
    assert 'action="/hub/login"' in member_side and "Clock-in number" in member_side
    leader_side = client.get("/hub/login?as=leader").data.decode()
    assert 'action="/shift/login"' in leader_side and 'value="Operator"' in leader_side
    wrong = client.post("/shift/login", data={"name": "Operator", "pin": "0000"})
    assert wrong.status_code == 401 and b'action="/shift/login"' in wrong.data
    assert b"Wrong PIN" in wrong.data
    login_operator(client)
    assert client.get("/hub/login").headers["Location"].endswith("/shift/")
    out = client.post("/shift/logout")
    assert out.headers["Location"].endswith("/hub/login")
    add_member()
    login_member(client)
    assert client.get("/hub/login").headers["Location"].endswith("/hub/")
    assert b'action="/shift/login"' in client.get("/hub/login?as=leader").data


def test_leaders_can_sign_in_without_the_hub(client, monkeypatch):
    import teamhub_nav
    monkeypatch.setattr(teamhub_nav, "hub_up", lambda: False)
    page = client.get("/shift/login")
    assert page.status_code == 200 and b'action="/shift/login"' in page.data
    assert b"Team member" not in page.data                 # no member side without the Hub
    login_operator(client)
    menu = client.get("/shift/more").data
    assert b"Systems &amp; Procedures" not in menu and b"Checklists" in menu
    assert client.post("/shift/logout").headers["Location"].endswith("/shift/login")


def test_shared_device_sign_in(client):
    resp = client.post("/shift/login", data={"name": "Operator", "pin": "9999", "shared": "1"})
    cookie = resp.headers.get("Set-Cookie", "")
    assert "session=" in cookie and "Expires" not in cookie        # gone when the browser closes
    assert client.get("/shift/").status_code == 200
    with client.session_transaction() as sess:
        sess["seen_at"] -= 31 * 60                                  # away 31 minutes
    resp = client.get("/shift/")
    assert resp.status_code == 302 and "/hub/login" in resp.headers["Location"]
    client.post("/shift/logout")
    resp = client.post("/shift/login", data={"name": "Operator", "pin": "9999"})
    assert "Expires" in resp.headers.get("Set-Cookie", "")          # unticked: 30 days


def test_menu_links_only_where_each_role_may_go(client):
    add_member()
    login_member(client)
    menu = client.get("/hub/menu").data.decode()
    assert "/shift/" not in menu.replace('href="/shift/login', "")
    assert "HR" in menu and "FOH Operations" in menu and "Leadership Development" not in menu
    client.post("/hub/logout")
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    menu = client.get("/shift/more").data.decode()
    assert "Manage pages" in menu and "Leader logins" in menu
    assert "Backups" not in menu and "/hub/people" not in menu and "Categories" not in menu
    client.post("/shift/logout")
    login_operator(client)
    menu = client.get("/hub/menu").data.decode()
    assert "Backups" in menu and "/hub/people" in menu


def test_database_from_an_early_hub_build_migrates(tmp_path, monkeypatch):
    """A database whose Hub tables predate rev/files/photo_no/nonce gets
    those columns on boot (the live disk never ran an early build, but a
    dev copy might have)."""
    import sqlite3
    path = tmp_path / "early.db"
    monkeypatch.setattr(shift_db, "DB_PATH", str(path))
    monkeypatch.setenv("SHIFT_HUB_FILES_DIR", str(tmp_path / "f"))
    early = sqlite3.connect(path)
    early.executescript("""
        CREATE TABLE hub_categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT 'x', blurb TEXT NOT NULL DEFAULT '',
            leaders_only INTEGER NOT NULL DEFAULT 0, policy INTEGER NOT NULL DEFAULT 0,
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE hub_pages (id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL,
            section TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            keywords TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft', leaders_only INTEGER NOT NULL DEFAULT 0,
            required INTEGER NOT NULL DEFAULT 0, needs_counsel INTEGER NOT NULL DEFAULT 0,
            annual_review INTEGER NOT NULL DEFAULT 0, review_by TEXT,
            version INTEGER NOT NULL DEFAULT 0, ack_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL, published_at TEXT);
        CREATE TABLE hub_page_versions (id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL,
            version INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '', change_note TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL, saved_at TEXT NOT NULL, saved_by TEXT NOT NULL,
            reviewed_by TEXT, reviewed_at TEXT, UNIQUE (page_id, version));
        CREATE TABLE hub_attachments (id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL,
            filename TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            size INTEGER NOT NULL, sha256 TEXT NOT NULL, alt TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '', uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL);
        CREATE TABLE hub_access (member_id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL,
            granted_at TEXT NOT NULL, granted_by TEXT NOT NULL);
        INSERT INTO hub_categories (name) VALUES ('FOH Operations');
        INSERT INTO hub_pages (category_id, title, created_at, created_by, updated_at, updated_by)
            VALUES (1, 'Old page', 't', 'x', 't', 'x');
        INSERT INTO hub_attachments (page_id, filename, stored_name, kind, size, sha256,
            uploaded_at, uploaded_by) VALUES (1, 'a.png', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'png', 1, 'h', 't', 'x'),
            (1, 'b.pdf', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'pdf', 1, 'h', 't', 'x'), (1, 'c.png', 'cccccccccccccccccccccccccccccccc', 'png', 1, 'h', 't', 'x');
    """)
    early.close()
    shift_db.init_db()
    cols = {r["name"] for r in shift_db.connect().execute("PRAGMA table_info(hub_pages)")}
    assert "rev" in cols
    assert [f["photo_no"] for f in hub_db.attachments(1)] == [1, None, 2]
    shift_db.add_member("Sam Patel")
    sam = next(m for m in shift_db.roster() if m["name"] == "Sam Patel")
    assert hub_db.grant(sam["id"], "1042", "Operator") is None
    assert hub_db.member_for_login("Sam", "1042")["id"] == sam["id"]
    viewer = hub_db.Viewer("operator", None, "Operator", "admin")
    page = hub_db.get_page(1)
    assert hub_db.publish(viewer, 1, page["rev"], "", True) is None
    assert hub_db.search(viewer, "old")[0]["id"] == 1
