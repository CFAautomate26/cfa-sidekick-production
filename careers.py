"""The public careers pages (/careers...): open job postings, a phone-first
two-step application, and each applicant's own private status page.

These pages are for people who do not work here yet, so they share nothing
with the signed-in app: they never read or write the session (no cookie is
ever set), never flash, run no scripts and load nothing from another site.
Everything they keep goes through people_hiring_db into the People
database.

Abuse controls, all in this file: a hidden box that only a robot fills
(what it sends is thanked and thrown away), limits on applications per
address and overall, a limit on wrong private links per address, a 64 KB
cap on what can be sent, and a check that a form came from our own page.
The limits live in memory only, and the address is never stored: only a
salted hash of it is held, and only while the limit needs it.

Nothing here prints a name, a contact detail, an answer or a link.
"""

import hashlib
import re
import secrets
import threading
from urllib.parse import urlparse

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

import hub_text
import people_db
import people_hiring_db as hdb
import shift_db

careers_bp = Blueprint("careers", __name__, url_prefix="/careers")

MAX_BODY = 64 * 1024
PER_ADDRESS_AN_HOUR = 5           # applications from one address
ALL_AN_HOUR = 60                  # applications from everyone
ALL_A_DAY = 300
WRONG_LINKS_AN_HOUR = 20          # unknown private links from one address
MAX_ADDRESSES = 5000              # each table is bounded
HOUR = 3600
DAY = 86400

_SLUG = re.compile(r"[a-z0-9-]{2,80}")
_lock = threading.Lock()
_salt = secrets.token_bytes(16)
_applied: dict[str, list[float]] = {}     # hashed address -> when
_applied_all: list[float] = []
_wrong_links: dict[str, list[float]] = {}


def reset_throttles() -> None:
    """Forget every count (tests call this; a restart does the same)."""
    with _lock:
        _applied.clear()
        _applied_all.clear()
        _wrong_links.clear()


def _now() -> float:
    return shift_db.now_local().timestamp()


