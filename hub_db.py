"""Team Hub data layer: the store's "here's how we do it" library at /hub.

Lives in the shift app's SQLite database (tables in shift_db.SCHEMA) so the
two share backups and the Render disk. Pages hold processes and policies —
never records about a specific person.

Who sees what:
- Team members sign in with first name + clock-in number (hub_access,
  granted by the Operator); leaders use their Shift Lead login.
- Leaders see everything published, including leaders-only pages and
  categories; editors (the Operator + admin-role leaders) also see drafts.
- Policy categories (HR): only the Operator publishes or changes a
  published page, after a short review checklist.

Every publish is kept as an immutable version; "I've read this" records
are append-only and carry a hash of exactly what was on the page.
"""

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta

import hub_text
import shift_db
from shift_db import connect, now_stamp, today_local

ACK_WORDING = "I've received and read this version."
REVIEW_EVERY_DAYS = 365
REVIEW_WARN_DAYS = 30
MAX_FILE_BYTES = 15 * 1024 * 1024
_MIN_FREE_BYTES = 100 * 1024 * 1024

FILE_MIMETYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
IMAGE_KINDS = ("png", "jpg", "gif", "webp")
assert set(FILE_MIMETYPES) == set(shift_db.HUB_FILE_KINDS)


@dataclass(frozen=True)
class Viewer:
    kind: str                 # operator | leader | member
    id: int | None
    name: str
    role: str = ""            # leaders: lead | admin

    @property
    def key(self) -> str:
        return "operator" if self.kind == "operator" else f"{self.kind}:{self.id}"

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"

    @property
    def is_leader(self) -> bool:
        return self.kind in ("operator", "leader")

    @property
    def is_editor(self) -> bool:
        return self.is_operator or (self.kind == "leader" and self.role == "admin")


def files_dir() -> str:
    """Uploaded files live beside the database (the Render disk)."""
    return (os.getenv("SHIFT_HUB_FILES_DIR", "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(shift_db.DB_PATH)), "hub_files"))


def _audit(conn, who: str, action: str, page_id: int | None = None,
           detail: str = "") -> None:
    conn.execute("INSERT INTO hub_audit (at, who, action, page_id, detail) "
                 "VALUES (?,?,?,?,?)", (now_stamp(), who, action, page_id, detail[:500]))


def audit_log(limit: int = 100, page_id: int | None = None) -> list[dict]:
    q = ("SELECT a.*, p.title FROM hub_audit a LEFT JOIN hub_pages p ON p.id = a.page_id ")
    args: tuple = ()
    if page_id is not None:
        q += "WHERE a.page_id=? "
        args = (page_id,)
    q += "ORDER BY a.id DESC LIMIT ?"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q, args + (limit,))]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def categories(viewer: Viewer | None = None, include_inactive: bool = False) -> list[dict]:
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM hub_categories ORDER BY sort, id")]
    if not include_inactive:
        rows = [c for c in rows if c["active"]]
    if viewer is not None and not viewer.is_leader:
        rows = [c for c in rows if not c["leaders_only"]]
    return rows


def get_category(category_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM hub_categories WHERE id=?",
                           (category_id,)).fetchone()
    return dict(row) if row else None


def can_view_category(viewer: Viewer, category: dict | None) -> bool:
    if not category:
        return False
    if not category["active"] and not viewer.is_editor:
        return False
    return viewer.is_leader or not category["leaders_only"]


def save_category(category_id: int | None, name: str, icon: str, blurb: str,
                  leaders_only: bool, policy: bool, active: bool, sort: int,
                  who: str) -> int | None:
    name = " ".join(name.split())[:60]
    if not name:
        return None
    icon = (icon.strip() or "📘")[:8]
    blurb = " ".join(blurb.split())[:200]
    with closing(connect()) as conn, conn:
        if category_id is None:
            cur = conn.execute(
                "INSERT INTO hub_categories (name, icon, blurb, leaders_only, policy, "
                "sort, active) VALUES (?,?,?,?,?,?,?)",
                (name, icon, blurb, int(leaders_only), int(policy), sort, int(active)))
            category_id = cur.lastrowid
            _audit(conn, who, "category added", None, name)
        else:
            if not conn.execute("SELECT 1 FROM hub_categories WHERE id=?",
                                (category_id,)).fetchone():
                return None
            conn.execute(
                "UPDATE hub_categories SET name=?, icon=?, blurb=?, leaders_only=?, "
                "policy=?, sort=?, active=? WHERE id=?",
                (name, icon, blurb, int(leaders_only), int(policy), sort, int(active),
                 category_id))
            _audit(conn, who, "category changed", None,
                   f"{name}: leaders_only={int(leaders_only)} policy={int(policy)} "
                   f"active={int(active)}")
    return category_id


# ---------------------------------------------------------------------------
# Pages: visibility + reading
# ---------------------------------------------------------------------------

_PAGE_SELECT = (
    "SELECT p.*, c.name AS category_name, c.icon AS category_icon, "
    "c.leaders_only AS category_leaders_only, c.policy AS category_policy, "
    "c.active AS category_active FROM hub_pages p "
    "JOIN hub_categories c ON c.id = p.category_id ")


