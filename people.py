"""The People section of Team Hub: leader reviews and hiring, for leaders
only, mounted under /shift.

This module is the platform both features stand on: the gate (a signed-in
leader, then their PIN again, because these pages hold private information
about people), who may open what (the Operator, plus the leaders the
Operator has granted "hiring" or "reviewer": never role alone, never name
alone), and the Operator's own pages (who has access, our standards,
settings, the activity log, the backup). The features add their routes
through register(bp): people_reviews (/shift/reviews...) and people_hiring
(/shift/hiring...). The public careers pages are a separate blueprint
(careers.py).

Everything about a named person is stored by people_db.py in its own
database file, never in the shift database or its backups. Like the Hub,
these pages run no scripts and are never cached or framed.
"""

import hmac
import importlib
import json
import os
from dataclasses import dataclass
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

import hub_text
import people_ai
import people_db
import shift_app
import shift_db
import teamhub_nav

people_bp = Blueprint("people", __name__, url_prefix="/shift")

# Which features loaded ("reviews", "hiring"). Filled at the bottom of this
# module, before the app registers the blueprint.
FEATURES: set[str] = set()
_FEATURE_MODULES: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Who's here
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Viewer:
    kind: str                 # "operator" | "leader"
    leader_id: int | None     # None for the Operator
    name: str                 # sign-in name ("Operator" for the master login)
    role: str                 # "admin" | "lead"
    caps: frozenset           # the Operator: operator, hiring, reviewer; a leader: their grants

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"


OPERATOR_CAPS = frozenset({"operator", *people_db.CAPABILITIES})


def viewer() -> Viewer:
    """Whoever is signed in, as the gate found them for this request."""
    return g.people_viewer


def can(cap: str) -> bool:
    return cap in viewer().caps


def actor() -> str:
    """The sign-in name, for *_by stamps and the activity log."""
    return viewer().name


