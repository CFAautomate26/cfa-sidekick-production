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
salted hash of it is held, and only while the limit needs it. The address
is the one the hosting platform's proxy reports, never one the visitor
can type (client_address). If that turns out to be a private address (a
proxy of the platform's own, because PEOPLE_PROXY_HOPS does not fit this
server), every visitor would share one count: the per-address limits are
then left off rather than let one stranger lock everyone out, the limits
on everyone together still hold, and the Operator is warned.

Nothing here prints a name, a contact detail, an answer or a link, and an
error on these pages is answered here so that the server's error log
never carries the address of an applicant's private link. The server
this app is started with (`python app.py`) also writes a line for every
request, with its address: mask_request_log() takes the private link out
of those lines too. A log kept by the hosting platform in front of the
app is outside this file's reach (docs/people.md says what to check).
"""

import hashlib
import ipaddress
import logging
import os
import re
import secrets
import threading
from urllib.parse import urlparse

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

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


# ---------------------------------------------------------------------------
# The server's own request log
# ---------------------------------------------------------------------------

# A private link as it can reach the log: "/" may arrive as "%2F" or be
# doubled (a mail scanner or a messaging app re-encoded the link), in any
# letter case. Everything after "careers/a/" goes, up to the end of the
# address: a link the server answers and one it turns away are logged alike.
_PRIVATE_LINK = re.compile(r"(careers(?:/|%2f)+a(?:/|%2f)+)[^\s\x1b]*?(?=[\"')]*(?:[\s\x1b]|$))", re.I)
# The secret part itself is 32 hex characters (people_db.new_token). On a
# line about the careers pages any such run goes too, so a link bent some
# other way ("/careers/x/../a/...") or repeated after a "?" is not logged.
_LINK_TOKEN = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.I)
_CAREERS = re.compile(r"careers", re.I)
_TOKEN_VALUE = re.compile(r"([?&]token=)[^\s&#\"']+")


def mask_addresses(line: str) -> str:
    """A log line with the secret part of any address taken out: the last
    part of an applicant's private link, and the token on a /scheduled
    call (which opens the Team Hub backup)."""
    line = _PRIVATE_LINK.sub(r"\1[removed]", str(line))
    if _CAREERS.search(line):
        line = _LINK_TOKEN.sub("[removed]", line)
    return _TOKEN_VALUE.sub(r"\1[removed]", line)


class RequestLogMask(logging.Filter):
    """Rewrites each line of the built-in server's request log before it is
    written, so a private link is never in the hosting log."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg, record.args = mask_addresses(record.getMessage()), ()
        except Exception:
            record.msg, record.args = "request (line withheld)", ()
        return True


def mask_request_log() -> None:
    """Put the mask on the built-in server's request logger, once. app.py
    calls this when it registers these pages."""
    logger = logging.getLogger("werkzeug")
    if not any(isinstance(f, RequestLogMask) for f in logger.filters):
        logger.addFilter(RequestLogMask())


PROXY_HOPS_ENV = "PEOPLE_PROXY_HOPS"


def proxy_hops() -> int:
    """How many proxies of the hosting platform stand between a visitor and
    this app (PEOPLE_PROXY_HOPS, 0 to 5; 1 unless set). Each proxy adds the
    address it saw to the right-hand end of X-Forwarded-For."""
    raw = os.getenv(PROXY_HOPS_ENV, "").strip()
    return int(raw) if raw in ("0", "1", "2", "3", "4", "5") else 1


def client_address(forwarded: str | None, remote_addr: str | None, hops: int | None = None) -> str:
    """The address a limit is counted against: the one the platform's own
    proxy wrote, `hops` places from the right-hand end of X-Forwarded-For.
    Never the left-hand end: a proxy adds to that header, it does not
    replace it, so everything to the left is whatever the sender typed.
    With no proxy (hops 0), or a header shorter than expected, the
    socket's own address."""
    hops = proxy_hops() if hops is None else hops
    chain = [part.strip() for part in (forwarded or "").split(",") if part.strip()]
    if hops < 1 or len(chain) < hops:
        return (remote_addr or "")[:64]
    return chain[-hops][:64]