def get_page(page_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(_PAGE_SELECT + "WHERE p.id=?", (page_id,)).fetchone()
    return dict(row) if row else None


def is_leaders_only(page: dict) -> bool:
    return bool(page["leaders_only"] or page["category_leaders_only"])


def can_view(viewer: Viewer, page: dict | None) -> bool:
    if not page:
        return False
    if viewer.is_editor:
        return True
    if page["status"] != "published" or not page["category_active"]:
        return False
    return viewer.is_leader or not is_leaders_only(page)


def can_edit(viewer: Viewer, page: dict) -> bool:
    """Editors change any draft; a published (or retired) policy page is the
    Operator's alone."""
    if not viewer.is_editor:
        return False
    return viewer.is_operator or not (page["category_policy"] and page["version"] > 0)


def publish_block(viewer: Viewer, page: dict) -> str | None:
    """Why this viewer can't publish this draft now, or None."""
    if not viewer.is_editor:
        return "Only editors publish pages."
    if page["category_policy"] and not viewer.is_operator:
        return f"Only the Operator publishes {page['category_name']} pages."
    if page["needs_counsel"]:
        return ("Flagged for a lawyer's review — the Operator clears the flag once "
                "counsel has signed off.")
    return None


def pages_for_category(viewer: Viewer, category_id: int) -> list[dict]:
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            _PAGE_SELECT + "WHERE p.category_id=? ORDER BY p.section COLLATE NOCASE, "
            "p.title COLLATE NOCASE", (category_id,))]
    return [p for p in rows if can_view(viewer, p)]


def sections(category_id: int | None = None) -> list[str]:
    q = "SELECT DISTINCT section FROM hub_pages WHERE section != '' "
    args: tuple = ()
    if category_id is not None:
        q += "AND category_id=? "
        args = (category_id,)
    with closing(connect()) as conn:
        return [r["section"] for r in conn.execute(q + "ORDER BY section COLLATE NOCASE",
                                                   args)]


def category_counts(viewer: Viewer) -> dict[int, int]:
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(_PAGE_SELECT + "WHERE p.status='published'")]
    counts: dict[int, int] = {}
    for p in rows:
        if can_view(viewer, p):
            counts[p["category_id"]] = counts.get(p["category_id"], 0) + 1
    return counts


def recent_updates(viewer: Viewer, limit: int = 6) -> list[dict]:
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            _PAGE_SELECT + "WHERE p.status='published' ORDER BY p.published_at DESC, "
            "p.id DESC LIMIT 200")]
    return [p for p in rows if can_view(viewer, p)][:limit]


def draft_count() -> int:
    with closing(connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM hub_pages WHERE status='draft'").fetchone()[0]


def editor_queues() -> dict:
    """What needs an editor: drafts, flagged pages, reviews coming due."""
    soon = (date.fromisoformat(today_local())
            + timedelta(days=REVIEW_WARN_DAYS)).isoformat()
    with closing(connect()) as conn:
        def q(where, args=()):
            return [dict(r) for r in conn.execute(
                _PAGE_SELECT + "WHERE " + where + " ORDER BY p.updated_at DESC", args)]
        return {
            "drafts": q("p.status='draft'"),
            "counsel": q("p.needs_counsel=1 AND p.status != 'retired'"),
            "review_due": q("p.status='published' AND p.annual_review=1 "
                            "AND p.review_by IS NOT NULL AND p.review_by <= ?", (soon,)),
            "retired": q("p.status='retired'"),
        }


# ---------------------------------------------------------------------------
# Pages: writing
#
# Every write bumps hub_pages.rev. Forms carry the rev they were drawn from,
# and a save from a stale copy is refused rather than quietly undoing
# someone else's change (or publishing text a reviewer never saw).
# ---------------------------------------------------------------------------

BODY_MAX = 100_000
BODY_TOO_LONG = ("The page text is over 100,000 characters — split it into two pages "
                 "(your text is below, nothing was saved).")

STALE = ("This page changed since you opened it — someone else saved it. "
         "Your text is below: copy what you need, then reload the page.")


def clean_fields(form) -> dict:
    """Page form fields, trimmed and bounded."""
    def line(key, cap):
        return " ".join(hub_text.clean(form.get(key) or "").split())[:cap]
    return {
        "category_id": as_id(form.get("category_id")) or 0,
        "section": line("section", 60),
        "title": line("title", 140),
        "summary": line("summary", 300),
        "keywords": line("keywords", 300),
        "body": hub_text.clean(form.get("body") or "").strip(),     # BODY_MAX checked by routes
        "leaders_only": 1 if form.get("leaders_only") else 0,
        "required": 1 if form.get("required") else 0,
        "annual_review": 1 if form.get("annual_review") else 0,
    }


def as_id(raw) -> int | None:
    """A form/URL id: plain ASCII digits only (str.isdigit accepts '²')."""
    raw = (raw or "").strip() if isinstance(raw, str) else ""
    return int(raw) if re.fullmatch(r"[0-9]{1,15}", raw) else None


def _live_files(conn, page_id: int) -> list:
    return conn.execute(
        "SELECT * FROM hub_attachments WHERE page_id=? AND removed_at IS NULL ORDER BY id",
        (page_id,)).fetchall()


def _content_hash(conn, page_id: int, title: str, summary: str, body: str) -> str:
    """Fingerprint of exactly what a reader sees: the words and every file."""
    files = sorted(r["sha256"] for r in _live_files(conn, page_id))
    blob = "\x1e".join([title, summary, body, *files])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def create_page(viewer: Viewer, fields: dict, needs_counsel: bool = False) -> int:
    stamp = now_stamp()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO hub_pages (category_id, section, title, summary, keywords, body, "
            "status, leaders_only, required, needs_counsel, annual_review, version, "
            "ack_version, created_at, created_by, updated_at, updated_by) "
            "VALUES (?,?,?,?,?,?,'draft',?,?,?,?,0,0,?,?,?,?)",
            (fields["category_id"], fields["section"], fields["title"], fields["summary"],
             fields["keywords"], fields["body"], fields["leaders_only"], fields["required"],
             int(needs_counsel), fields["annual_review"], stamp, viewer.name, stamp,
             viewer.name))
        _audit(conn, viewer.name, "draft created", cur.lastrowid, fields["title"])
        return cur.lastrowid


def _snapshot(conn, page_id: int, version: int, fields: dict, change_note: str,
              who: str, reviewed: bool) -> None:
    """Keep this version exactly as published — its words and its files."""
    stamp = now_stamp()
    files = [{"id": r["id"], "name": r["filename"], "kind": r["kind"],
              "sha256": r["sha256"], "photo": r["photo_no"], "alt": r["alt"]}
             for r in _live_files(conn, page_id)]
    conn.execute(
        "INSERT INTO hub_page_versions (page_id, version, title, summary, body, "
        "change_note, content_hash, saved_at, saved_by, reviewed_by, reviewed_at, files) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (page_id, version, fields["title"], fields["summary"], fields["body"],
         change_note, _content_hash(conn, page_id, fields["title"], fields["summary"],
                                    fields["body"]),
         stamp, who, who if reviewed else None, stamp if reviewed else None,
         json.dumps(files, ensure_ascii=False)))


