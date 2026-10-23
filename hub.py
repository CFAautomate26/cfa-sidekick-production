"""Team Hub for Chick-fil-A Wharncliffe & Wonderland, mounted at /hub.

"Here's how we do it here": systems, processes, training and policies,
organized in categories (HR, FOH Operations, BOH Operations, Leadership
Development) with sections inside each.

Sign-in: team members use first name + clock-in number (granted by the
Operator on the People page); leaders use their Shift Lead login, which
carries over. Editors are the Operator and admin-role leaders. HR-style
"policy" categories are published only by the Operator.

Data layer: hub_db.py. Setup guide: docs/team-hub.md
"""

import hmac
import io
import os
import time
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

import hub_db
import hub_text
import shift_app
import shift_db

hub_bp = Blueprint("hub", __name__, url_prefix="/hub")

# Uploads (one file per request) — Flask applies this per request.
_UPLOAD_LIMIT = 16 * 1024 * 1024

# Clock-in sign-in throttle (in-process, per worker): 5 misses locks a
# first name for 10 minutes; 50 misses across everyone pauses clock-in
# sign-ins for 10 minutes (leaders and existing sessions are unaffected).
_NAME_FAILS = 5
_ALL_FAILS = 50
_WINDOW = 600
_fails: dict[str, list[float]] = {}


def _recent(key: str) -> list[float]:
    now = time.time()
    hits = [t for t in _fails.get(key, []) if now - t < _WINDOW]
    if hits:
        _fails[key] = hits
    else:
        _fails.pop(key, None)
    return hits


def _locked(name_key: str) -> str | None:
    if len(_recent("*")) >= _ALL_FAILS:
        return "Clock-in sign-in is paused for a few minutes. Try again soon."
    if len(_recent(name_key)) >= _NAME_FAILS:
        return "Too many tries for that name. Try again in 10 minutes."
    return None


def _record_miss(name_key: str) -> None:
    if len(_fails) > 500:
        for key in list(_fails):
            _recent(key)
    now = time.time()
    _fails.setdefault(name_key, []).append(now)
    _fails.setdefault("*", []).append(now)


# ---------------------------------------------------------------------------
# Who's here
# ---------------------------------------------------------------------------

_PUBLIC_ENDPOINTS = {"hub.login", "hub.logout", "hub.manifest", "hub.icon"}


def _load_viewer() -> hub_db.Viewer | None:
    if session.get("shift_name"):
        if not shift_app.leader_session_valid():
            return None
        leader_id = session.get("shift_leader_id")
        if leader_id:
            return hub_db.Viewer("leader", leader_id, session["shift_name"],
                                 session.get("shift_role", "lead"))
        return hub_db.Viewer("operator", None, shift_app.OPERATOR_NAME, "admin")
    member_id = session.get("hub_member_id")
    if member_id:
        current = hub_db.member_session(member_id)
        if (not current or session.get("hub_epoch", "") != shift_db.session_epoch()
                or not hmac.compare_digest(session.get("hub_tag", ""), current["tag"])):
            session.clear()
            return None
        return hub_db.Viewer("member", current["id"], current["name"])
    return None


@hub_bp.before_request
def require_viewer():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    viewer = _load_viewer()
    if viewer is None:
        return redirect(url_for("hub.login", next=_resume_target()))
    g.viewer = viewer
    return None


def _same_site_referrer() -> str | None:
    """The Hub page the request came from (path + query), if any."""
    ref = urlparse(request.referrer or "")
    if ref.netloc == request.host and ref.path.startswith("/hub/"):
        return ref.path + (f"?{ref.query}" if ref.query else "")
    return None


def _resume_target() -> str:
    """Where to land after signing in: the page asked for (with its query),
    or — for a form post that can't be replayed — the page it came from."""
    if request.method == "GET":
        return request.full_path.rstrip("?")
    return _same_site_referrer() or "/hub/"


@hub_bp.after_request
def _headers(resp):
    # Pages can hold internal procedures: never cached, framed or sniffed.
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    if resp.mimetype == "text/html":
        # The Hub runs no scripts at all.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'")
    return resp


@hub_bp.errorhandler(OverflowError)
def _id_out_of_range(e):
    flash("That page doesn't exist.")
    return redirect(url_for("hub.home"))