# Addresses that are never a visitor's own on the public internet: private
# networks, the carrier-shared range, this machine, link-local.
_NOT_A_VISITOR = tuple(ipaddress.ip_network(block) for block in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "0.0.0.0/8", "::1/128", "fc00::/7", "fe80::/10"))


def tells_visitors_apart(address: str) -> bool:
    """Is this an address a limit can fairly be counted against? Not when
    it is empty, unreadable, or inside a private or reserved range: that is
    a proxy in front of the app (PEOPLE_PROXY_HOPS is wrong for this
    server), and every visitor would share one count, so one stranger
    could use it up for everyone."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return not any(ip.version == block.version and ip in block for block in _NOT_A_VISITOR)


def _counted_as(address: str) -> str:
    """What a limit is keyed on: the address, or for IPv6 its /64 (one
    household or phone is handed a whole /64, not one address)."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return address or ""
    if ip.version == 6 and not getattr(ip, "ipv4_mapped", None):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(getattr(ip, "ipv4_mapped", None) or ip)


def _address_key() -> str | None:
    """A salted hash of where the request came from (see client_address),
    or None when that address cannot tell one visitor from another (see
    tells_visitors_apart): the per-address limits are then not applied,
    and only the limits on everyone together hold. The address itself is
    never kept anywhere."""
    address = client_address(request.headers.get("X-Forwarded-For"), request.remote_addr)
    if not tells_visitors_apart(address):
        return None
    return hashlib.sha256(_salt + _counted_as(address).encode("utf-8", "replace")).hexdigest()[:24]


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


def _take_application_slot(key: str | None, now: float) -> bool:
    """Count one application against the limits; False when one is used
    up. key None (an address that cannot tell visitors apart) is counted
    only against the limits on everyone together."""
    with _lock:
        mine = [t for t in _applied.get(key, []) if now - t < HOUR] if key else []
        _applied_all[:] = [t for t in _applied_all if now - t < DAY]
        last_hour = sum(1 for t in _applied_all if now - t < HOUR)
        if (len(mine) >= PER_ADDRESS_AN_HOUR or last_hour >= ALL_AN_HOUR
                or len(_applied_all) >= ALL_A_DAY):
            return False
        if key:
            _trim(_applied, now, HOUR)
            _applied[key] = mine + [now]
        _applied_all.append(now)
        return True


def _too_many_wrong_links(key: str | None, now: float) -> bool:
    if not key:
        return False
    with _lock:
        return sum(1 for t in _wrong_links.get(key, []) if now - t < HOUR) >= WRONG_LINKS_AN_HOUR


def _count_wrong_link(key: str | None, now: float) -> None:
    if not key:
        return
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


@careers_bp.errorhandler(Exception)
def _went_wrong(e):
    """Anything a careers page raises ends here, so the server's own error
    log never writes the address that was asked for: an applicant's private
    link is in that address. Only the kind of error is printed."""
    if isinstance(e, HTTPException):
        if e.code and e.code < 500:
            return _message(e.code, "Please try again",
                            "That didn't work. Go back to the posting and try once more.")
    else:
        people_db.report_fault("careers page", e)
    return _message(500, "Something went wrong on our side",
                    "Please try again in a few minutes. If you were sending a form, send it again.")


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
    conf = hdb.settings()
    postings = [{"slug": p["slug"], "title": p["title"], "pay": hdb.pay_label(p, conf),
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
        return render_template("careers/thanks.html", **thanks)
    fields, problems = hdb.clean_application(form)
    if problems:
        return _form(view, fields, problems, 400)
    if not _take_application_slot(_address_key(), _now()):
        return _message(429, "Please try again later",
                        "We have had a lot of applications in the last while. Try again in an "
                        f"hour, or contact {view['contact']}.")
    result = hdb.apply(posting_row["id"], form, ai_shown=form.get("ai") == "1")
    if result["status"] == "created":
        # The same answer whether or not this email or phone has applied
        # before: this page must never tell a stranger who has applied.
        return redirect(url_for("careers.private", token=result["token"]))
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