def _next_review(annual_review: int, current: str | None) -> str | None:
    if not annual_review:
        return None
    return current or (date.fromisoformat(today_local())
                       + timedelta(days=REVIEW_EVERY_DAYS)).isoformat()


def _ack_from(page: dict, version: int, made_version: bool, reread: bool,
              required: int) -> int:
    """The version read confirmations count from after a save. A page that
    isn't required always counts from its latest words, and making a page
    required asks everyone to confirm what it says now."""
    if made_version and (reread or not required):
        return version
    if required and not page["required"]:
        return version
    return page["ack_version"]


def update_page(viewer: Viewer, page: dict, fields: dict, rev: int | None,
                change_note: str = "", reread: bool = False, reviewed: bool = False,
                new_version: bool = False) -> str | None:
    """Save an edit made from `page` as it was at `rev`. A draft just
    changes; on a published page a change to the words (or `new_version`,
    e.g. a move into a policy category) is published as a new version,
    while a settings-only change makes none. Returns a short summary, or
    None if the page changed since the form was drawn."""
    content_changed = any(fields[k] != page[k] for k in ("title", "summary", "body"))
    made_version = page["version"] > 0 and (content_changed or new_version)
    version = page["version"] + 1 if made_version else page["version"]
    ack_version = _ack_from(page, version, made_version, reread, fields["required"])
    stamp = now_stamp()
    with closing(connect()) as conn, conn:
        if rev is None or not conn.execute(
                "UPDATE hub_pages SET category_id=?, section=?, title=?, summary=?, "
                "keywords=?, body=?, leaders_only=?, required=?, annual_review=?, "
                "review_by=?, version=?, ack_version=?, updated_at=?, updated_by=?, "
                "published_at=CASE WHEN ? THEN ? ELSE published_at END, rev=rev+1 "
                "WHERE id=? AND rev=?",
                (fields["category_id"], fields["section"], fields["title"],
                 fields["summary"], fields["keywords"], fields["body"],
                 fields["leaders_only"], fields["required"], fields["annual_review"],
                 _next_review(fields["annual_review"], page["review_by"]), version,
                 ack_version, stamp, viewer.name, int(made_version), stamp, page["id"],
                 rev)).rowcount:
            return None
        if made_version:
            _snapshot(conn, page["id"], version, fields, change_note, viewer.name, reviewed)
            rereads = ack_version == version and fields["required"]
            _audit(conn, viewer.name, "new version", page["id"],
                   f"v{version}" + (" (re-read)" if rereads else "")
                   + (" (policy review checklist done)" if reviewed else "")
                   + (f": {change_note}" if change_note else ""))
            return f"v{version} published" + (" — everyone re-reads" if rereads else "")
        _audit(conn, viewer.name, "edited" if page["version"] == 0 or content_changed
               else "settings changed", page["id"], fields["title"])
    return "Saved"


def publish(viewer: Viewer, page_id: int, rev: int | None, change_note: str,
            policy_checked: bool) -> str | None:
    """First publish of a draft, exactly as the publisher saw it (`rev`):
    version 1, which everyone reads. Returns why not, or None."""
    stamp = now_stamp()
    with closing(connect()) as conn, conn:
        row = conn.execute(_PAGE_SELECT + "WHERE p.id=?", (page_id,)).fetchone()
        if not row or row["version"] != 0:
            return "That page is already published."
        page = dict(row)
        if rev is None or page["rev"] != rev:
            return ("This draft changed since you opened it — read it over again, "
                    "then publish.")
        block = publish_block(viewer, page)
        if block:
            return block
        if page["category_policy"] and not policy_checked:
            return "Tick each review check before publishing a policy page."
        if not conn.execute(
                "UPDATE hub_pages SET status='published', version=1, ack_version=1, "
                "published_at=?, updated_at=?, updated_by=?, review_by=?, rev=rev+1 "
                "WHERE id=? AND rev=? AND version=0 AND category_id=?",
                (stamp, stamp, viewer.name, _next_review(page["annual_review"], None),
                 page_id, rev, page["category_id"])).rowcount:
            return "This draft changed since you opened it — read it over again, then publish."
        _snapshot(conn, page_id, 1, page, change_note or "First version", viewer.name,
                  bool(page["category_policy"]))
        _audit(conn, viewer.name, "published", page_id,
               "v1" + (" (policy review checklist done)" if page["category_policy"] else ""))
    return None