def requires(cap: str):
    """Put directly under the route decorator: only viewers holding `cap`
    ("operator", "hiring" or "reviewer") get the page."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not can(cap):
                flash("That page isn't available on your sign-in.")
                return redirect(url_for("people.home"))
            return view(*args, **kwargs)
        return wrapped
    return decorate


def deny(endpoint: str = "people.home"):
    """One answer for "not there" and "not yours", so a guess at an id
    learns nothing."""
    flash("That isn't here, or it isn't yours to open.")
    return redirect(url_for(endpoint))


# ---------------------------------------------------------------------------
# The gate, and the lock
# ---------------------------------------------------------------------------

_OPEN_WHILE_LOCKED = {"people.unlock", "people.lock"}
_NEXT_PREFIXES = ("/shift/people", "/shift/hiring", "/shift/reviews")


def _now() -> int:
    return int(shift_db.now_local().timestamp())


def _identity(who: Viewer) -> str:
    return "op" if who.is_operator else f"l:{who.leader_id}"


def _lock_minutes() -> int:
    try:
        return int(people_db.get_setting("lock_minutes"))
    except Exception:
        return int(people_db.SETTINGS["lock_minutes"])


UNLOCK_MAX_HOURS = 8             # the PIN is asked for again this long after it was entered, whatever happens


def _unlocked(who: Viewer) -> bool:
    """The PIN was entered again, by this same sign-in, recently enough,
    and the server still holds that unlock. The cookie alone is never
    enough: "Lock now" and signing out remove the server's record, so a
    copy of the cookie taken while People was open stops working too."""
    stamp = session.get("people_unlock_at")
    if stamp is None or session.get("people_unlock_for") != _identity(who):
        return False
    try:
        idle = _now() - int(stamp)
    except (TypeError, ValueError):
        return False
    limit = _lock_minutes() * 60
    if not -60 <= idle <= limit:
        return False
    return people_db.unlock_valid(session.get("people_unlock_id"), _identity(who), _now(), limit,
                                  UNLOCK_MAX_HOURS * 3600)


@people_bp.before_app_request
def _lock_when_signing_out_or_in():
    """Signing out of Team Hub (or signing in as someone else) closes the
    People lock on the server, for this browser and for any copy of its
    cookie. Runs for every request, so it must never raise."""
    try:
        if request.method == "POST" and request.endpoint in ("shift.logout", "hub.logout",
                                                             "shift.login", "hub.login"):
            people_db.unlock_close(session.get("people_unlock_id"))
    except Exception:
        pass
    return None


def _cross_site() -> bool:
    """A form post that did not come from one of our own pages."""
    origin = request.headers.get("Origin")
    if origin is not None and urlparse(origin).netloc != request.host:
        return True
    return request.headers.get("Sec-Fetch-Site", "").strip().lower() in ("cross-site", "same-site")


@people_bp.before_request
def _gate():
    if shift_app.shared_device_expired():
        return redirect(shift_app.front_door_url())
    if not session.get("shift_name"):
        if session.get("hub_member_id"):
            # A clock-in sign-in only ever opens team-member content.
            flash("That part of Team Hub is for leaders.")
            return redirect(url_for("hub.home") if teamhub_nav.hub_up()
                            else url_for("shift.login"))
        return redirect(shift_app.front_door_url(shift_app.resume_path()))
    if not shift_app.leader_session_valid():
        return redirect(shift_app.front_door_url())
    reading = request.method in ("GET", "HEAD", "OPTIONS")
    if not reading and _cross_site():
        flash("That didn't come from Team Hub. Please try again.")
        return redirect(url_for("people.home"))
    people_db.ensure()
    if shift_app._is_operator():
        who = Viewer("operator", None, shift_app.OPERATOR_NAME, "admin", OPERATOR_CAPS)
    else:
        leader_id, name = session.get("shift_leader_id"), session.get("shift_name", "")
        who = Viewer("leader", leader_id, name, session.get("shift_role", "lead"),
                     people_db.capabilities(leader_id, name))
    g.people_viewer = who
    if request.endpoint in _OPEN_WHILE_LOCKED:
        return None
    if _unlocked(who):
        session["people_unlock_at"] = _now()
        g.people_unlocked = True
        return None
    if reading:
        return redirect(url_for("people.unlock", next=request.full_path.rstrip("?")))
    # A form post is never carried out while locked. After the PIN they
    # land back on the page the form was on, to try again.
    flash("People pages locked themselves. Enter your PIN and try again.")
    came_from = urlparse(request.referrer or "")
    back = _safe_next(came_from.path) if came_from.netloc == request.host else ""
    return redirect(url_for("people.unlock", next=back or None))


@people_bp.after_request
def _headers(resp):
    # These pages hold private information about people: never cached,
    # framed or sniffed, and (like the Hub) they run no scripts at all.
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


@people_bp.errorhandler(OverflowError)
def _id_out_of_range(e):
    # <int:...> ids are unbounded but SQLite integers are not: a hand-typed
    # huge id is a page that isn't there, never a 500.
    return deny()


@people_bp.errorhandler(RequestEntityTooLarge)
def _too_big(e):
    if request.endpoint == "people.standards_import":
        flash("That file is too big. A standards file is 256 KB at most.")
        return redirect(url_for("people.standards"))
    flash("That was too much to save at once. Try a smaller file or less text.")
    return redirect(url_for("people.home"))


def _day(stamp) -> str:
    """'2026-10-22 14:05' or '2026-10-22' -> 'Oct 22' (with the year when it
    isn't this one)."""
    try:
        d = date.fromisoformat(str(stamp or "")[:10])
    except ValueError:
        return str(stamp or "")
    this_year = shift_db.now_local().year
    return f"{d:%b} {d.day}" + ("" if d.year == this_year else f", {d.year}")


def _when(stamp) -> str:
    """'2026-10-22 14:05' -> 'Oct 22, 2:05 pm'."""
    clock = str(stamp or "")[11:16]
    try:
        hour, minute = int(clock[:2]), int(clock[3:5])
    except ValueError:
        return _day(stamp)
    return f"{_day(stamp)}, {hour % 12 or 12}:{minute:02d} {'am' if hour < 12 else 'pm'}"


# App-wide filters, so the shared macros and the careers pages can use them.
people_bp.add_app_template_filter(_day, "pp_day")
people_bp.add_app_template_filter(_when, "pp_when")


def _nav() -> dict | None:
    """The Team Hub chrome, built exactly as /shift builds it. If that ever
    fails, the page still renders, without the menu."""
    if not session.get("shift_name"):
        return None
    try:
        return shift_app._nav()
    except Exception:
        return None


@people_bp.context_processor
def _globals():
    who = getattr(g, "people_viewer", None)
    return {
        "nav": _nav(),
        "viewer": who,
        "people_features": frozenset(FEATURES),
        "people_unlocked": bool(getattr(g, "people_unlocked", False)),
        "people_lock_minutes": _lock_minutes() if who else 0,
        "people_lock_hours": UNLOCK_MAX_HOURS,
        "people_at_risk": bool(shift_app.DATA_AT_RISK
                               and not os.getenv("PEOPLE_DB_PATH", "").strip()),
    }


# ---------------------------------------------------------------------------
# Unlock / lock
# ---------------------------------------------------------------------------

def _safe_next(raw: str | None) -> str:
    """Only ever back into People pages, never off-site."""
    target = raw or ""
    if ("//" in target or "\\" in target or not target.isprintable()
            or target.startswith(("/shift/people/unlock", "/shift/people/lock"))):
        return ""
    for prefix in _NEXT_PREFIXES:
        if target == prefix or target.startswith((prefix + "/", prefix + "?")):
            return target
    return ""


# PINs tried at the lock are counted in the People store (people_db.pin_try),
# apart from wrong PINs at sign-in. Only someone already signed in can add
# to this count, and it is kept against that sign-in ("op" or
# "l:<leader id>"), so nobody on the public sign-in page can shut a leader
# out of People by guessing at their name. Each try is counted BEFORE its
# PIN is checked, in one write transaction, so the limit holds when many
# guesses arrive at the same moment, after a restart, and across server
# processes.
UNLOCK_MAX_MISSES = 5
UNLOCK_HOLD_SECONDS = 600


@people_bp.route("/people/unlock", methods=["GET", "POST"])
def unlock():
    who = viewer()
    target = _safe_next(request.values.get("next"))
    if request.method == "GET":
        if _unlocked(who):
            return redirect(target or url_for("people.home"))
        return render_template("people/unlock.html", next_url=target)
    again = url_for("people.unlock", next=target or None)
    # The box is not called "pin" and is not a password box, so a browser
    # that saved the sign-in PIN does not fill it in for whoever holds the phone.
    pin = (request.form.get("unlock_code") or request.form.get("pin") or "").strip()
    if not pin:
        flash("Enter your PIN.")
        return redirect(again)
    # Five wrong PINs hold the lock for ten minutes. This try is counted
    # first and only then is its PIN checked: a sixth is never checked,
    # even when it arrives in the same instant as the other five. The
    # right PIN clears the count.
    identity = _identity(who)
    nth = people_db.pin_try(identity, _now(), UNLOCK_MAX_MISSES, UNLOCK_HOLD_SECONDS)
    if not nth:
        flash("Too many wrong PINs. Try again in 10 minutes.")
        return redirect(again)
    if who.is_operator:
        master = shift_app.SHIFT_ADMIN_PIN
        ok = bool(master) and hmac.compare_digest(pin.encode(), master.encode())
    else:
        leader = shift_db.verify_leader(who.name, pin) if who.leader_id else None
        ok = bool(leader) and leader["id"] == who.leader_id
    if not ok:
        people_db.audit(who.name, "wrong PIN at the lock")
        if nth == UNLOCK_MAX_MISSES:
            people_db.audit(who.name, "lock held after wrong PINs")
        flash("Wrong PIN.")
        return redirect(again)
    people_db.pin_clear(identity)
    people_db.unlock_close(session.get("people_unlock_id"))
    session["people_unlock_at"] = _now()
    session["people_unlock_for"] = identity
    session["people_unlock_id"] = people_db.unlock_open(identity, _now(), UNLOCK_MAX_HOURS * 3600)
    people_db.audit(who.name, "unlocked")
    return redirect(target or url_for("people.home"))


@people_bp.route("/people/lock", methods=["POST"])
def lock():
    people_db.unlock_close(session.pop("people_unlock_id", None))
    session.pop("people_unlock_at", None)
    session.pop("people_unlock_for", None)
    flash("People pages are locked.")
    return redirect(url_for("shift.today"))


# ---------------------------------------------------------------------------
# What the features add, for the home page and the menu
# ---------------------------------------------------------------------------

_PAGE_URLS: dict[str, str | None] = {}


def _page_url(path: str) -> str | None:
    """Where a feature's page lives, or None when that feature didn't load:
    a link must never point at a page that isn't there."""
    if path not in _PAGE_URLS:
        found = None
        for rule in current_app.url_map.iter_rules():
            if (rule.endpoint.startswith("people.") and "GET" in (rule.methods or ())
                    and not rule.arguments and rule.rule.rstrip("/") == path):
                found = rule.rule
                break
        _PAGE_URLS[path] = found
    return _PAGE_URLS[path]


def _feature_counts(kind: str, leader_id: int | None, name: str, caps: frozenset) -> dict:
    """Counts from the features that loaded, each asked on its own so one
    feature's trouble never hides the other. A viewer only ever gets the
    counts for what they may open."""
    counts: dict[str, int] = {}
    for feature, module in _FEATURE_MODULES.items():
        if feature == "hiring" and "hiring" not in caps:
            continue
        try:
            got = dict(module.nav_counts(kind, leader_id, name) or {})
            more = getattr(module, "extra_counts", None)     # optional: what needs attention
            if more:
                got.update(more(kind, leader_id, name) or {})
            if feature == "reviews" and "reviewer" not in caps:
                got = {"mine": got.get("mine", 0)}       # their own review only
            counts.update({key: max(int(value or 0), 0) for key, value in got.items()})
        except Exception:
            continue
    try:
        # Everything of their own a leader can open, waiting or not, so the
        # link to a review they have already confirmed stays.
        own = getattr(_FEATURE_MODULES.get("reviews"), "own_count", None)
        if own and kind != "operator":
            counts["mine_all"] = max(int(own(kind, leader_id, name) or 0), 0)
    except Exception:
        pass
    return counts


_BADGES = (                     # first one with a count wins; red = someone is waiting
    ("concerns", "{n} to follow up", "b-red"),
    ("notices_overdue", "{n} notice{s} overdue", "b-red"),
    ("attention", "{n} posting{s} to fix", "b-red"),
    ("on_hold", "{n} on hold", "b-red"),
    ("new", "{n} new", "b-red"),
    ("notices_due", "{n} notice{s} due", "b-tan"),
    ("notices_follow_up", "{n} to tell", "b-tan"),
    ("past_due", "{n} past due", "b-tan"),
    ("drafts", "{n} to finish", "b-tan"),
)


def _badge(counts: dict) -> tuple[str, str]:
    for key, text, tone in _BADGES:
        n = counts.get(key, 0)
        if n:
            return text.format(n=n, s="" if n == 1 else "s"), tone
    return "", "b-tan"


def nav_rows(kind: str, leader_id: int | None, name: str) -> list[dict]:
    """The People rows for the Team Hub menu (teamhub_nav calls this for
    leaders and the Operator only, inside its own guard). "People" for the
    Operator and for leaders holding a capability; "My review" for a leader
    only while the reviews feature has something of their own for them to
    open, with a badge while it is waiting on them."""
    operator = kind == "operator"
    caps = OPERATOR_CAPS if operator else people_db.capabilities(leader_id, name)
    counts = _feature_counts(kind, leader_id, name, caps)
    rows = []
    if operator or caps:
        badge, tone = _badge(counts)
        rows.append({"icon": "users", "label": "People", "url": url_for("people.home"),
                     "badge": badge, "tone": tone})
    mine = counts.get("mine", 0)
    mine_url = _page_url("/shift/reviews/mine")
    if not operator and (mine or counts.get("mine_all")) and mine_url:
        rows.append({"icon": "file", "label": "My review", "url": mine_url,
                     "badge": f"{mine} waiting" if mine else "", "tone": "b-red"})
    return rows


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def _retention_due() -> tuple[int, str | None]:
    """(how many applications are past the months their applicants were
    told, where the Operator removes them). Applicants are told the
    Operator removes their details after that time and nothing does it
    automatically, so the Operator is reminded on the People home page."""
    url = _page_url("/shift/hiring/retention")
    if not url or "hiring" not in FEATURES:
        return 0, None
    try:
        import people_hiring_db
        return len(people_hiring_db.retention()["due"]), url
    except Exception:
        return 0, None


@people_bp.route("/people", strict_slashes=False)
def home():
    who = viewer()
    counts = _feature_counts(who.kind, who.leader_id, who.name, who.caps)
    retention_due, retention_url = _retention_due() if who.is_operator else (0, None)
    return render_template(
        "people/home.html", counts=counts, retention_due=retention_due, retention_url=retention_url,
        reviews_url=_page_url("/shift/reviews") if can("reviewer") else None,
        hiring_url=_page_url("/shift/hiring") if can("hiring") else None,
        mine_url=(_page_url("/shift/reviews/mine")
                  if not who.is_operator and (counts.get("mine") or counts.get("mine_all"))
                  else None),
        has_model=bool(people_db.active_model()) if who.caps else True,
        ai_ready=people_ai.available(),
        wages_to_check=people_db.wages_to_check() if who.is_operator else [])


# ---------------------------------------------------------------------------
# Who has access (the Operator)
# ---------------------------------------------------------------------------

@people_bp.route("/people/access")
@requires("operator")
def access():
    leaders = shift_db.leaders()
    grants = people_db.grants()
    rows, matched = [], set()
    for leader in leaders:
        held = {}
        for grant in grants:
            if people_db.same_person(leader["id"], leader["name"],
                                     grant["leader_id"], grant["leader_name"]):
                held[grant["capability"]] = grant
                matched.add(grant["id"])
        rows.append({"id": leader["id"], "name": leader["name"], "role": leader["role"],
                     "held": held})
    stale = [grant for grant in grants if grant["id"] not in matched]
    return render_template("people/access.html", rows=rows, stale=stale,
                           capabilities=people_db.CAPABILITIES,
                           labels=people_db.CAPABILITY_LABELS)


@people_bp.route("/people/access/grant", methods=["POST"])
@requires("operator")
def access_grant():
    leader_id = _form_id("leader_id")
    # The name comes from the leader's own row, never from the form.
    leader = next((l for l in shift_db.leaders() if l["id"] == leader_id), None)
    capability = request.form.get("capability", "")
    if not leader or capability not in people_db.CAPABILITIES:
        flash("That leader or kind of access isn't there any more.")
    elif people_db.grant(leader["id"], leader["name"], capability, actor()):
        flash(f"{leader['name']} can now open {people_db.CAPABILITY_LABELS[capability].lower()}.")
    else:
        flash(f"{leader['name']} already had that.")
    return redirect(url_for("people.access") + (f"#l{leader_id}" if leader else ""))


@people_bp.route("/people/access/revoke", methods=["POST"])
@requires("operator")
def access_revoke():
    grant_id = _form_id("grant_id")
    if grant_id and people_db.revoke(grant_id, actor()):
        flash("Access removed. It stops on their next tap.")
    else:
        flash("That access was already removed.")
    return redirect(url_for("people.access"))


def _form_id(key: str) -> int | None:
    raw = (request.form.get(key) or "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 12 else None


# ---------------------------------------------------------------------------
# Settings (the Operator)
# ---------------------------------------------------------------------------

# key -> (label, hint, what to say when a value is refused, input type)
SETTING_FIELDS = {
    "employer_name": (
        "Employer name", "Shown on job postings and in what applicants read.",
        "The employer name can't be empty.", "text"),
    "hiring_contact_name": (
        "Hiring contact: name",
        "The named person applicants contact with a question or to ask for an accommodation. "
        "Shown on every posting.",
        "That hiring contact name couldn't be saved.", "text"),
    "hiring_contact_email": (
        "Hiring contact: email", "A posting needs this or a phone number before it can go live.",
        "That hiring contact email doesn't look like an email address.", "email"),
    "hiring_contact_phone": (
        "Hiring contact: phone", "A posting needs this or an email before it can go live.",
        "The hiring contact phone needs 10 to 15 digits.", "tel"),
    "min_wage_general": (
        "Ontario general minimum wage ($ an hour)",
        "A posting can't go live with pay below this. The rate usually changes on October 1: "
        "check the current rate on ontario.ca and update it here.",
        "The general minimum wage needs a dollar amount like 17.95.", "text"),
    "min_wage_student": (
        "Ontario student minimum wage ($ an hour)",
        "Used only when a posting says its lower figure is the student rate. Check the "
        "current rate on ontario.ca each October 1.",
        "The student minimum wage needs a dollar amount like 16.90.", "text"),
    "quick_link_days": (
        "Days an applicant's private link takes answers", "A whole number from 1 to 30.",
        "Days for the private link must be a whole number from 1 to 30.", "number"),
    "retention_months": (
        "Months to keep applications from people who were not hired",
        "Counted from the hiring decision. A whole number from 12 to 120. It can't be shorter "
        "than 12 because a human rights application can generally be made up to a year after "
        "a decision, and sometimes later.",
        "Months to keep applications must be a whole number from 12 to 120.", "number"),
    "lock_minutes": (
        "Minutes before People pages lock",
        "Minutes without a tap before these pages ask for the PIN again. From 5 to 120.",
        "Minutes before the lock must be a whole number.", "number"),
}
SETTING_GROUPS = (
    ("contact", "Store and hiring contact",
     ("employer_name", "hiring_contact_name", "hiring_contact_email", "hiring_contact_phone")),
    ("hiring", "Hiring rules",
     ("min_wage_general", "min_wage_student", "quick_link_days", "retention_months")),
    ("lock", "The lock", ("lock_minutes",)),
)
AI_SWITCHES = ("ai_reviews", "ai_hiring")


_CONTACT_KEYS = ("hiring_contact_name", "hiring_contact_email", "hiring_contact_phone")
CONTACT_NEEDED = ("A posting is live, and its accommodation notice names the hiring contact. Keep a "
                  "name and an email or phone number here, or close the posting first.")


def _hiring_store():
    """The hiring store when that feature loaded, else None: Settings must
    keep working without it."""
    if "hiring" not in FEATURES:
        return None
    try:
        import people_hiring_db
        return people_hiring_db
    except Exception:
        return None


def _contact_left_empty(form) -> bool:
    """Would this save leave a live posting without a named hiring contact
    (a name, and an email or a phone number)?"""
    store = _hiring_store()
    if not store or not any(key in form for key in _CONTACT_KEYS):
        return False
    try:
        if not store.open_postings():
            return False
        after = {key: " ".join((form.get(key) if key in form else people_db.get_setting(key)).split())
                 for key in _CONTACT_KEYS}
    except Exception:
        return False
    return not after["hiring_contact_name"] or not (after["hiring_contact_email"]
                                                    or after["hiring_contact_phone"])


WAGE_LOWER_NEEDS_TICK = ("To lower a minimum wage figure, tick the box under it. A lower figure "
                         "lets a posting with lower pay go live, so check it on ontario.ca first.")


def _lowers_wage(key: str, typed: str) -> bool:
    try:
        return float(" ".join(typed.split())) < float(people_db.get_setting(key))
    except ValueError:
        return False                              # not a number: refused by the store as usual


def _postings_to_fix() -> list[dict]:
    """Live postings that no longer pass the publish check (a wage floor
    went up, the contact changed): [{"id", "title", "problems"}]."""
    store = _hiring_store()
    try:
        return store.needs_attention() if store else []
    except Exception:
        return []


def _address_check() -> dict | None:
    """How the public careers pages would count the address of the request
    being answered right now (the Operator's own), so whoever set up the
    server can check the limit is counted against the right one. None when
    the careers pages did not load."""
    try:
        if "careers" not in current_app.blueprints:
            return None
        import careers
        forwarded = request.headers.get("X-Forwarded-For")
        return {"hops": careers.proxy_hops(), "variable": careers.PROXY_HOPS_ENV,
                "chain": [part.strip()[:64] for part in (forwarded or "").split(",") if part.strip()],
                "counted": careers.client_address(forwarded, request.remote_addr)}
    except Exception:
        return None


@people_bp.route("/people/settings", methods=["GET", "POST"])
@requires("operator")
def settings():
    if request.method == "POST":
        saved, refused = 0, []
        keep_contact = _contact_left_empty(request.form)
        if keep_contact:
            refused.append(CONTACT_NEEDED)
        failing_before = {p["id"] for p in _postings_to_fix()}
        for key in people_db.SETTINGS:
            if key not in request.form or (keep_contact and key in _CONTACT_KEYS):
                continue
            if key in people_db.WAGE_SETTINGS and request.form.get("confirm_lower") != "1" \
                    and _lowers_wage(key, request.form.get(key, "")):
                refused.append(WAGE_LOWER_NEEDS_TICK)
                continue
            if people_db.set_setting(key, request.form.get(key, ""), actor()):
                saved += 1
            elif key in SETTING_FIELDS:
                refused.append(SETTING_FIELDS[key][2])
            else:
                refused.append("That switch couldn't be changed.")
        refused = list(dict.fromkeys(refused))
        for problem in refused:
            flash(problem)
        if not refused:
            flash("Settings saved." if saved else "Nothing was changed.")
        elif saved:
            flash("Everything else was saved.")
        # A live posting is checked against these settings. Say so the
        # moment a change leaves one failing, not only on the Hiring page.
        failing = [p for p in _postings_to_fix() if p["id"] not in failing_before]
        if failing:
            names = ", ".join(f"“{p['title']}”" for p in failing[:5])
            flash(f"{len(failing)} live posting{' no longer passes' if len(failing) == 1 else 's no longer pass'} "
                  f"the publish check after this change: {names}. Open Hiring to fix "
                  f"{'it' if len(failing) == 1 else 'them'}, or close {'it' if len(failing) == 1 else 'them'}.")
        return redirect(url_for("people.settings"))
    return render_template("people/settings.html", settings=people_db.settings_all(),
                           groups=SETTING_GROUPS, fields=SETTING_FIELDS,
                           ai_ready=people_ai.available(), address_check=_address_check(),
                           wage_keys=people_db.WAGE_SETTINGS, wages_to_check=people_db.wages_to_check(),
                           wages_as_of=people_db.WAGES_AS_OF)


@people_bp.route("/people/settings/confirm", methods=["POST"])
@requires("operator")
def settings_confirm():
    """"Still correct today": restamp a minimum wage figure without changing it."""
    if people_db.confirm_setting(request.form.get("key", ""), actor()):
        flash("Confirmed as correct today.")
    else:
        flash("That figure can't be confirmed here.")
    return redirect(url_for("people.settings") + "#hiring")


# ---------------------------------------------------------------------------
# Our standards
# ---------------------------------------------------------------------------

def _standards_page(problems=None, draft=None, status: int = 200):
    model = people_db.active_model()
    return render_template("people/standards.html", model=model, problems=problems or [],
                           draft=draft or {}, versions=people_db.model_versions(),
                           caps=people_db.CAPS), status


@people_bp.route("/people/standards")
def standards():
    if not viewer().caps:                       # the Operator, or a leader with a grant
        flash("That page isn't available on your sign-in.")
        return redirect(url_for("people.home"))
    return _standards_page()


def _download(body: str, filename: str) -> Response:
    resp = Response(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@people_bp.route("/people/standards/import", methods=["POST"])
@requires("operator")
def standards_import():
    request.max_content_length = people_db.MODEL_MAX_BYTES + 64 * 1024
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a standards file first.")
        return redirect(url_for("people.standards"))
    raw = upload.read(people_db.MODEL_MAX_BYTES + 1)
    model_id = None
    if len(raw) > people_db.MODEL_MAX_BYTES:
        problems = ["That file is too big. A standards file is 256 KB at most."]
    else:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            problems = ["We couldn't read that file. A standards file is a JSON text file, "
                        "like the example file on this page."]
        else:
            model_id, problems = people_db.save_model(data, actor())
    if problems:
        return _standards_page(problems=problems, status=400)
    flash(f"Standards loaded as version {people_db.get_model(model_id)['version']}.")
    return redirect(url_for("people.standards"))


@people_bp.route("/people/standards/export", methods=["POST"])
@requires("operator")
def standards_export():
    model = people_db.active_model()
    if not model:
        flash("There are no standards to download yet.")
        return redirect(url_for("people.standards"))
    people_db.audit(actor(), "standards downloaded", "standards", model["id"])
    return _download(people_db.model_export(model["id"]),
                     f"our-standards-v{model['version']}.json")


@people_bp.route("/people/standards/example", methods=["POST"])
@requires("operator")
def standards_example():
    return _download(json.dumps(people_db.PEOPLE_EXAMPLE_MODEL, ensure_ascii=False, indent=1),
                     "example-standards.json")


def _box(key: str) -> str:
    return hub_text.clean(request.form.get(key) or "").strip()


@people_bp.route("/people/standards/edit", methods=["POST"])
@requires("operator")
def standards_edit():
    """Change one standard's wording. It saves a whole new version, so
    reviews already started keep the wording they began with."""
    model = people_db.active_model()
    key = request.form.get("key", "")
    if not model or _form_id("model_id") != model["id"] \
            or key not in {d["key"] for d in model["dimensions"]}:
        flash("The standards changed while that form was open. Check the wording and try again.")
        return redirect(url_for("people.standards"))
    entered = {
        "key": key, "name": " ".join(_box("name").split()), "definition": _box("definition"),
        "anchors": [_box(f"anchor{n}") for n in range(1, people_db.LEVEL_COUNT + 1)],
        "evidence_examples": _box("evidence_examples"),
        "hiring_question": _box("hiring_question"),
        "hiring_anchors": [_box(f"hiring_anchor{n}") for n in range(1, people_db.LEVEL_COUNT + 1)],
    }
    data = json.loads(people_db.model_export(model["id"]))
    for group in data["groups"]:
        for dim in group["dimensions"]:
            if dim["key"] != key:
                continue
            dim.update(name=entered["name"], definition=entered["definition"],
                       anchors=entered["anchors"], evidence_examples=entered["evidence_examples"])
            if entered["hiring_question"] or any(entered["hiring_anchors"]):
                dim["hiring"] = {"question": entered["hiring_question"],
                                 "anchors": entered["hiring_anchors"]}
            else:
                dim.pop("hiring", None)         # both left blank: no hiring question
    model_id, problems = people_db.save_model(data, actor())
    if problems:
        return _standards_page(problems=problems, draft=entered, status=400)
    flash(f"Saved as version {people_db.get_model(model_id)['version']}.")
    return redirect(url_for("people.standards") + f"#s-{key}")


# ---------------------------------------------------------------------------
# Activity log and backup (the Operator)
# ---------------------------------------------------------------------------

AUDIT_PAGE = 200


@people_bp.route("/people/audit")
@requires("operator")
def audit():
    raw = request.args.get("page", "")
    page = max(int(raw), 1) if raw.isascii() and raw.isdigit() and len(raw) <= 4 else 1
    rows = people_db.audit_feed(limit=AUDIT_PAGE + 1, offset=(page - 1) * AUDIT_PAGE)
    return render_template("people/audit.html", rows=rows[:AUDIT_PAGE], page=page,
                           has_more=len(rows) > AUDIT_PAGE)


@people_bp.route("/people/backup", methods=["GET", "POST"])
@requires("operator")
def backup():
    if request.method == "POST":
        return _download(people_db.export_json(actor()),
                         f"people-backup-{shift_db.now_local():%Y-%m-%d}.json")
    last = next((r for r in people_db.audit_feed(limit=1000) if r["action"] == "backup downloaded"),
                None)
    return render_template("people/backup.html", last=last)


# ---------------------------------------------------------------------------
# What People needs from the server before it may load
# ---------------------------------------------------------------------------

# The values of ENV that mean "not the live store". Anything else, and no
# value at all, is treated as production.
NOT_PRODUCTION = ("test", "testing", "dev", "development", "local")


def session_key_problem(environ) -> str | None:
    """Why People must not be registered on this server, or None. The gate
    and the PIN lock both rest on the key that signs the session cookie.
    Without FLASK_SECRET_KEY the app derives that key from SCHEDULE_SECRET
    and the Operator's PIN: SCHEDULE_SECRET travels in the address of the
    scheduled calls and the PIN is a few digits, so someone who learned the
    first could work out the second and sign their own Operator session.
    That is not good enough for records about people, so in production
    People loads only with a dedicated key. app.py asks before it registers
    the blueprint (`environ` is os.environ)."""
    if (environ.get("ENV") or "production").strip().lower() in NOT_PRODUCTION:
        return None
    if len((environ.get("FLASK_SECRET_KEY") or "").strip()) >= 32:
        return None
    return ("People is NOT loaded: set FLASK_SECRET_KEY on the server to a long random value "
            "(32 characters or more) and redeploy. Everyone signs in again once. "
            "/shift, /hub and the bots are unaffected.")


# ---------------------------------------------------------------------------
# The features. Each loads on its own, here, before the app registers the
# blueprint: one that fails is skipped with a warning and People carries on.
# ---------------------------------------------------------------------------

_FEATURE_NAMES = (("reviews", "people_reviews"), ("hiring", "people_hiring"))
_settled: set[str] = set()


def load_features() -> None:
    """Import each feature and let it add its routes, once. Runs when this
    module is imported; app.py calls it again just before it registers the
    blueprint, which only matters when a feature module was imported first
    and imported this one while still half-built: that feature waits for
    the second call instead of being written off."""
    for feature, module_name in _FEATURE_NAMES:
        if feature in _settled:
            continue
        try:
            module = importlib.import_module(module_name)
            if not hasattr(module, "register") \
                    and getattr(getattr(module, "__spec__", None), "_initializing", False):
                continue                        # it is importing us right now: ask again later
            _settled.add(feature)
            module.register(people_bp)
            FEATURES.add(feature)
            _FEATURE_MODULES[feature] = module
        except Exception as e:
            _settled.add(feature)
            print(f"WARNING: People feature {feature} failed to load, the rest of People "
                  f"is unaffected: {e!r}")


load_features()