def _address_key() -> str:
    """A salted hash of where the request came from: the first hop of
    X-Forwarded-For (the app sits behind a proxy), else the socket's
    address. The address itself is never kept anywhere."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    address = (forwarded or request.remote_addr or "")[:64]
    return hashlib.sha256(_salt + address.encode("utf-8", "replace")).hexdigest()[:24]


def _trim(table: dict, now: float, window: float) -> None:
    """Keep a table bounded: drop addresses with nothing recent, and if it
    is still full, the oldest half."""
    if len(table) < MAX_ADDRESSES:
        return
    for key in [k for k, times in table.items() if not any(now - t < window for t in times)]:
        del table[key]
    if len(table) >= MAX_ADDRESSES:
        for key in list(table)[:MAX_ADDRESSES // 2]:
            del table[key]


def _take_application_slot(key: str, now: float) -> bool:
    """Count one application against the limits; False when one is used up."""
    with _lock:
        mine = [t for t in _applied.get(key, []) if now - t < HOUR]
        _applied_all[:] = [t for t in _applied_all if now - t < DAY]
        last_hour = sum(1 for t in _applied_all if now - t < HOUR)
        if (len(mine) >= PER_ADDRESS_AN_HOUR or last_hour >= ALL_AN_HOUR
                or len(_applied_all) >= ALL_A_DAY):
            return False
        _trim(_applied, now, HOUR)
        _applied[key] = mine + [now]
        _applied_all.append(now)
        return True


def _too_many_wrong_links(key: str, now: float) -> bool:
    with _lock:
        return sum(1 for t in _wrong_links.get(key, []) if now - t < HOUR) >= WRONG_LINKS_AN_HOUR


def _count_wrong_link(key: str, now: float) -> None:
    with _lock:
        _trim(_wrong_links, now, HOUR)
        _wrong_links[key] = [t for t in _wrong_links.get(key, []) if now - t < HOUR] + [now]


def _message(status: int, heading: str, text: str):
    return render_template("careers/message.html", heading=heading, text=text), status


def _not_found():
    return _message(404, "That page isn't here",
                    "The position may have closed, or the link may be mistyped.")


def _cross_site() -> bool:
    """A form that was not sent from one of our own pages."""
    origin = request.headers.get("Origin")
    if origin is not None and urlparse(origin).netloc != request.host:
        return True
    return request.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site"


@careers_bp.before_request
def _guard():
    people_db.ensure()
    if request.method == "POST":
        request.max_content_length = MAX_BODY
        if (request.content_length or 0) > MAX_BODY:
            return _too_big(None)
        if _cross_site():
            return _message(403, "Please try again",
                            "That form didn't come from this page. Open the posting and send it again.")
    return None


@careers_bp.after_request
def _headers(resp):
    # The same strict set as the People pages: never cached, framed or
    # sniffed, and no scripts at all.
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    if resp.mimetype == "text/html":
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'")
    return resp


@careers_bp.errorhandler(RequestEntityTooLarge)
def _too_big(e):
    return _message(413, "That was too much to send",
                    "Shorten what you wrote and send it again.")


@careers_bp.errorhandler(404)
def _missing(e):
    return _not_found()


@careers_bp.errorhandler(405)
def _wrong_method(e):
    return _not_found()


@careers_bp.context_processor
def _globals():
    try:
        employer = people_db.get_setting("employer_name")
    except Exception:
        employer = people_db.SETTINGS["employer_name"]
    return {"employer": employer}


def _open_posting(slug: str) -> dict:
    """The posting, only while it is open: a draft, a closed posting and a
    made-up address all get the same page."""
    posting = hdb.posting_by_slug(slug) if _SLUG.fullmatch(slug or "") else None
    if not posting or posting["status"] != "open":
        abort(404)
    # Whatever is about to be shown is on record first (a setting the
    # posting prints may have changed since the last kept copy).
    hdb.keep_current_copy(posting["id"])
    return posting


@careers_bp.route("/")
def index():
    postings = [{"slug": p["slug"], "title": p["title"], "pay": hdb.pay_line(p),
                 "type_label": hdb.EMPLOYMENT_TYPES.get(p["employment_type"], "")}
                for p in hdb.open_postings()]
    return render_template("careers/index.html", postings=postings)


@careers_bp.route("/<slug>", methods=["GET", "POST"])
def posting(slug):
    view = hdb.public_view(_open_posting(slug))
    if request.method == "POST":                 # nothing is ever sent to a posting itself
        abort(404)
    return render_template("careers/posting.html", view=view, body=hub_text.render(view["description"]))


_BLANK = {"first_name": "", "last_name": "", "email": "", "phone": "", "work_entitled": "",
          "adult": "", "heard_from": "", "referrer": "", "experience": "", "consent": False,
          "availability": {}}


def _form(view: dict, fields: dict, problems: list, status: int = 200):
    return render_template("careers/apply.html", view=view, f=fields, problems=problems,
                           days=hdb.DAYS, dayparts=hdb.DAYPARTS, heard=hdb.HEARD_FROM,
                           adult_reason=hdb.ADULT_REASON), status


@careers_bp.route("/<slug>/apply", methods=["GET", "POST"])
def apply(slug):
    posting_row = _open_posting(slug)
    view = hdb.public_view(posting_row)
    if request.method == "GET":
        return _form(view, _BLANK, [])
    form = request.form
    thanks = dict(title=view["title"], contact=view["contact"])
    if (form.get("website") or "").strip():
        # Only a robot fills the hidden box. It gets the ordinary thanks
        # page, and nothing is kept.
        return render_template("careers/thanks.html", duplicate=False, **thanks)
    fields, problems = hdb.clean_application(form)
    if problems:
        return _form(view, fields, problems, 400)
    if not _take_application_slot(_address_key(), _now()):
        return _message(429, "Please try again later",
                        "We have had a lot of applications in the last while. Try again in an "
                        f"hour, or contact {view['contact']}.")
    result = hdb.apply(posting_row["id"], form, ai_shown=form.get("ai") == "1")
    if result["status"] == "created":
        return redirect(url_for("careers.private", token=result["token"]))
    if result["status"] == "duplicate":
        return render_template("careers/thanks.html", duplicate=True, **thanks)
    if result["status"] == "invalid":
        return _form(view, result["fields"], result["problems"], 400)
    abort(404)                                   # it closed while the form was open


@careers_bp.route("/a/<token>", methods=["GET", "POST"])
def private(token):
    """The applicant's own page: the quick questions once, then status.
    It never shows a score, a note or a stage name."""
    key, now = _address_key(), _now()
    if _too_many_wrong_links(key, now):
        return _message(429, "Please try again later",
                        "Too many links that don't match were tried from this connection. "
                        "Wait an hour and open your link again.")
    mine = hdb.application_by_token(token)
    if not mine:
        _count_wrong_link(key, now)
        abort(404)
    page = dict(mine=mine, token=token, mic_hint=hdb.MIC_HINT, answer_max=hdb.ANSWER_MAX)
    if request.method == "POST":
        if mine["can_answer"]:
            typed = {q["id"]: request.form.get(f"q_{q['id']}", "") for q in mine["questions"]}
            if hdb.save_answers(mine["id"], typed) == "empty":
                return render_template("careers/private.html", typed=typed,
                                       problem="Write an answer to at least one question.", **page), 400
        return redirect(url_for("careers.private", token=token))
    return render_template("careers/private.html", typed={}, problem=None, **page)


@careers_bp.route("/<path:anything>", methods=["GET", "POST"])
def nothing_here(anything):
    abort(404)