def import_bundle(viewer: Viewer, entries: list[dict]) -> dict:
    """Many pages at once (the Operator's library bundle). Each entry is a
    page as hub_text.bundle_entries describes it. Pages whose category is a
    policy category stay drafts whatever the bundle says (the Operator
    publishes those one by one, after the review checks); a title that is
    already in its category is left alone. All or nothing: one transaction.
    Returns {"published": [...], "drafts": [...], "skipped": [(title, why)]}."""
    stamp = now_stamp()
    result: dict = {"published": [], "drafts": [], "skipped": []}
    with closing(connect()) as conn, conn:
        cats = {c["name"].strip().lower(): dict(c) for c in conn.execute(
            "SELECT * FROM hub_categories WHERE active=1")}
        for e in entries:
            cat = cats.get(e["category"].strip().lower())
            if not cat:
                result["skipped"].append((e["title"], f"no category called “{e['category']}”"))
                continue
            if len(e["body"]) > BODY_MAX:
                result["skipped"].append((e["title"], "too long for one page"))
                continue
            if conn.execute(
                    "SELECT 1 FROM hub_pages WHERE category_id=? AND status != 'retired' "
                    "AND title=? COLLATE NOCASE", (cat["id"], e["title"])).fetchone():
                result["skipped"].append((e["title"], "already in " + cat["name"]))
                continue
            fields = {"category_id": cat["id"], "section": e["section"], "title": e["title"],
                      "summary": e["summary"], "keywords": e["keywords"], "body": e["body"],
                      "leaders_only": int(bool(e.get("leaders_only"))),
                      "required": int(bool(e.get("required"))), "annual_review": 0}
            cur = conn.execute(
                "INSERT INTO hub_pages (category_id, section, title, summary, keywords, body, "
                "status, leaders_only, required, needs_counsel, annual_review, version, "
                "ack_version, created_at, created_by, updated_at, updated_by) "
                "VALUES (?,?,?,?,?,?,'draft',?,?,0,0,0,0,?,?,?,?)",
                (fields["category_id"], fields["section"], fields["title"], fields["summary"],
                 fields["keywords"], fields["body"], fields["leaders_only"], fields["required"],
                 stamp, viewer.name, stamp, viewer.name))
            page_id = cur.lastrowid
            _audit(conn, viewer.name, "draft created", page_id, f"{e['title']} (from a bundle)")
            if e.get("publish") and not cat["policy"]:
                conn.execute(
                    "UPDATE hub_pages SET status='published', version=1, ack_version=1, "
                    "published_at=?, updated_at=?, updated_by=?, rev=rev+1 WHERE id=?",
                    (stamp, stamp, viewer.name, page_id))
                _snapshot(conn, page_id, 1, fields, "First version (from a bundle)",
                          viewer.name, False)
                _audit(conn, viewer.name, "published", page_id, "v1 (from a bundle)")
                result["published"].append(e["title"])
            else:
                result["drafts"].append(e["title"])
    return result


def _touch(conn, page_id: int, who: str) -> None:
    conn.execute("UPDATE hub_pages SET rev=rev+1, updated_at=?, updated_by=? WHERE id=?",
                 (now_stamp(), who, page_id))


def set_status(viewer: Viewer, page: dict, status: str) -> None:
    assert status in ("published", "retired") and page["version"] > 0
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE hub_pages SET status=? WHERE id=?", (status, page["id"]))
        _touch(conn, page["id"], viewer.name)
        _audit(conn, viewer.name, "retired" if status == "retired" else "brought back",
               page["id"], page["title"])


def delete_draft(viewer: Viewer, page: dict) -> bool:
    """Only a never-published draft can be deleted (and its files with it)."""
    with closing(connect()) as conn, conn:
        names = [r["stored_name"] for r in conn.execute(
            "SELECT stored_name FROM hub_attachments WHERE page_id=?", (page["id"],))]
        gone = conn.execute("DELETE FROM hub_pages WHERE id=? AND version=0",
                            (page["id"],)).rowcount
        if not gone:
            return False
        _audit(conn, viewer.name, "draft deleted", page["id"], page["title"])
    for name in names:
        _remove_file(name)
    return True


def set_counsel(viewer: Viewer, page: dict, flagged: bool, note: str) -> bool:
    """Any editor can flag a page for a lawyer; only the Operator clears it."""
    if not viewer.is_editor or (not flagged and not viewer.is_operator):
        return False
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE hub_pages SET needs_counsel=? WHERE id=?",
                     (int(flagged), page["id"]))
        _touch(conn, page["id"], viewer.name)
        _audit(conn, viewer.name, "flagged for counsel" if flagged else "counsel cleared",
               page["id"], note)
    return True


def mark_reviewed(viewer: Viewer, page: dict, note: str) -> None:
    next_due = (date.fromisoformat(today_local())
                + timedelta(days=REVIEW_EVERY_DAYS)).isoformat()
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO hub_reviews (page_id, reviewed_at, reviewed_by, note) "
                     "VALUES (?,?,?,?)", (page["id"], now_stamp(), viewer.name, note))
        conn.execute("UPDATE hub_pages SET review_by=? WHERE id=?", (next_due, page["id"]))
        _touch(conn, page["id"], viewer.name)
        _audit(conn, viewer.name, "reviewed", page["id"], note)


def versions(page_id: int) -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hub_page_versions WHERE page_id=? ORDER BY version DESC",
            (page_id,))]


def get_version(page_id: int, version: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM hub_page_versions WHERE page_id=? AND version=?",
                           (page_id, version)).fetchone()
    if not row:
        return None
    v = dict(row)
    try:
        v["files"] = json.loads(v["files"] or "[]")
    except ValueError:
        v["files"] = []
    return v


def reviews(page_id: int) -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hub_reviews WHERE page_id=? ORDER BY id DESC", (page_id,))]


# ---------------------------------------------------------------------------
# "I've read this"
# ---------------------------------------------------------------------------

