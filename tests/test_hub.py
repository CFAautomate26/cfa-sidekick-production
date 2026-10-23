"""Team Hub (/hub): sign-in, who sees what, publishing, read confirmations,
files, people, and how the Hub rides along with backups."""

import io
import json
import os
import zipfile

import pytest

import app as cfa_app
import hub
import hub_db
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
        checks = {"check_people": "1", "check_law": "1", "check_counsel": "1"}
        client.post(f"/hub/page/{page_id}/publish", data=checks)
    return page_id


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
    assert client.get("/hub/icon.svg").status_code == 200


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
    resp = client.get("/shift/")
    assert resp.status_code == 302 and "/shift/login" in resp.headers["Location"]


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
    assert client.get("/hub/").status_code == 200
    assert b"Maya" in client.get("/hub/me").data


def test_operator_pin_change_signs_operator_out(client, monkeypatch):
    login_operator(client)
    assert client.get("/hub/").status_code == 200
    assert client.get("/shift/").status_code == 200
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "1234")
    assert client.get("/hub/").status_code == 302
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
    resp = client.post(f"/hub/page/{pid}/publish", data={
        "check_people": "1", "check_law": "1", "check_counsel": "1"})
    assert hub_db.get_page(pid)["status"] == "draft"
    client.post("/shift/logout")

    login_operator(client)
    client.post(f"/hub/page/{pid}/publish", data={"check_people": "1"})   # checks missing
    assert hub_db.get_page(pid)["status"] == "draft"
    client.post(f"/hub/page/{pid}/publish", data={
        "check_people": "1", "check_law": "1", "check_counsel": "1"})
    page = hub_db.get_page(pid)
    assert page["status"] == "published" and page["version"] == 1
    assert hub_db.versions(pid)[0]["reviewed_by"] == "Operator"
    client.post("/shift/logout")

    login_leader(client, "Ava", "5555")                 # admin can't edit it now
    resp = client.post(f"/hub/page/{pid}/edit", data={
        "category_id": page["category_id"], "title": "Dress code", "body": "changed",
        "base_version": "1"})
    assert resp.status_code == 302
    assert hub_db.get_page(pid)["body"] != "changed"
    assert b"Only the Operator" in client.get(f"/hub/page/{pid}").data


def test_admin_cannot_move_published_page_into_hr(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, title="Breaks")
    resp = client.post(f"/hub/page/{pid}/edit", data={
        "category_id": category_id("HR"), "title": "Breaks", "body": "# Steps\n- Greet the guest",
        "base_version": "1"})
    assert resp.status_code == 400
    assert hub_db.get_page(pid)["category_id"] == category_id("FOH Operations")


def test_counsel_flag_blocks_publishing_until_operator_clears(client):
    make_leader(client, "Ava", "5555", role="admin")
    login_leader(client, "Ava", "5555")
    pid = new_page(client, title="Scheduling rules", publish=False, needs_counsel="1")
    client.post(f"/hub/page/{pid}/publish", data={})
    assert hub_db.get_page(pid)["status"] == "draft"
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": "looked fine"})
    assert hub_db.get_page(pid)["needs_counsel"] == 1   # only the Operator clears
    client.post("/shift/logout")
    login_operator(client)
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": ""})
    assert hub_db.get_page(pid)["needs_counsel"] == 1   # a note is required
    client.post(f"/hub/page/{pid}/counsel", data={"flag": "0", "note": "Counsel OK 10/20"})
    client.post(f"/hub/page/{pid}/publish", data={})
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
        base, body="Use 200 ppm quat.", base_version="1", reread="0", change_note="typo"))
    page = hub_db.get_page(pid)
    assert (page["version"], page["ack_version"]) == (2, 1)       # small fix
    client.post(f"/hub/page/{pid}/edit", data=dict(
        base, body="Use 400 ppm quat.", base_version="2", reread="1",
        change_note="New strength"))
    page = hub_db.get_page(pid)
    assert (page["version"], page["ack_version"]) == (3, 3)
    stale = client.post(f"/hub/page/{pid}/edit", data=dict(
        base, body="Something else", base_version="2", reread="0"))
    assert stale.status_code == 400 and hub_db.get_page(pid)["version"] == 3
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
    csv_text = client.get(f"/hub/page/{pid}/readers.csv").data.decode()
    assert csv_text.count("Sam Patel") == 2 and hub_db.ACK_WORDING in csv_text


def test_settings_only_change_makes_no_version(client):
    login_operator(client)
    pid = new_page(client, title="Uniform", body="Hat on.")
    client.post(f"/hub/page/{pid}/edit", data={
        "category_id": category_id("FOH Operations"), "title": "Uniform", "body": "Hat on.",
        "section": "Training", "keywords": "hat cap", "base_version": "1"})
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


def test_upload_serve_and_remove_files(client, tmp_path):
    login_operator(client)
    pid = new_page(client, title="Fryer", body="Hang it like this:\n[photo: 1]")
    resp = client.post(f"/hub/page/{pid}/files", data={
        "file": (io.BytesIO(PNG), "basket.png"), "alt": "Basket hung"},
        content_type="multipart/form-data")
    assert resp.status_code == 302
    client.post(f"/hub/page/{pid}/files", data={"file": (io.BytesIO(PDF), "guide.pdf")},
                content_type="multipart/form-data")
    bad = client.post(f"/hub/page/{pid}/files", data={
        "file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.png")},
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
    client.post(f"/hub/file/{files[1]['id']}/delete", data={"confirm": "1"})
    assert len(hub_db.attachments(pid)) == 1
    assert not os.path.exists(path)


def test_files_follow_page_visibility(client):
    login_operator(client)
    pid = new_page(client, title="Leaders file", leaders_only="1")
    client.post(f"/hub/page/{pid}/files", data={"file": (io.BytesIO(PDF), "x.pdf")},
                content_type="multipart/form-data")
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