@hub_bp.errorhandler(RequestEntityTooLarge)
def _too_big(e):
    flash("That file is too big — 15 MB at most." if request.mimetype == "multipart/form-data"
          else "That's too much text to save at once — split it into two pages.")
    return redirect(_same_site_referrer() or url_for("hub.home"))


@hub_bp.context_processor
def _globals():
    viewer = getattr(g, "viewer", None)
    return {
        "viewer": viewer,
        "hub_to_read": len(hub_db.to_read(viewer)) if viewer else 0,
        "data_at_risk": shift_app.DATA_AT_RISK,
    }


def editor_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.viewer.is_editor:
            flash("That's for the Operator and admin leaders.")
            return redirect(url_for("hub.home"))
        return view(*args, **kwargs)
    return wrapped


def operator_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.viewer.is_operator:
            flash("That's for the Operator master login.")
            return redirect(url_for("hub.home"))
        return view(*args, **kwargs)
    return wrapped


def _viewable_page(page_id: int) -> dict:
    page = hub_db.get_page(page_id)
    if not hub_db.can_view(g.viewer, page):
        abort(404)
    return page


def _editable_page(page_id: int) -> dict | None:
    page = _viewable_page(page_id)
    return page if hub_db.can_edit(g.viewer, page) else None


def _line(key: str, cap: int = 300) -> str:
    return " ".join(hub_text.clean(request.form.get(key) or "").split())[:cap]


# ---------------------------------------------------------------------------
# Sign in / out
# ---------------------------------------------------------------------------

@hub_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and _load_viewer():
        return redirect(url_for("hub.home"))
    error, picked = None, ""
    if request.method == "POST":
        picked = " ".join((request.form.get("name") or "").split())[:60]
        code = request.form.get("code") or ""
        key = "name:" + (picked.split()[0].casefold() if picked else "")
        error = _locked(key)
        if not error:
            member = hub_db.member_for_login(picked, code)
            if member:
                # Misses age out on their own: clearing the shared first-name
                # bucket here would let one Alex reset the lock on another.
                session.clear()
                session.permanent = True
                session["hub_member_id"] = member["id"]
                session["hub_tag"] = member["tag"]
                session["hub_epoch"] = shift_db.session_epoch()
                return shift_app._safe_next("hub.home")
            _record_miss(key)
            error = ("That name and clock-in number don't match. "
                     "Ask a leader if you're stuck.")
        return render_template("hub/login.html", error=error, picked=picked), 401
    return render_template("hub/login.html", error=None, picked="")


@hub_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("hub.login"))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@hub_bp.route("/")
def home():
    viewer = g.viewer
    cats = hub_db.categories(viewer)
    return render_template(
        "hub/home.html", categories=cats, counts=hub_db.category_counts(viewer),
        to_read=hub_db.to_read(viewer), recent=hub_db.recent_updates(viewer),
        queues=hub_db.editor_queues() if viewer.is_editor else None)


@hub_bp.route("/c/<int:category_id>")
def category(category_id):
    cat = hub_db.get_category(category_id)
    if not hub_db.can_view_category(g.viewer, cat):
        abort(404)
    pages = hub_db.pages_for_category(g.viewer, category_id)
    acks = hub_db.my_acks(g.viewer, [p["id"] for p in pages])
    groups: dict[str, list[dict]] = {}
    for p in pages:
        if p["status"] == "published":
            groups.setdefault(p["section"] or "General", []).append(p)
    return render_template(
        "hub/category.html", category=cat, groups=groups, acks=acks,
        drafts=[p for p in pages if p["status"] == "draft"],
        retired=[p for p in pages if p["status"] == "retired"])


def _file_url(f: dict) -> str:
    return url_for("hub.file", file_id=f["id"])