def _linked_keys(conn) -> dict[str, set[str]]:
    """A leader login and a roster row count as one person only when the
    Slack roster pull linked them (same Slack account) — never by name, so
    a new hire who shares a departed leader's name starts with nothing
    confirmed. Both the reader's own To read list and the editors' Who's
    read it use this. key -> every key of that person (itself included)."""
    links: dict[str, set[str]] = {}
    for r in conn.execute(
            "SELECT l.id AS leader_id, m.id AS member_id FROM leaders l "
            "JOIN team_members m ON m.slack_id = l.slack_id WHERE l.slack_id IS NOT NULL"):
        group = {f"leader:{r['leader_id']}", f"member:{r['member_id']}"}
        for key in list(group):
            group |= links.get(key, set())
        for key in group:
            links[key] = group
    return links


def _identity_keys(conn, viewer: Viewer) -> list[str]:
    return sorted(_linked_keys(conn).get(viewer.key, {viewer.key}))


def my_acks(viewer: Viewer, page_ids: list[int] | None = None) -> dict[int, dict]:
    """page_id -> this person's latest confirmation."""
    with closing(connect()) as conn:
        keys = _identity_keys(conn, viewer)
        rows = conn.execute(
            f"SELECT * FROM hub_acks WHERE reader_key IN ({','.join('?' * len(keys))}) "
            "ORDER BY version", keys).fetchall()
    out = {}
    for r in rows:
        if page_ids is None or r["page_id"] in page_ids:
            out[r["page_id"]] = dict(r)
    return out


def ack(viewer: Viewer, page: dict) -> bool:
    if viewer.is_operator or page["status"] != "published" or not page["required"]:
        return False
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO hub_acks (page_id, version, reader_key, reader_name, "
            "wording, content_hash, acked_at) VALUES (?,?,?,?,?,?,?)",
            (page["id"], page["version"], viewer.key, viewer.name, ACK_WORDING,
             _content_hash(conn, page["id"], page["title"], page["summary"], page["body"]),
             now_stamp()))
        return cur.rowcount == 1


def to_read(viewer: Viewer) -> list[dict]:
    """Required pages this person hasn't confirmed at the current version."""
    if viewer.is_operator:
        return []
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            _PAGE_SELECT + "WHERE p.status='published' AND p.required=1 "
            "ORDER BY p.published_at DESC")]
    rows = [p for p in rows if can_view(viewer, p)]
    acks = my_acks(viewer, [p["id"] for p in rows])
    return [dict(p, last_ack=acks.get(p["id"])) for p in rows
            if not acks.get(p["id"]) or acks[p["id"]]["version"] < p["ack_version"]]


def required_progress(viewer: Viewer) -> tuple[int, int]:
    """(done, total): the required pages this person can see, and how many
    of them they've confirmed at the version that counts. total - done is
    always len(to_read(viewer)); the Operator reads nothing (0, 0)."""
    if viewer.is_operator:
        return 0, 0
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            _PAGE_SELECT + "WHERE p.status='published' AND p.required=1")]
    rows = [p for p in rows if can_view(viewer, p)]
    acks = my_acks(viewer, [p["id"] for p in rows])
    done = sum(1 for p in rows
               if acks.get(p["id"]) and acks[p["id"]]["version"] >= p["ack_version"])
    return done, len(rows)


def changes_since(page_id: int, version: int) -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT version, change_note, saved_at FROM hub_page_versions "
            "WHERE page_id=? AND version > ? ORDER BY version", (page_id, version))]


def readers(page: dict) -> list[dict]:
    """Everyone the page is for, with where they are on it: current, an
    older version, or not yet. People who can sign in both ways count once."""
    with closing(connect()) as conn:
        links = _linked_keys(conn)
        people: list[dict] = []
        seen: set[str] = set()
        for kind, q in (("leader", "SELECT id, name FROM leaders WHERE active=1"),
                        ("member", "SELECT m.id, m.name FROM team_members m JOIN hub_access a "
                                   "ON a.member_id = m.id WHERE m.active=1")):
            for r in conn.execute(q):
                key = f"{kind}:{r['id']}"
                if key in seen:
                    continue          # the leader login already listed this person
                keys = links.get(key, {key})
                seen |= keys
                people.append({"name": r["name"], "leader": kind == "leader", "keys": keys})
        acks: dict[str, dict] = {}
        for r in conn.execute("SELECT * FROM hub_acks WHERE page_id=? ORDER BY version",
                              (page["id"],)):
            acks[r["reader_key"]] = dict(r)
    out = []
    for person in people:
        if is_leaders_only(page) and not person["leader"]:
            continue
        mine = [acks[k] for k in person["keys"] if k in acks]
        last = max(mine, key=lambda a: (a["version"], a["acked_at"])) if mine else None
        state = ("current" if last and last["version"] >= page["ack_version"]
                 else "old" if last else "none")
        out.append({"name": person["name"], "leader": person["leader"],
                    "state": state, "ack": last})
    order = {"none": 0, "old": 1, "current": 2}
    return sorted(out, key=lambda p: (order[p["state"]], p["name"].casefold()))


def acks_csv(page: dict) -> str:
    """Every confirmation ever recorded for the page (append-only log)."""
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM hub_acks WHERE page_id=? ORDER BY acked_at, id",
                            (page["id"],)).fetchall()
    buf = io.StringIO()
    buf.write("\ufeff")            # BOM: Excel then reads accented names correctly
    w = csv.writer(buf)
    w.writerow(["page", "version", "name", "confirmed at", "wording", "content sha256"])
    for r in rows:
        w.writerow([_csv_safe(page["title"]), r["version"], _csv_safe(r["reader_name"]),
                    r["acked_at"], r["wording"], r["content_hash"]])
    return buf.getvalue()


def _csv_safe(value: str) -> str:
    """Spreadsheet apps run cells starting with = + - @ as formulas."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(viewer: Viewer, query: str, limit: int = 40) -> list[dict]:
    words = re.findall(r"[\w'’-]+", query.lower())[:8]
    if not words:
        return []
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            _PAGE_SELECT + "WHERE p.status='published'")]
        files: dict[int, list[str]] = {}
        for r in conn.execute("SELECT page_id, filename, text FROM hub_attachments "
                              "WHERE removed_at IS NULL"):
            files.setdefault(r["page_id"], []).append(f"{r['filename']} {r['text']}")
    pattern = re.compile("|".join(re.escape(w) for w in words), re.I)
    results = []
    for p in rows:
        if not can_view(viewer, p):
            continue
        body = hub_text.plain(p["body"])
        hay = " ".join([p["title"], p["summary"], p["keywords"], p["section"],
                        p["category_name"], body, *files.get(p["id"], [])]).lower()
        if not all(w in hay for w in words):
            continue
        title = p["title"].lower()
        score = sum(4 for w in words if w in title) + sum(
            2 for w in words if w in (p["keywords"] + " " + p["summary"]).lower())
        results.append((score, p["published_at"] or "", dict(
            p, snippet=_snippet(body or p["summary"], pattern))))
    results.sort(key=lambda t: t[1], reverse=True)       # newest first...
    results.sort(key=lambda t: -t[0])                     # ...within each score
    return [r for _, _, r in results][:limit]


def _snippet(text: str, pattern: re.Pattern, width: int = 160) -> list[tuple[str, bool]]:
    """A window of text around the first match, as (text, is_match) parts —
    the template escapes each part and marks the matches."""
    m = pattern.search(text)
    start = max(0, (m.start() if m else 0) - width // 3)
    window = text[start:start + width]
    if start > 0:
        window = "…" + window.split(" ", 1)[-1]
    if start + width < len(text):
        window = window.rsplit(" ", 1)[0] + "…"
    parts, last = [], 0
    for hit in pattern.finditer(window):
        parts.append((window[last:hit.start()], False))
        parts.append((hit.group(0), True))
        last = hit.end()
    parts.append((window[last:], False))
    return [p for p in parts if p[0]]


# ---------------------------------------------------------------------------
# Files
#
# On a draft, files come and go freely. On a published page, adding or
# removing one is a change to what people read: it makes a new version
# (with the re-read choice, and the policy checks on policy pages), and a
# removed file stays on disk for History until the Operator purges it.
# ---------------------------------------------------------------------------

def sniff_kind(data: bytes) -> str | None:
    """What a file really is, from its first bytes — never its name."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04"):
        return _office_kind(data)
    return None


def _office_kind(data: bytes) -> str | None:
    """docx/xlsx/pptx — never a macro-enabled or template variant."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if len(names) > 5000 or any("vbaproject" in n.lower() for n in names):
                return None
            info = z.getinfo("[Content_Types].xml")
            if info.file_size > 1_000_000:
                return None
            types = z.read(info).decode("utf-8", "replace")
    except Exception:          # any damaged or hostile zip is simply not allowed
        return None
    if "macroenabled" in types.lower():
        return None
    for kind, part in (("docx", "wordprocessingml.document.main+xml"),
                       ("xlsx", "spreadsheetml.sheet.main+xml"),
                       ("pptx", "presentationml.presentation.main+xml")):
        if part in types:
            return kind
    return None


def _file_path(stored_name: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", stored_name or ""):
        raise ValueError("bad stored name")
    return os.path.join(files_dir(), stored_name)


def file_path(attachment: dict) -> str:
    return _file_path(attachment["stored_name"])


def _remove_file(stored_name: str) -> None:
    try:
        os.remove(_file_path(stored_name))
    except (OSError, ValueError):
        pass


def safe_filename(name: str, kind: str) -> str:
    base = os.path.basename(hub_text.clean(name or "").replace("\\", "/").replace("\n", " "))
    stem = os.path.splitext(base)[0]
    stem = re.sub(r"[^\w .()-]", "", stem).strip(" .")[:80].rstrip(" .")
    return f"{stem or 'file'}.{kind}"


def _file_version(conn, viewer: Viewer, page: dict, rev: int | None, note: str,
                  reread: bool, reviewed: bool) -> int | None:
    """A file change on a published page: the next version, claimed against
    `rev` before the change is made. None if the page moved on."""
    version = page["version"] + 1
    ack_version = _ack_from(page, version, True, reread, page["required"])
    stamp = now_stamp()
    if rev is None or not conn.execute(
            "UPDATE hub_pages SET version=?, ack_version=?, published_at=?, updated_at=?, "
            "updated_by=?, rev=rev+1 WHERE id=? AND rev=? AND version=?",
            (version, ack_version, stamp, stamp, viewer.name, page["id"], rev,
             page["version"])).rowcount:
        return None
    return version


def add_attachment(viewer: Viewer, page: dict, filename: str, data: bytes,
                   alt: str = "", rev: int | None = None, change_note: str = "",
                   reread: bool = False, reviewed: bool = False) -> tuple[dict | None, str]:
    """Store an upload. Returns (row, "") or (None, why not)."""
    if not data:
        return None, "That file is empty."
    if len(data) > MAX_FILE_BYTES:
        return None, "That file is over 15 MB — shrink it or split it."
    kind = sniff_kind(data)
    if not kind:
        return None, ("Only PDFs, photos (JPG/PNG/GIF/WebP) and Word, Excel or "
                      "PowerPoint files (no macros) can be added.")
    text = hub_text.plain(hub_text.docx_text(io.BytesIO(data)))[:50_000] if kind == "docx" else ""
    folder = files_dir()
    os.makedirs(folder, exist_ok=True)
    if shutil.disk_usage(folder).free < len(data) + _MIN_FREE_BYTES:
        return None, "The server's disk is nearly full — ask the Operator."
    stored = secrets.token_hex(16)
    path = _file_path(stored)
    name = safe_filename(filename, kind)
    alt = " ".join(hub_text.clean(alt).split())[:200]
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    try:
        with closing(connect()) as conn, conn:
            version = None
            if page["version"] > 0:
                version = _file_version(conn, viewer, page, rev, change_note, reread, reviewed)
                if version is None:
                    raise _Stale()
            photo_no = None
            if kind in IMAGE_KINDS:
                photo_no = conn.execute(
                    "SELECT COALESCE(MAX(photo_no), 0) + 1 FROM hub_attachments "
                    "WHERE page_id=?", (page["id"],)).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO hub_attachments (page_id, filename, stored_name, kind, size, "
                "sha256, alt, text, uploaded_at, uploaded_by, photo_no) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (page["id"], name, stored, kind, len(data),
                 hashlib.sha256(data).hexdigest(), alt, text, now_stamp(), viewer.name,
                 photo_no))
            if version is None:
                _touch(conn, page["id"], viewer.name)
            else:
                _snapshot(conn, page["id"], version, page,
                          change_note or f"Added {name}", viewer.name, reviewed)
            _audit(conn, viewer.name, "file added", page["id"],
                   f"{name} ({kind})" + (f" — v{version}" if version else ""))
            row = dict(conn.execute("SELECT * FROM hub_attachments WHERE id=?",
                                    (cur.lastrowid,)).fetchone())
    except _Stale:
        _remove_file(stored)
        return None, STALE.split(" Your text")[0] + " Reload it and try again."
    except Exception:
        _remove_file(stored)
        raise
    return row, ""


class _Stale(Exception):
    """The page moved on under a file change: roll it back."""


def remove_attachment(viewer: Viewer, attachment: dict, page: dict, rev: int | None,
                      change_note: str = "", reread: bool = False,
                      reviewed: bool = False) -> str | None:
    """Take a file off its page. A draft's file is deleted outright; a
    published page's is kept for History (new version). Error or None."""
    if page["version"] == 0:
        # Nothing has fingerprinted a draft's file: its bytes go now, but the
        # row stays as a tombstone so its photo number is never handed out again.
        stamp = now_stamp()
        with closing(connect()) as conn, conn:
            conn.execute("UPDATE hub_attachments SET removed_at=?, removed_by=?, purged_at=?, "
                         "text='' WHERE id=?", (stamp, viewer.name, stamp, attachment["id"]))
            _touch(conn, page["id"], viewer.name)
            _audit(conn, viewer.name, "file removed", page["id"], attachment["filename"])
        _remove_file(attachment["stored_name"])
        return None
    try:
        with closing(connect()) as conn, conn:
            version = _file_version(conn, viewer, page, rev, change_note, reread, reviewed)
            if version is None:
                raise _Stale()
            conn.execute("UPDATE hub_attachments SET removed_at=?, removed_by=? WHERE id=?",
                         (now_stamp(), viewer.name, attachment["id"]))
            _snapshot(conn, page["id"], version, page,
                      change_note or f"Removed {attachment['filename']}", viewer.name,
                      reviewed)
            _audit(conn, viewer.name, "file removed", page["id"],
                   f"{attachment['filename']} — v{version} (kept for History)")
    except _Stale:
        return STALE.split(" Your text")[0] + " Reload it and try again."
    return None