@hub_bp.route("/page/<int:page_id>")
def page(page_id, upload_error: str | None = None):
    pg = _viewable_page(page_id)
    files = hub_db.attachments(page_id)
    photos = hub_db.photo_map(files, _file_url)
    shown = {int(m.group(1)) for line in hub_text.clean(pg["body"]).split("\n")
             if (m := hub_text.PHOTO.match(line))}
    gallery = [(n, url, alt) for n, (url, alt) in sorted(photos.items()) if n not in shown]
    mine = hub_db.my_acks(g.viewer, [page_id]).get(page_id)
    needs_ack = (pg["status"] == "published" and pg["required"]
                 and not g.viewer.is_operator
                 and (not mine or mine["version"] < pg["ack_version"]))
    return render_template(
        "hub/page.html", page=pg, body=hub_text.render(pg["body"], photos),
        files=[f for f in files if f["kind"] not in hub_db.IMAGE_KINDS],
        all_files=files,
        gallery=gallery, mine=mine, needs_ack=needs_ack, POLICY_CHECKS=_POLICY_CHECKS,
        changes=(hub_db.changes_since(page_id, mine["version"])
                 if needs_ack and mine else []),
        can_edit=hub_db.can_edit(g.viewer, pg), upload_error=upload_error,
        publish_block=(hub_db.publish_block(g.viewer, pg)
                       if pg["status"] == "draft" else None),
        ACK_WORDING=hub_db.ACK_WORDING)