def purge_attachment(viewer: Viewer, attachment: dict) -> bool:
    """Operator only: delete a removed file's bytes for good (e.g. it showed
    something it never should have). The row stays as a tombstone so photo
    numbers are never reused and History says what was there."""
    if not viewer.is_operator or not attachment["removed_at"] or attachment["purged_at"]:
        return False
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE hub_attachments SET purged_at=?, text='' WHERE id=?",
                     (now_stamp(), attachment["id"]))
        _audit(conn, viewer.name, "file deleted for good", attachment["page_id"],
               attachment["filename"])
    _remove_file(attachment["stored_name"])
    return True


def attachments(page_id: int) -> list[dict]:
    """The files on the page now."""
    with closing(connect()) as conn:
        return [dict(r) for r in _live_files(conn, page_id)]


def removed_attachments(page_id: int) -> list[dict]:
    """Files taken off the page (kept for History unless purged)."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hub_attachments WHERE page_id=? AND removed_at IS NOT NULL "
            "ORDER BY removed_at DESC, id DESC", (page_id,))]


def get_attachment(attachment_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM hub_attachments WHERE id=?",
                           (attachment_id,)).fetchone()
    return dict(row) if row else None


def photo_map(files: list[dict], url_for_file) -> dict[int, tuple[str, str]]:
    """[photo: N] -> the page's photo number N (fixed at upload, never
    reused, so removing one photo can't shift the others)."""
    return {f["photo_no"]: (url_for_file(f), f["alt"] or f["filename"])
            for f in files if f["kind"] in IMAGE_KINDS and f["photo_no"]}


def version_photo_map(version: dict, url_for_file) -> dict[int, tuple[str, str]]:
    """The photos a past version showed (purged ones are gone)."""
    out = {}
    for f in version["files"]:
        if f.get("photo"):
            att = get_attachment(f["id"])
            if att and not att["purged_at"]:
                out[f["photo"]] = (url_for_file(att), f.get("alt") or f["name"])
    return out


# ---------------------------------------------------------------------------
# Sign-in with first name + clock-in number
# ---------------------------------------------------------------------------

def normalize_code(raw: str) -> str:
    """A clock-in number: 3-12 letters/digits, spaces ignored; "" if not."""
    code = re.sub(r"[\s-]", "", raw or "").upper()
    return code if re.fullmatch(r"[A-Z0-9]{3,12}", code) else ""


def _pepper(conn) -> bytes:
    row = conn.execute("SELECT value FROM meta WHERE key='hub_pepper'").fetchone()
    if row:
        return row["value"].encode()
    value = secrets.token_hex(32)
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('hub_pepper', ?)", (value,))
    return conn.execute("SELECT value FROM meta WHERE key='hub_pepper'"
                        ).fetchone()["value"].encode()


def _code_hash(conn, code: str) -> str:
    return hmac.new(_pepper(conn), code.encode(), hashlib.sha256).hexdigest()


def people() -> list[dict]:
    """The roster with each person's Hub access."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT m.id, m.name, m.role, m.active, a.granted_at, a.granted_by, "
            "(a.member_id IS NOT NULL) AS has_access FROM team_members m "
            "LEFT JOIN hub_access a ON a.member_id = m.id "
            "WHERE m.active=1 OR a.member_id IS NOT NULL ORDER BY m.name COLLATE NOCASE")]


def grant(member_id: int, raw_code: str, who: str) -> str | None:
    """Give (or change) a person's clock-in sign-in. Error message or None."""
    code = normalize_code(raw_code)
    if not code:
        return "A clock-in number is 3–12 letters or digits."
    with closing(connect()) as conn, conn:
        member = conn.execute("SELECT id, name, active FROM team_members WHERE id=?",
                              (member_id,)).fetchone()
        if not member or not member["active"]:
            return "That person isn't on the active roster."
        digest = _code_hash(conn, code)
        other = conn.execute(
            "SELECT m.name FROM hub_access a JOIN team_members m ON m.id = a.member_id "
            "WHERE a.code_hash=? AND a.member_id != ?", (digest, member_id)).fetchone()
        if other:
            return f"That clock-in number is already set for {other['name']}."
        # A new nonce every time: phones signed in under an earlier grant
        # stay signed out, even when the same number is given back.
        conn.execute("INSERT OR REPLACE INTO hub_access (member_id, code_hash, nonce, "
                     "granted_at, granted_by) VALUES (?,?,?,?,?)",
                     (member_id, digest, secrets.token_hex(16), now_stamp(), who))
        _audit(conn, who, "access granted", None, member["name"])
    return None


def revoke(member_id: int, who: str) -> None:
    with closing(connect()) as conn, conn:
        member = conn.execute("SELECT name FROM team_members WHERE id=?",
                              (member_id,)).fetchone()
        if conn.execute("DELETE FROM hub_access WHERE member_id=?", (member_id,)).rowcount:
            _audit(conn, who, "access removed", None, member["name"] if member else "")


def bulk_grant(text: str, who: str, add_missing: bool) -> dict:
    """Lines of "Name, clock-in number" (comma or tab). Names match the
    roster in full, or by first name when only one person has it."""
    out = {"granted": [], "added": [], "unmatched": [], "ambiguous": [], "errors": []}
    roster = shift_db.roster()
    for n, raw in enumerate((text or "").splitlines()[:500], start=1):
        if not raw.strip():
            continue
        fields = [" ".join(f.split()) for f in re.split(r"[,\t]", raw)]
        fields = [f for f in fields if f]
        if len(fields) != 2:
            out["errors"].append(f"line {n}: use exactly “Name, clock-in number” "
                                 "(no extra columns, first name first)")
            continue
        name, code = fields
        if re.search(r"\d", name):
            out["errors"].append(f"line {n}: the name has numbers in it — "
                                 "use “Name, clock-in number”")
            continue
        if not normalize_code(code):
            out["errors"].append(f"line {n} ({name}): that isn't a clock-in number")
            continue
        matches = [m for m in roster if m["name"].casefold() == name.casefold()]
        if not matches and " " not in name:
            matches = [m for m in roster
                       if m["name"].split()[0].casefold() == name.casefold()]
        if len(matches) > 1:
            out["ambiguous"].append(name)
            continue
        if not matches:
            if not add_missing:
                out["unmatched"].append(name)
                continue
            shift_db.add_member(name, touch_existing=False)
            member = next((m for m in shift_db.roster(include_inactive=True)
                           if m["name"].casefold() == name.casefold()), None)
            if not member:
                out["errors"].append(f"line {n} ({name}): couldn't add to the roster")
                continue
            if not member["active"]:          # someone coming back
                shift_db.set_member_active(member["id"], True)
            roster.append(member)
            out["added"].append(member["name"])
            matches = [member]
        error = grant(matches[0]["id"], code, who)
        if error:
            out["errors"].append(f"line {n} ({matches[0]['name']}): {error}")
        else:
            out["granted"].append(matches[0]["name"])
    return out


def member_for_login(name: str, raw_code: str) -> dict | None:
    """The active person with Hub access whose full or first name matches
    and whose clock-in number this is."""
    code = normalize_code(raw_code)
    typed = " ".join((name or "").split()).casefold()
    if not code or not typed:
        return None
    with closing(connect()) as conn:
        digest = _code_hash(conn, code)
        rows = conn.execute(
            "SELECT m.id, m.name, a.code_hash, a.nonce FROM hub_access a JOIN team_members m "
            "ON m.id = a.member_id WHERE m.active=1").fetchall()
    for r in rows:
        full = r["name"].casefold()
        if typed in (full, full.split()[0]) and hmac.compare_digest(r["code_hash"], digest):
            return {"id": r["id"], "name": r["name"], "tag": r["nonce"]}
    return None


def member_session(member_id: int) -> dict | None:
    """The signed-in member's current name + session nonce (None = no longer
    allowed in: off the roster or access removed; a changed nonce means
    access was re-granted or they left the roster since this sign-in)."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT m.id, m.name, a.nonce FROM hub_access a JOIN team_members m "
            "ON m.id = a.member_id WHERE m.id=? AND m.active=1", (member_id,)).fetchone()
    return {"id": row["id"], "name": row["name"], "tag": row["nonce"]} if row else None