@hub_bp.route("/page/<int:page_id>/ack", methods=["POST"])
def ack(page_id):
    pg = _viewable_page(page_id)
    if (request.form.get("version") or "") != str(pg["version"]):
        flash("This page changed while you had it open — have a look at the "
              "latest version, then confirm.")
    elif hub_db.ack(g.viewer, pg):
        flash("Thanks — that's recorded.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()[:100]
    return render_template("hub/search.html", q=q,
                           results=hub_db.search(g.viewer, q) if q else [])


@hub_bp.route("/to-read")
def to_read():
    return render_template("hub/to_read.html", pages=hub_db.to_read(g.viewer))


@hub_bp.route("/me")
def me():
    acks = hub_db.my_acks(g.viewer)
    done = []
    for page_id, a in acks.items():
        pg = hub_db.get_page(page_id)
        if pg and hub_db.can_view(g.viewer, pg) and pg["status"] == "published":
            done.append(dict(pg, ack=a))
    done.sort(key=lambda p: p["ack"]["acked_at"], reverse=True)
    return render_template("hub/me.html", done=done)


@hub_bp.route("/file/<int:file_id>")
def file(file_id):
    att = hub_db.get_attachment(file_id)
    if not att or att["purged_at"]:
        abort(404)
    _viewable_page(att["page_id"])
    if att["removed_at"] and not g.viewer.is_editor:      # History only
        abort(404)
    path = hub_db.file_path(att)
    if not os.path.exists(path):
        abort(404)
    inline = att["kind"] == "pdf" or att["kind"] in hub_db.IMAGE_KINDS
    return send_file(path, mimetype=hub_db.FILE_MIMETYPES[att["kind"]],
                     as_attachment=not inline, download_name=att["filename"],
                     conditional=False, etag=False, max_age=0)


# ---------------------------------------------------------------------------
# Writing (editors)
# ---------------------------------------------------------------------------

_POLICY_CHECKS = [
    ("check_people", "It describes how we do things — nothing about any specific "
                     "team member (no names in a discipline, wage, medical or "
                     "accommodation context)."),
    ("check_law", "I checked it against current Ontario rules (ESA / OHSA / Human "
                  "Rights Code) and our legal-counsel notes."),
    ("check_counsel", "If it changes anyone's rights or a rule people are held to, "
                      "a lawyer has reviewed it."),
]


def _policy_checked() -> bool:
    return all(request.form.get(key) for key, _ in _POLICY_CHECKS)


def _change_to_live_page(pg: dict) -> tuple[dict | None, str | None]:
    """The "what changed" part of a file change on a published page (drafts
    need none): (kwargs for hub_db, None) or (None, why not)."""
    if pg["version"] == 0:
        return {}, None
    if pg["needs_counsel"]:
        return None, ("This page is flagged for a lawyer's review — the Operator "
                      "clears it first.")
    if pg["category_policy"] and not _policy_checked():
        return None, "Tick each review check before changing a policy page."
    return {"rev": hub_db.as_id(request.form.get("rev")),
            "change_note": _line("change_note"),
            "reread": bool(pg["required"] and request.form.get("reread") == "1"),
            "reviewed": bool(pg["category_policy"])}, None


def _edit_context(page: dict | None, fields: dict, **extra) -> dict:
    cats = hub_db.categories(include_inactive=True)
    files = hub_db.attachments(page["id"]) if page else []
    # A re-drawn form (preview, error) keeps the rev it was first drawn
    # from: a stale copy must stay stale until the editor reloads.
    form_rev = (request.form.get("rev", "") if request.method == "POST"
                else page["rev"] if page else "")
    return dict(page=page, f=fields, categories=cats, form_rev=form_rev,
                sections=hub_db.sections(), POLICY_CHECKS=_POLICY_CHECKS,
                photo_numbers=hub_db.photo_map(files, _file_url), **extra)


def _render_edit(page, fields, status=200, **extra):
    return render_template("hub/edit.html", **_edit_context(page, fields, **extra)), status


def _check_category(fields: dict, page: dict | None) -> str | None:
    cat = hub_db.get_category(fields["category_id"])
    if not cat:
        return "Pick a category."
    if not fields["title"]:
        return "Give the page a title."
    if len(fields["body"]) > hub_db.BODY_MAX:
        return hub_db.BODY_TOO_LONG
    was_policy = bool(page and page["category_policy"])
    if (page and page["version"] > 0 and (cat["policy"] or was_policy)
            and not g.viewer.is_operator):
        return "Only the Operator moves a published page into or out of a policy category."
    return None


@hub_bp.route("/new", methods=["GET", "POST"])
@editor_required
def new_page():
    if request.method == "GET":
        fields = {"category_id": hub_db.as_id(request.args.get("category")) or 0, "section": request.args.get("section", "")[:60],
                  "title": "", "summary": "", "keywords": "", "body": "",
                  "leaders_only": 0, "required": 0, "annual_review": 0}
        return _render_edit(None, fields)
    fields = hub_db.clean_fields(request.form)
    error = _check_category(fields, None)
    if error or request.form.get("action") == "preview":
        return _render_edit(None, fields, 400 if error else 200, error=error,
                            preview=None if error else hub_text.render(fields["body"]),
                            needs_counsel=bool(request.form.get("needs_counsel")))
    page_id = hub_db.create_page(g.viewer, fields,
                                 needs_counsel=bool(request.form.get("needs_counsel")))
    flash("Draft saved — only editors can see it until it's published.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/edit", methods=["GET", "POST"])
@editor_required
def edit_page(page_id):
    pg = _editable_page(page_id)
    if not pg:
        flash("Only the Operator changes a published policy page.")
        return redirect(url_for("hub.page", page_id=page_id))
    if request.method == "GET":
        return _render_edit(pg, pg)
    fields = hub_db.clean_fields(request.form)
    target = hub_db.get_category(fields["category_id"])
    content_changed = any(fields[k] != pg[k] for k in ("title", "summary", "body"))
    # Moving a published page into a policy category publishes it there:
    # it goes through the same checks, as a new version.
    into_policy = bool(pg["version"] > 0 and target and target["policy"]
                       and not pg["category_policy"])
    policy = bool(pg["category_policy"] or (target and target["policy"]))
    publishing = pg["version"] > 0 and (content_changed or into_policy)
    preview = request.form.get("action") == "preview"
    error = _check_category(fields, pg)
    if not error and hub_db.as_id(request.form.get("rev")) != pg["rev"]:
        error = hub_db.STALE
    if not error and not preview and publishing:
        if pg["needs_counsel"]:
            error = ("This page is flagged for a lawyer's review — the Operator clears "
                     "it first.")
        elif policy and not _policy_checked():
            error = "Tick each review check before publishing a policy change."
    if error or preview:
        photos = hub_db.photo_map(hub_db.attachments(page_id), _file_url)
        status = 409 if error == hub_db.STALE else 400 if error else 200
        return _render_edit(pg, fields, status, error=error,
                            preview=None if error else hub_text.render(fields["body"], photos))
    summary = hub_db.update_page(
        g.viewer, pg, fields, hub_db.as_id(request.form.get("rev")),
        change_note=_line("change_note"),
        reread=bool(fields["required"] and request.form.get("reread") == "1"),
        reviewed=policy and publishing, new_version=into_policy)
    if summary is None:
        return _render_edit(pg, fields, 409, error=hub_db.STALE)
    flash(summary + ".")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/publish", methods=["POST"])
@editor_required
def publish(page_id):
    pg = _viewable_page(page_id)
    error = hub_db.publish(g.viewer, page_id, hub_db.as_id(request.form.get("rev")),
                           _line("change_note"), _policy_checked())
    flash(error or "Published." + (" Everyone will see it under To read."
                                   if pg["required"] else ""))
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/status", methods=["POST"])
@editor_required
def set_status(page_id):
    pg = _editable_page(page_id)
    status = request.form.get("status")
    if not pg or pg["version"] == 0 or status not in ("published", "retired"):
        flash("You can't do that to this page.")
    elif status != pg["status"]:
        hub_db.set_status(g.viewer, pg, status)
        flash("Retired — only editors see it now." if status == "retired"
              else "It's back for everyone.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/delete", methods=["POST"])
@editor_required
def delete_page(page_id):
    pg = _editable_page(page_id)
    if pg and pg["version"] == 0 and request.form.get("confirm") == "1":
        hub_db.delete_draft(g.viewer, pg)
        flash("Draft deleted.")
        return redirect(url_for("hub.category", category_id=pg["category_id"]))
    flash("Only an unpublished draft can be deleted — retire a published page instead.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/counsel", methods=["POST"])
@editor_required
def counsel(page_id):
    pg = _viewable_page(page_id)
    flagged = request.form.get("flag") == "1"
    note = _line("note")
    if not flagged and not note:
        flash("Add a note — who reviewed it, and when.")
    elif hub_db.set_counsel(g.viewer, pg, flagged, note):
        flash("Flagged — it can't be published until the Operator clears it."
              if flagged else "Counsel flag cleared.")
    else:
        flash("Only the Operator clears a counsel flag.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/review", methods=["POST"])
@editor_required
def review(page_id):
    pg = _editable_page(page_id)
    if not pg or pg["status"] != "published":
        flash("You can't mark this page reviewed.")
    else:
        hub_db.mark_reviewed(g.viewer, pg, _line("note"))
        flash("Marked reviewed — next review is due in a year.")
    return redirect(url_for("hub.page", page_id=page_id))


@hub_bp.route("/page/<int:page_id>/history")
@editor_required
def history(page_id):
    pg = _viewable_page(page_id)
    return render_template("hub/history.html", page=pg,
                           versions=hub_db.versions(page_id),
                           removed=hub_db.removed_attachments(page_id),
                           reviews=hub_db.reviews(page_id),
                           log=hub_db.audit_log(100, page_id))


@hub_bp.route("/page/<int:page_id>/v/<int:version>")
@editor_required
def old_version(page_id, version):
    pg = _viewable_page(page_id)
    v = hub_db.get_version(page_id, version)
    if not v:
        abort(404)
    files = [dict(f, att=hub_db.get_attachment(f["id"])) for f in v["files"]]
    return render_template("hub/version.html", page=pg, v=v, files=files,
                           body=hub_text.render(v["body"],
                                                hub_db.version_photo_map(v, _file_url)))


@hub_bp.route("/page/<int:page_id>/readers")
@editor_required
def readers(page_id):
    pg = _viewable_page(page_id)
    people = hub_db.readers(pg)
    return render_template("hub/readers.html", page=pg, people=people,
                           counts={s: sum(p["state"] == s for p in people)
                                   for s in ("current", "old", "none")})


@hub_bp.route("/page/<int:page_id>/readers.csv")
@editor_required
def readers_csv(page_id):
    pg = _viewable_page(page_id)
    return Response(hub_db.acks_csv(pg), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=hub-page-{page_id}-confirmations.csv"})


@hub_bp.route("/page/<int:page_id>/files", methods=["POST"])
@editor_required
def upload(page_id):
    request.max_content_length = _UPLOAD_LIMIT
    pg = _editable_page(page_id)
    if not pg:
        flash("Only the Operator changes a published policy page.")
        return redirect(url_for("hub.page", page_id=page_id))
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a file first.")
        return redirect(url_for("hub.page", page_id=page_id))
    change, error = _change_to_live_page(pg)
    row = None
    if not error:
        row, error = hub_db.add_attachment(g.viewer, pg, upload.filename,
                                           upload.read(hub_db.MAX_FILE_BYTES + 1),
                                           alt=request.form.get("alt") or "", **change)
    if error:
        # Re-draw the page with the upload form open, keeping what was typed
        # (browsers never let a page re-fill the chosen file).
        return page(page_id, upload_error=error), 400
    if row["photo_no"]:
        flash(f"Photo {row['photo_no']} added. Type [photo: {row['photo_no']}] on its own "
              "line in the page text to show it there — otherwise it shows at the bottom.")
    else:
        flash(f"Added {row['filename']}.")
    return redirect(url_for("hub.page", page_id=page_id))


def _live_attachment(file_id: int) -> tuple[dict, dict | None]:
    att = hub_db.get_attachment(file_id)
    if not att or att["removed_at"]:
        abort(404)
    return att, _editable_page(att["page_id"])


@hub_bp.route("/file/<int:file_id>/remove", methods=["GET", "POST"])
@editor_required
def remove_file(file_id):
    att, pg = _live_attachment(file_id)
    if not pg:
        flash("Only the Operator changes a published policy page.")
        return redirect(url_for("hub.page", page_id=att["page_id"]))
    if request.method == "GET":
        return render_template("hub/file_remove.html", att=att, page=pg,
                               POLICY_CHECKS=_POLICY_CHECKS)
    change, error = _change_to_live_page(pg)
    if not error:
        error = hub_db.remove_attachment(g.viewer, att, pg, **(change or {"rev": None}))
    if error:
        return render_template("hub/file_remove.html", att=att, page=pg, error=error,
                               POLICY_CHECKS=_POLICY_CHECKS), 400
    flash(f"Removed {att['filename']}." + (" It stays in the page's History."
                                           if pg["version"] else ""))
    return redirect(url_for("hub.page", page_id=att["page_id"]))


@hub_bp.route("/file/<int:file_id>/purge", methods=["POST"])
@operator_required
def purge_file(file_id):
    att = hub_db.get_attachment(file_id)
    if not att:
        abort(404)
    if request.form.get("confirm") == "1" and hub_db.purge_attachment(g.viewer, att):
        flash(f"{att['filename']} is deleted for good.")
    else:
        flash("Tick the box to delete it for good (only files already taken off a page).")
    return redirect(url_for("hub.history", page_id=att["page_id"]))


@hub_bp.route("/import", methods=["POST"])
@editor_required
def import_word():
    """A Word document becomes a new draft (the file itself isn't kept)."""
    request.max_content_length = _UPLOAD_LIMIT
    upload = request.files.get("file")
    category_id = hub_db.as_id(request.form.get("category_id"))
    cat = hub_db.get_category(category_id) if category_id else None
    if not cat:
        flash("Pick a category for the new page.")
        return redirect(url_for("hub.manage"))
    data = upload.read(hub_db.MAX_FILE_BYTES + 1) if upload else b""
    if not data or len(data) > hub_db.MAX_FILE_BYTES or hub_db.sniff_kind(data) != "docx":
        flash("That isn't a Word (.docx) file under 15 MB. Older .doc files: open "
              "them in Word and Save As .docx first.")
        return redirect(url_for("hub.category", category_id=cat["id"]))
    text, notes = hub_text.docx_import(io.BytesIO(data))
    if len(text) > hub_db.BODY_MAX:
        flash(f"That document is too long for one page ({len(text):,} characters of text; "
              f"the limit is {hub_db.BODY_MAX:,}). Split it into parts in Word, then import "
              "each one.")
        return redirect(url_for("hub.category", category_id=cat["id"]))
    if not text.strip():
        flash("No text could be read from that Word file — it may be all pictures, or "
              "saved as “Strict Open XML”. Save it as a regular Word Document (.docx) "
              "and try again, or attach it to a page instead.")
        return redirect(url_for("hub.category", category_id=cat["id"]))
    title, body = _split_title(text, upload.filename)
    fields = {"category_id": cat["id"], "section": _line("section", 60), "title": title,
              "summary": "", "keywords": "", "body": body, "leaders_only": 0,
              "required": 0, "annual_review": 0}
    page_id = hub_db.create_page(g.viewer, fields)
    flash("Imported as a draft — check the formatting, then publish."
          + (" Note: " + "; ".join(notes) + "." if notes else ""))
    return redirect(url_for("hub.edit_page", page_id=page_id))


def _split_title(text: str, filename: str) -> tuple[str, str]:
    """The document's first heading becomes the page title."""
    lines = text.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()[:140], "\n".join(lines[1:]).strip()
    stem = os.path.splitext(os.path.basename(filename or "Imported page"))[0]
    stem = hub_text.clean(stem.replace("_", " "))
    return (" ".join(stem.split())[:140] or "Imported page"), text


# ---------------------------------------------------------------------------
# Manage: queues, categories, people, activity
# ---------------------------------------------------------------------------

@hub_bp.route("/manage")
@editor_required
def manage():
    return render_template("hub/manage.html", queues=hub_db.editor_queues(),
                           categories=hub_db.categories(include_inactive=True),
                           log=hub_db.audit_log(60))


@hub_bp.route("/categories", methods=["POST"])
@operator_required
def save_category():
    sort = hub_db.as_id(request.form.get("sort")) or 0
    saved = hub_db.save_category(
        hub_db.as_id(request.form.get("category_id")), _line("name", 60), _line("icon", 8),
        _line("blurb", 200), bool(request.form.get("leaders_only")),
        bool(request.form.get("policy")), bool(request.form.get("active")), sort,
        g.viewer.name)
    flash("Category saved." if saved else "A category needs a name.")
    return redirect(url_for("hub.manage"))


@hub_bp.route("/people")
@operator_required
def people():
    rows = hub_db.people()
    q = " ".join((request.args.get("q") or "").split())[:60]
    show = request.args.get("show") if request.args.get("show") in ("yes", "no") else ""
    shown = [p for p in rows
             if (not q or q.casefold() in p["name"].casefold())
             and (not show or bool(p["has_access"]) == (show == "yes"))]
    return render_template("hub/people.html", people=shown, total=len(rows), q=q, show=show,
                           with_access=sum(1 for p in rows if p["has_access"]))


@hub_bp.route("/people/<int:member_id>/grant", methods=["POST"])
@operator_required
def grant(member_id):
    error = hub_db.grant(member_id, request.form.get("code") or "", g.viewer.name)
    flash(error or "Access saved — they sign in with their first name + that number.")
    return redirect(url_for("hub.people") + f"#m{member_id}")


@hub_bp.route("/people/<int:member_id>/revoke", methods=["POST"])
@operator_required
def revoke(member_id):
    hub_db.revoke(member_id, g.viewer.name)
    flash("Access removed — they're signed out on their next tap.")
    return redirect(url_for("hub.people") + f"#m{member_id}")


@hub_bp.route("/people/bulk", methods=["POST"])
@operator_required
def bulk_grant():
    out = hub_db.bulk_grant(request.form.get("lines") or "", g.viewer.name,
                            add_missing=bool(request.form.get("add_missing")))
    parts = [f"{len(out['granted'])} given access"]
    if out["added"]:
        parts.append("added to the roster: " + ", ".join(out["added"]))
    if out["unmatched"]:
        parts.append("not on the roster (tick “add them” to include): "
                     + ", ".join(out["unmatched"]))
    if out["ambiguous"]:
        parts.append("more than one person with that first name — use the full name: "
                     + ", ".join(out["ambiguous"]))
    flash(". ".join(parts) + ".")
    for error in out["errors"][:20]:
        flash(error)
    return redirect(url_for("hub.people"))


# ---------------------------------------------------------------------------
# Home-screen app bits (public)
# ---------------------------------------------------------------------------

@hub_bp.route("/manifest.webmanifest")
def manifest():
    return {
        "name": "CFA Team Hub",
        "short_name": "Team Hub",
        "start_url": "/hub/",
        "display": "standalone",
        "background_color": "#FFFAF2",
        "theme_color": "#E31937",
        "icons": [{"src": "/hub/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, 200, {"Content-Type": "application/manifest+json"}


_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
<rect width="180" height="180" rx="40" fill="#E31937"/>
<text x="90" y="88" font-family="Helvetica, Arial, sans-serif" font-size="56"
 font-weight="bold" fill="#fff" text-anchor="middle">CFA</text>
<text x="90" y="132" font-family="Helvetica, Arial, sans-serif" font-size="30"
 fill="#FFFAF2" text-anchor="middle">Hub</text>
</svg>"""


@hub_bp.route("/icon.svg")
def icon():
    return Response(_ICON_SVG, mimetype="image/svg+xml")
