"""Shift leading app for Chick-fil-A Wharncliffe & Wonderland.

A phone-first Huddle-style tool for the leadership team, mounted at /shift:
daily shift checklists (opening/transition/closing with who-did-what
accountability), each leader's two goals for the day, to-dos, shift
notes, and announcements with "Got it" acknowledgments.

Auth: each leader gets a personal PIN (managed in /shift/admin); the
Operator signs in as "Operator" with the SHIFT_ADMIN_PIN env var. Sessions
are signed Flask cookies (app.secret_key is set in app.py) and last 30
days so nobody logs in mid-rush.

Setup guide: docs/shift-leading-app.md
"""

import hmac
import os
import re
import threading
import time
from datetime import date
from functools import wraps
from urllib.parse import urlparse

import requests
from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, session, url_for)

import shift_course
import shift_db
import teamhub_nav

shift_bp = Blueprint("shift", __name__, url_prefix="/shift")

# .strip() guards against invisible whitespace pasted into the Render dashboard.
SHIFT_ADMIN_PIN = os.getenv("SHIFT_ADMIN_PIN", "").strip()
OPERATOR_NAME = "Operator"

# Production data survives deploys only when SHIFT_DB_PATH points at a
# Render persistent disk; warn loudly in the UI when it doesn't.
DATA_AT_RISK = (
    os.getenv("ENV", "production") == "production" and not os.getenv("SHIFT_DB_PATH")
)

# Where to-do completion notifications go. Slack is the reliable path:
# SHIFT_NOTIFY_SLACK_CHANNEL names a channel the CFA Sidekick Slack bot has
# been invited to (posted with the same SLACK_BOT_TOKEN the coverage bot
# uses), and SHIFT_NOTIFY_SLACK_MENTION optionally @-mentions a Slack user
# ID so the post triggers a real phone notification. Email rides FormSubmit,
# whose Cloudflare front bot-challenges server-side posts, so it's opt-in:
# set SHIFT_NOTIFY_EMAIL explicitly to also send email.
SHIFT_NOTIFY_EMAIL = os.getenv("SHIFT_NOTIFY_EMAIL", "").strip()
SHIFT_NOTIFY_SLACK_CHANNEL = os.getenv("SHIFT_NOTIFY_SLACK_CHANNEL", "").strip()
SHIFT_NOTIFY_SLACK_MENTION = os.getenv("SHIFT_NOTIFY_SLACK_MENTION", "").strip()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
# Shout-out cross-posts go to the team Slack channel with the same bot token
# as the coverage flow (the bot must be invited to the channel). Empty
# channel disables the cross-post.
SHIFT_SHOUTOUT_SLACK_CHANNEL = os.getenv("SHIFT_SHOUTOUT_SLACK_CHANNEL", "").strip()
# The team roster can be pulled from a Slack channel's members (needs the
# bot scopes channels:read + users:read). Empty = the workspace's #general.
SHIFT_ROSTER_SLACK_CHANNEL = os.getenv("SHIFT_ROSTER_SLACK_CHANNEL", "").strip()

# In-process login throttle: 5 wrong PINs locks that name for 10 minutes.
# Resets on redeploy and is per-worker — fine for one small gunicorn service.
_MAX_FAILS = 5
_LOCK_SECONDS = 600
_pin_fails: dict[str, list[float]] = {}


def _locked_out(name: str) -> bool:
    now = time.time()
    fails = [t for t in _pin_fails.get(name.lower(), []) if now - t < _LOCK_SECONDS]
    _pin_fails[name.lower()] = fails
    return len(fails) >= _MAX_FAILS


def _record_fail(name: str) -> None:
    now = time.time()
    # Bound the dict: drop names whose failures have all aged out, so a
    # stream of unauthenticated POSTs with unique names can't grow it forever.
    if len(_pin_fails) > 200:
        for stale in [k for k, v in _pin_fails.items()
                      if not v or now - v[-1] > _LOCK_SECONDS]:
            _pin_fails.pop(stale, None)
    _pin_fails.setdefault(name.lower(), []).append(now)


def _clear_fails(name: str) -> None:
    _pin_fails.pop(name.lower(), None)


def app_configured() -> bool:
    """Someone must be able to log in: the admin PIN env var or an existing
    leader account."""
    return bool(SHIFT_ADMIN_PIN) or bool(shift_db.leaders())


def current_name() -> str:
    return session.get("shift_name", "")


def is_admin() -> bool:
    return session.get("shift_role") == "admin"


def _operator_pin_tag() -> str:
    """Ties an Operator session to the current master PIN: changing
    SHIFT_ADMIN_PIN signs every Operator session out."""
    if not SHIFT_ADMIN_PIN:
        return ""
    return hmac.new(str(current_app.secret_key).encode(),
                    b"operator-pin:" + SHIFT_ADMIN_PIN.encode(), "sha256").hexdigest()[:32]


def leader_session_valid() -> bool:
    """Re-check a signed-in leader/Operator against the database on every
    request (the Shift Lead app and the Team Hub both call this). Clears the
    session and returns False when it no longer stands."""
    leader_id = session.get("shift_leader_id")
    if leader_id:
        # Deactivated leaders lose access — and role changes (a demoted
        # admin) take effect — on their next request, not when the cookie
        # expires. A backup restore can renumber leader ids, so sessions
        # minted before the restore must re-login rather than attach to a
        # different row.
        leader = shift_db.get_leader(leader_id)
        if (not leader or not leader["active"]
                or session.get("shift_epoch", "") != shift_db.session_epoch()):
            session.clear()
            return False
        session["shift_role"] = leader["role"]
        return True
    tag = _operator_pin_tag()
    if not tag or not hmac.compare_digest(session.get("shift_pin_tag", ""), tag):
        session.clear()
        return False
    return True


# ---------------------------------------------------------------------------
# Request guards + template context
# ---------------------------------------------------------------------------

_PUBLIC_ENDPOINTS = {"shift.login", "shift.logout", "shift.manifest", "shift.icon"}


@shift_bp.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not app_configured():
        return render_template("shift/not_configured.html"), 200
    if shared_device_expired():
        return redirect(front_door_url())
    if not session.get("shift_name"):
        if session.get("hub_member_id"):
            # A clock-in sign-in only ever opens team-member content.
            flash("That part of Team Hub is for leaders.")
            return redirect(url_for("hub.home") if teamhub_nav.hub_up()
                            else url_for("shift.login"))
        return redirect(front_door_url(resume_path()))
    if not leader_session_valid():
        return redirect(front_door_url())
    return None


# "Shared phone or computer" (ticked at sign-in): the cookie lasts only for
# the browser session, and the session also ends after 30 idle minutes —
# so the office PC can't stay signed in as the Operator for a month.
SHARED_IDLE_SECONDS = 30 * 60


def start_session(shared: bool) -> None:
    """Call right after session.clear() on a successful sign-in."""
    session.permanent = not shared
    if shared:
        session["shared_device"] = True
        session["seen_at"] = int(time.time())


def shared_device_expired() -> bool:
    """Both blueprints call this first: True (and the session cleared) when
    a shared-device session has sat idle too long."""
    if not session.get("shared_device"):
        return False
    now = int(time.time())
    if now - int(session.get("seen_at", 0)) > SHARED_IDLE_SECONDS:
        session.clear()
        flash("Signed out after 30 minutes away (shared phone or computer).")
        return True
    session["seen_at"] = now
    return False


def front_door_url(next_path: str | None = None) -> str:
    """The leader side of Team Hub's front door (or the /shift sign-in when
    the Hub blueprint isn't loaded)."""
    if teamhub_nav.hub_up():
        return url_for("hub.login", next=next_path, **{"as": "leader"})
    return url_for("shift.login", next=next_path)


def resume_path() -> str | None:
    """Where to land after signing in: the page asked for (with its query),
    or — for a form post that can't be replayed — the page it came from."""
    if request.method == "GET":
        return request.full_path.rstrip("?")
    ref = urlparse(request.referrer or "")
    if ref.netloc == request.host and ref.path.startswith(("/shift", "/hub")):
        return ref.path
    return None


def _clean_next() -> str:
    target = request.values.get("next", "")
    if (target.startswith(("/shift", "/hub")) and "//" not in target
            and "\\" not in target and target.isprintable()):
        return target
    return ""


def render_front_door(mode: str, error: str | None = None, picked: str = "",
                      status: int = 200):
    """Team Hub's one sign-in page (templates/shared/front_door.html)."""
    names = [l["name"] for l in shift_db.leaders()]
    if SHIFT_ADMIN_PIN:
        names.append(OPERATOR_NAME)
    return render_template(
        "shared/front_door.html", mode=mode, error=error, picked=picked, names=names,
        next_url=_clean_next(), manifest_url=url_for("shift.manifest"),
        member_ok=teamhub_nav.hub_up(), shared=bool(request.form.get("shared"))), status


# Blueprint-scoped (not app-wide): the bot's own pages must never touch the
# shift database, so a shift-DB failure can't break them.
@shift_bp.errorhandler(OverflowError)
def _id_out_of_range(e):
    # <int:...> URL ids are unbounded, but SQLite integers stop at 2**63-1:
    # a hand-edited huge id is just a page that doesn't exist, never a 500.
    flash("That page doesn't exist.")
    return redirect(url_for("shift.today"))


@shift_bp.context_processor
def inject_shift_globals():
    signed_in = bool(session.get("shift_name"))
    return {
        "shift_name": current_name(),
        "shift_is_admin": is_admin(),
        "shift_is_operator": is_admin() and not session.get("shift_leader_id"),
        "shift_today": shift_db.today_local(),
        "shift_daypart": shift_db.current_daypart(),
        "DAYPARTS": shift_db.DAYPARTS,
        "DAYPART_LABELS": shift_db.DAYPART_LABELS,
        "CHECKLIST_DAYPARTS": shift_db.CHECKLIST_DAYPARTS,
        "FREQUENCY_LABELS": shift_db.FREQUENCY_LABELS,
        "WEEKDAY_LABELS": shift_db.WEEKDAY_LABELS,
        "CHECKLIST_SIDES": shift_db.CHECKLIST_SIDES,
        "CHECKLIST_SIDE_LABELS": shift_db.CHECKLIST_SIDE_LABELS,
        "CHECKLIST_VIEWS": shift_db.CHECKLIST_VIEWS,
        "CHECKLIST_VIEW_LABELS": shift_db.CHECKLIST_VIEW_LABELS,
        "CLOSED_WEEKDAYS": shift_db.CLOSED_WEEKDAYS,
        "check_stamp": shift_db.check_stamp,
        "NOTE_CATEGORIES": shift_db.NOTE_CATEGORIES,
        "data_at_risk": DATA_AT_RISK,
        "unread_announcements": (
            shift_db.unacked_count(current_name()) if signed_in else 0
        ),
        "nav": _nav() if signed_in else None,
    }


def _nav() -> dict:
    leader_id = session.get("shift_leader_id")
    return teamhub_nav.leader_nav("leader" if leader_id else "operator", current_name(),
                                  session.get("shift_role", ""), leader_id)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            flash("That page is for the Operator/admins.")
            return redirect(url_for("shift.today"))
        return view(*args, **kwargs)
    return wrapped


def _valid_date(value: str | None) -> str:
    """A safe YYYY-MM-DD (defaults to the store-local business date)."""
    if value:
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            pass
    return shift_db.today_local()


def _valid_checklist_daypart(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in shift_db.CHECKLIST_DAYPARTS else shift_db.current_daypart()


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _optional_date(value: str | None) -> str:
    """A normalized YYYY-MM-DD, or '' for blank/invalid input — for optional
    dates, where silently substituting today would store a wrong date."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def _safe_next(default_endpoint: str = "shift.today"):
    """Only redirect back to same-app paths (the Shift Lead app or the Team
    Hub), never off-site."""
    target = request.form.get("next", "") or request.args.get("next", "")
    if (target.startswith(("/shift", "/hub")) and "//" not in target
            and "\\" not in target and target.isprintable()):
        return redirect(target)
    return redirect(url_for(default_endpoint))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@shift_bp.route("/login", methods=["GET", "POST"])
def login():
    if not app_configured():
        return render_template("shift/not_configured.html"), 200

    if request.method == "GET":
        # One front door: the Hub's, in leader mode — or right here if the
        # Hub isn't loaded, so leaders can always sign in.
        if teamhub_nav.hub_up():
            return redirect(url_for("hub.login", next=_clean_next() or None,
                                    **{"as": "leader"}))
        return render_front_door("leader")

    if request.method == "POST":
        shared = bool(request.form.get("shared"))
        name = (request.form.get("name") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        error = None

        if not name or not pin:
            error = "Pick your name and enter your PIN."
        elif _locked_out(name):
            error = "Too many wrong PINs. Try again in 10 minutes."
        elif name.lower() == OPERATOR_NAME.lower():
            if SHIFT_ADMIN_PIN and hmac.compare_digest(pin, SHIFT_ADMIN_PIN):
                _clear_fails(name)
                session.clear()
                start_session(shared)
                session["shift_name"] = OPERATOR_NAME
                session["shift_role"] = "admin"
                session["shift_pin_tag"] = _operator_pin_tag()
                return _safe_next()
            error = "Wrong PIN."
            _record_fail(name)
        else:
            leader = shift_db.verify_leader(name, pin)
            if leader:
                _clear_fails(name)
                session.clear()
                start_session(shared)
                session["shift_name"] = leader["name"]
                session["shift_role"] = leader["role"]
                session["shift_leader_id"] = leader["id"]
                session["shift_epoch"] = shift_db.session_epoch()
                return _safe_next()
            error = "Wrong name or PIN."
            _record_fail(name)

        return render_front_door("leader", error, name, 401)


@shift_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("hub.login") if teamhub_nav.hub_up() else url_for("shift.login"))


# ---------------------------------------------------------------------------
# Today dashboard
# ---------------------------------------------------------------------------

@shift_bp.route("/")
def today():
    """A leader's home is their to-dos and nothing else: a greeting, every
    open to-do (ticked off right here) and a box to add one. The Operator,
    who has no list of their own, sees the open ones they handed out,
    soonest due first. Everything else is a tab or the ☰ menu away."""
    day = shift_db.today_local()
    # Opening the app starts the day's checklists (the scorecard counts on
    # it), even though they're a tab away now.
    shift_db.ensure_runs_for_date(day)
    now = shift_db.now_local()
    name = current_name()
    leader_id = session.get("shift_leader_id")       # None: the Operator login
    if leader_id:
        open_tasks = shift_db.tasks_for_leader(leader_id)[0]
    else:
        open_tasks = sorted(
            (t for t in shift_db.tasks_assigned_by(OPERATOR_NAME) if not t["completed_at"]),
            key=lambda t: (t["due_date"] is None, t["due_date"] or "", t["id"]))
    return render_template(
        "shift/today.html",
        eyebrow=_today_eyebrow(shift_db.current_daypart(), day, now),
        greeting=_greeting(now),
        # The Operator login is "Operator", not a name: its greeting goes without.
        first_name=(name.split() or [""])[0] if leader_id else "",
        tasks=open_tasks,
        my_leader_id=leader_id,
        day=day,
        short_date=_short_date,
    )


def _greeting(now) -> str:
    """"Good morning" until noon, "Good afternoon" until 5, then evening
    (a close running past midnight is still the evening)."""
    hour = now.hour
    if 4 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    return "Good evening"


def _daypart_word(daypart: str) -> str:
    """"Lunch (10:30–2)" -> "Lunch"."""
    return shift_db.DAYPART_LABELS.get(daypart, daypart).split(" (")[0]


def _clock(stamp: str) -> str:
    """"13:07" (or a whole now_stamp) -> "1:07"."""
    try:
        h, m = int((stamp or "")[-5:-3]), stamp[-2:]
    except ValueError:
        return stamp or ""
    return f"{h % 12 or 12}:{m}"


def _today_eyebrow(daypart: str, day: str, now) -> str:
    """"Lunch is in session · Fri, Oct 23 · 12:07" — the business date, so a
    1am close still reads as the shift it belongs to."""
    d = date.fromisoformat(day)
    return (f"{_daypart_word(daypart)} is in session · "
            f"{d.strftime('%a, %b')} {d.day} · {_clock(now.strftime('%H:%M'))}")


def _short_date(iso: str | None) -> str:
    """"2026-10-26" -> "Mon, Oct 26" (a bad value comes back as is)."""
    try:
        d = date.fromisoformat(iso or "")
    except ValueError:
        return iso or ""
    return f"{d.strftime('%a, %b')} {d.day}"


def _due_period_runs(runs: list[dict], day: str) -> list[dict]:
    """Weekly/monthly lists worth a card on Today: unfinished, still in use,
    and due — a weekly list from its assigned day on (the weekend if no day
    is assigned), a monthly audit in the month's last 7 days."""
    weekday = date.fromisoformat(day).weekday()
    due = []
    for r in runs:
        if (r["frequency"] == "daily" or not r["scheduled"]
                or (r["total"] and r["done"] == r["total"])):
            continue
        if r["frequency"] == "weekly":
            due_day = r["due_weekday"] if r["due_weekday"] is not None else 5
            if weekday < due_day:
                continue
            r["due_note"] = (f"due {shift_db.WEEKDAY_LABELS[due_day]}"
                             + (" — overdue" if weekday > due_day
                                and r["due_weekday"] is not None else ""))
        else:
            days_left = (date.fromisoformat(r["period_end"])
                         - date.fromisoformat(day)).days
            if days_left >= 7:
                continue
            r["due_note"] = f"due by {r['period_end'][5:]}"
        due.append(r)
    return due


def _can_edit_run(run: dict) -> bool:
    """Lists lock when their period ends, so the scorecard measures what
    was done on the day; the Operator can still correct a past list."""
    return run["is_current"] or _is_operator()


# ---------------------------------------------------------------------------
# Checklists
# ---------------------------------------------------------------------------

@shift_bp.route("/checklists")
def checklists():
    """One daypart's checklists (?daypart=, default the one we're in) on one
    day (?date=, default today). Live (today, the current daypart) it leads
    with the list to open now: a read-only preview of what's left. Ticking
    stays on the run page, so the checklist script never meets a second
    progress block. Then the rest that's due now (critical first, with
    earlier dayparts' lists still owing critical items and the weekly and
    monthly lists that are due), the all-day station sheets, what's coming
    up later today, and this week's and month's lists."""
    today = shift_db.today_local()
    day = _valid_date(request.args.get("date"))
    is_today = day == today
    if is_today:
        shift_db.ensure_runs_for_date(day)
    runs = shift_db.runs_for_date(day)
    # Each leader sees their side of the store's lists (and the ones that
    # are both) unless they tap Show all; the Operator always sees every list.
    leader_id = session.get("shift_leader_id")
    view = ((shift_db.get_leader(leader_id) or {}).get("checklist_side") or "all"
            if leader_id else "all")
    sides = shift_db.sides_for_view(view)
    show_all = request.args.get("lists") == "all"
    if sides and not show_all:
        runs = [r for r in runs if r["side"] in sides]
    parts = shift_db.DAYPARTS
    now_part = shift_db.current_daypart()
    daypart = _valid_checklist_daypart(request.args.get("daypart"))
    if daypart not in parts:                    # all-day sheets show on every daypart
        daypart = now_part
    pos, now_pos = parts.index(daypart), parts.index(now_part)
    live = is_today and daypart == now_part

    def short(dp: str) -> str:
        return shift_db.DAYPART_LABELS[dp].split(" (")[0]

    def nice(iso: str, weekday: bool = False) -> str:
        d = date.fromisoformat(iso)
        return (f"{d:%a}, " if weekday else "") + f"{d:%b} {d.day}"

    def critical_first(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda r: not r["critical_remaining"])   # stable

    weekday = date.fromisoformat(day).weekday()
    for r in runs:
        r["finished"] = bool(r["total"]) and r["done"] == r["total"]
        r["daypart_name"] = short(r["daypart"]) if r["daypart"] in parts else "All day"
        r["due_badge"] = None
        if r["frequency"] == "monthly":
            r["due_by"] = nice(r["period_end"])
        if not is_today or r["frequency"] == "daily" or r["finished"]:
            continue
        if r["frequency"] == "weekly":
            due_day = r["due_weekday"] if r["due_weekday"] is not None else 5
            r["due_badge"] = (f"Due {shift_db.WEEKDAY_LABELS[due_day]}" if weekday < due_day
                              else "Due today" if weekday == due_day or r["due_weekday"] is None
                              else "Overdue")
        else:
            left = (date.fromisoformat(r["period_end"]) - date.fromisoformat(day)).days
            r["due_badge"] = ("Due today" if left <= 0
                              else f"{left} day{'s' if left != 1 else ''} left")

    # A list retired (or rescheduled) after someone ticked it keeps its
    # ticks for the day's record but no longer counts as due — same rule
    # as Today's _due_now.
    daily = [r for r in runs if r["frequency"] == "daily" and r.get("scheduled", True)]
    allday = [r for r in daily if r["daypart"] == "allday"]
    mine = [r for r in daily if r["daypart"] == daypart]
    open_now, also_due, done_now, coming = None, [], [], []
    if live:
        current = critical_first([r for r in mine if not r["finished"]])
        done_now = [r for r in mine if r["finished"]]
        leftovers = [r for r in daily if r["daypart"] in parts[:pos]
                     and r["critical_remaining"] and not r["finished"]]
        for r in leftovers:
            r["from_daypart"] = r["daypart_name"]
        open_now = current[0] if current else None
        also_due = critical_first(current[1:] + leftovers + _due_period_runs(runs, day))
        if open_now is None and also_due:
            open_now = also_due.pop(0)
        coming = [r for r in daily if r["daypart"] in parts[pos + 1:]]

    open_items, open_more = [], 0
    if open_now:
        _, items = shift_db.run_with_items(open_now["id"])
        left = sorted((i for i in items if not i["done"]), key=lambda i: not i["critical"])
        open_items, open_more = left[:3], max(len(left) - 3, 0)

    # The scorecard's headline number: daily-list items checked off over the
    # last 7 finished open days (lists lock when their day ends, so it's real).
    score_done = score_total = score_days = 0
    for card in shift_db.checklist_scorecard():
        if card["frequency"] == "daily":
            recent = card["history"][:7]
            score_days = max(score_days, len(recent))
            score_done += sum(h["done"] for h in recent)
            score_total += sum(h["total"] for h in recent)

    lists = (([open_now] if open_now else []) + also_due) if live else mine
    return render_template(
        "shift/checklists.html", day=day, runs=runs, is_today=is_today, live=live,
        daypart=daypart, now_part=now_part,
        when=("past" if day < today else "future" if day > today
              else "now" if live else "later" if pos > now_pos else "earlier"),
        day_label=nice(day, weekday=True), part_name=short(daypart),
        part_hours=shift_db.DAYPART_LABELS[daypart].partition(" (")[2].rstrip(")"),
        daypart_names={dp: short(dp) for dp in parts},
        mine=mine, open_now=open_now, open_items=open_items, open_more=open_more,
        also_due=also_due, done_now=done_now, allday=allday, coming=coming,
        week=[r for r in runs if r["frequency"] == "weekly"],
        month=[r for r in runs if r["frequency"] == "monthly"],
        list_count=len(lists), finished_count=sum(1 for r in mine if r["finished"]),
        critical_left=sum(r["critical_remaining"] for r in lists),
        allday_done=sum(r["done"] for r in allday),
        allday_total=sum(r["total"] for r in allday),
        score_pct=round(score_done / score_total * 100) if score_total else None,
        score_days=score_days,
        filtered=bool(sides), show_all=show_all,
        view_label=shift_db.CHECKLIST_VIEW_LABELS.get(view, ""),
    )


@shift_bp.route("/checklists/scorecard")
def checklist_scorecard():
    today = shift_db.today_local()
    shift_db.ensure_runs_for_date(today)                   # "in progress" is real
    cards = shift_db.checklist_scorecard()
    areas: dict[str, list[dict]] = {}
    for c in cards:
        areas.setdefault(c["area"], []).append(c)
    return render_template(
        "shift/scorecard.html", areas=areas,
        today_closed=date.fromisoformat(today).weekday() in shift_db.CLOSED_WEEKDAYS)


@shift_bp.route("/checklists/run/<int:run_id>")
def run_detail(run_id):
    run, items = shift_db.run_with_items(run_id)
    if not run:
        flash("That checklist doesn't exist.")
        return redirect(url_for("shift.checklists"))
    return render_template("shift/run.html", run=run, items=items,
                           can_edit=_can_edit_run(run),
                           roster=[m["name"] for m in shift_db.roster()])


@shift_bp.route("/checklists/run/<int:run_id>/owner", methods=["POST"])
def run_owner(run_id):
    run, _ = shift_db.run_with_items(run_id)
    if not run:
        flash("That checklist doesn't exist.")
        return redirect(url_for("shift.checklists"))
    if not _can_edit_run(run):
        flash("That list is closed — its period has ended.")
    else:
        shift_db.set_run_owner(run_id, request.form.get("owner", ""))
    return redirect(url_for("shift.run_detail", run_id=run_id))


@shift_bp.route("/checklists/run/<int:run_id>/verify", methods=["POST"])
def run_verify(run_id):
    run, _ = shift_db.run_with_items(run_id)
    if not run:
        flash("That checklist doesn't exist.")
        return redirect(url_for("shift.checklists"))
    verified = request.form.get("verified") == "1"
    if not _can_edit_run(run):
        flash("That list is closed — its period has ended.")
    elif not shift_db.set_run_verified(run_id, verified, current_name()):
        fresh, _ = shift_db.run_with_items(run_id)
        flash(f"Already checked by {fresh['verified_by']} — nothing changed."
              if verified and fresh["verified_by"] else "Nothing to undo.")
    return redirect(url_for("shift.run_detail", run_id=run_id))


@shift_bp.route("/item/<int:item_id>/toggle", methods=["POST"])
def toggle_item(item_id):
    done = request.form.get("done") == "1"
    run_id = shift_db.item_run_id(item_id)
    if run_id is None:
        if request.form.get("ajax") == "1":
            return {"ok": False}, 404
        flash("That item doesn't exist.")
        return redirect(url_for("shift.checklists"))
    run, _ = shift_db.run_with_items(run_id)
    if not _can_edit_run(run):
        if request.form.get("ajax") == "1":
            return {"ok": False, "locked": True}, 403
        flash("That list is closed — its period has ended, so it can't be "
              "changed (the Operator can correct it).")
        return redirect(url_for("shift.run_detail", run_id=run_id))

    shift_db.set_item_done(item_id, done, current_name())

    if request.form.get("ajax") == "1":
        run, items = shift_db.run_with_items(run_id)
        item = next(i for i in items if i["id"] == item_id)
        return {
            "ok": True,
            "done": bool(item["done"]),
            "done_by": item["done_by"],
            "done_at": item["done_at"],
            "total": len(items),
            "done_count": sum(1 for i in items if i["done"]),
            "critical_remaining": sum(1 for i in items if i["critical"] and not i["done"]),
        }
    return redirect(url_for("shift.run_detail", run_id=run_id) + f"#item-{item_id}")


# ---------------------------------------------------------------------------
# Retired pages
# ---------------------------------------------------------------------------
# Lineups and goals (store goals with targets, then the daily goals) came
# out of the app in October 2026: lineups live on the floor's setup sheets
# and goals are set and seen on the floor, in person. Their tables stay,
# and stay in backups; old links and home-screen shortcuts land on Today.

@shift_bp.route("/lineup")
@shift_bp.route("/goals")
@shift_bp.route("/goals/<int:goal_id>")
def retired_page(goal_id=None):
    return redirect(url_for("shift.today"))


# ---------------------------------------------------------------------------
# Shift notes
# ---------------------------------------------------------------------------

@shift_bp.route("/notes")
def notes():
    category = request.args.get("category") or None
    if category not in shift_db.NOTE_CATEGORIES:
        category = None
    return render_template("shift/notes.html", category=category,
                           feed=shift_db.notes_feed(category=category))


@shift_bp.route("/notes", methods=["POST"])
def note_create():
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Write the note first.")
        return redirect(url_for("shift.notes"))
    category = request.form.get("category", "general")
    if category not in shift_db.NOTE_CATEGORIES:
        category = "general"
    daypart = (request.form.get("daypart") or "").strip().lower()
    if daypart not in shift_db.DAYPARTS:
        daypart = ""
    shift_db.add_note(shift_db.today_local(), daypart, category, body, current_name())
    return redirect(url_for("shift.notes"))


@shift_bp.route("/notes/<int:note_id>/delete", methods=["POST"])
def note_delete(note_id):
    note = shift_db.get_note(note_id)
    if note and (is_admin() or note["author"] == current_name()):
        shift_db.delete_note(note_id)
    elif note:
        flash("Only the author or an admin can delete a note.")
    return redirect(url_for("shift.notes"))


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

@shift_bp.route("/announcements")
def announcements():
    items = [_with_page_link(a) for a in shift_db.active_announcements(reader=current_name())]
    readers = {a["id"]: shift_db.announcement_readers(a["id"]) for a in items} \
        if is_admin() else {}
    return render_template("shift/announcements.html", items=items, readers=readers)


# A Systems & Procedures page linked in an announcement ("…/hub/page/40").
_HUB_PAGE_LINK = re.compile(r"(?:https?://[^\s/]+)?/hub/page/(\d+)(?:[/?#][^\s)\]]*)?")


def _with_page_link(a: dict) -> dict:
    """An announcement whose text links a Systems & Procedures page gets an
    "Open the page" link instead of the raw address (when the Hub is up)."""
    body, page_id = a["body"], None
    match = _HUB_PAGE_LINK.search(body)
    if match and teamhub_nav.hub_up():
        page_id = int(match.group(1))
        head, rest = body[:match.start()].rstrip(), body[match.end():]
        rest = rest if rest[:1] in tuple(".,;:!?)]") else " " + rest.lstrip()
        body = (head + rest).strip()
    return dict(a, body=body, page_id=page_id)


@shift_bp.route("/announcements", methods=["POST"])
@admin_required
def announcement_create():
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Write the announcement first.")
        return redirect(url_for("shift.announcements"))
    shift_db.add_announcement(
        body, current_name(), pinned=request.form.get("pinned") == "1",
        expires_on=_optional_date(request.form.get("expires_on")),
    )
    return redirect(url_for("shift.announcements"))


@shift_bp.route("/announcements/<int:announcement_id>/ack", methods=["POST"])
def announcement_ack(announcement_id):
    if not shift_db.ack_announcement(announcement_id, current_name()):
        flash("That announcement was removed.")
    return _safe_next("shift.announcements")


@shift_bp.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
@admin_required
def announcement_delete(announcement_id):
    shift_db.delete_announcement(announcement_id)
    return redirect(url_for("shift.announcements"))


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

@shift_bp.route("/roster")
def roster():
    return render_template("shift/roster.html",
                           members=shift_db.roster(),
                           inactive=[m for m in shift_db.roster(include_inactive=True)
                                     if not m["active"]])


@shift_bp.route("/roster/add", methods=["POST"])
def roster_add():
    if not shift_db.add_member(request.form.get("name", "")):
        flash("A name is required.")
    return redirect(url_for("shift.roster"))


@shift_bp.route("/roster/<int:member_id>/toggle", methods=["POST"])
def roster_toggle(member_id):
    shift_db.set_member_active(member_id, request.form.get("active") == "1")
    return redirect(url_for("shift.roster"))


# ---------------------------------------------------------------------------
# More (menu page for everything beyond the 5 tabs)
# ---------------------------------------------------------------------------

@shift_bp.route("/more")
def more():
    """The Team Hub menu (also at /hub/menu): served here too so leaders
    keep it even if the Hub blueprint failed to load."""
    return menu_page(_nav(), "shift.today", url_for("shift.manifest"))


def menu_page(nav: dict, home_endpoint: str, manifest_url: str):
    back = request.args.get("from", "")
    if not (back.startswith(("/shift", "/hub")) and "//" not in back and "\\" not in back
            and back.isprintable()) or back.rstrip("?").endswith(("/more", "/menu")):
        back = url_for(home_endpoint)
    return render_template("shared/menu.html", nav=nav, back=back, manifest_url=manifest_url)


# ---------------------------------------------------------------------------
# Leadership development course
# ---------------------------------------------------------------------------

def _can_view_development(leader_id: int) -> bool:
    return is_admin() or session.get("shift_leader_id") == leader_id


def _is_operator() -> bool:
    """The master-PIN login: admin role with no leader row behind it."""
    return is_admin() and not session.get("shift_leader_id")


def operator_required(view):
    """Stricter than admin_required: backups carry every leader's private
    1:1 content, and PIN resets allow impersonation — both belong to the
    Operator master login alone."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_operator():
            flash("That action is for the Operator master login.")
            return redirect(url_for("shift.admin") if is_admin()
                            else url_for("shift.today"))
        return view(*args, **kwargs)
    return wrapped


def _can_view_oneonone(leader_id: int) -> bool:
    """1:1 agendas are private between each leader and the Operator — even
    admin-role leaders see only their own (the Operator's explicit call)."""
    return _is_operator() or session.get("shift_leader_id") == leader_id


LD_CATEGORY = "leadership development"


def _ld_library() -> dict | None:
    """The Library on Leadership Development: the published pages of the
    Hub's "Leadership Development" category, read as this leader (or the
    Operator) so hub_db's own visibility rules decide what's listed. None
    when the Hub isn't loaded or the category is gone — the page then
    just leaves the section out; the Hub's trouble never breaks /shift."""
    if not teamhub_nav.hub_up():
        return None
    try:
        import hub_db
        leader_id = session.get("shift_leader_id")
        viewer = (hub_db.Viewer("leader", leader_id, current_name(),
                                session.get("shift_role", "lead"))
                  if leader_id else hub_db.Viewer("operator", None, OPERATOR_NAME, "admin"))
        category = next((c for c in hub_db.categories(viewer)
                         if " ".join(c["name"].split()).lower() == LD_CATEGORY), None)
        if not hub_db.can_view_category(viewer, category):
            return None
        pages = [p for p in hub_db.pages_for_category(viewer, category["id"])
                 if p["status"] == "published"]
        unread = {p["id"] for p in hub_db.to_read(viewer)}
        groups: dict[str, list[dict]] = {}
        for p in pages:
            groups.setdefault(p["section"] or "General", []).append(
                dict(p, url=url_for("hub.page", page_id=p["id"]), to_read=p["id"] in unread))
        return {
            "groups": list(groups.items()),
            "count": len(pages),
            "new_url": (url_for("hub.new_page", category=category["id"])
                        if viewer.is_editor else None),
        }
    except Exception as e:      # noqa: BLE001 — the Hub's trouble stays the Hub's
        print(f"Leadership Development library unavailable: {e}")
        return None


def _course_progress(modules: list[dict]) -> dict:
    """One leader's course at a glance: per-stage done/total, the stage
    they're on and their next lesson (None once everything's done)."""
    stages = [{"module": m["module"], "done": m["done"], "total": m["total"]}
              for m in modules]
    upcoming = next(((m["module"], l) for m in modules for l in m["lessons"]
                     if not l["done"]), None)
    return {
        "stages": stages,
        "done": sum(s["done"] for s in stages),
        "total": sum(s["total"] for s in stages),
        "stage": upcoming[0] if upcoming else None,
        "next": upcoming[1] if upcoming else None,
    }


@shift_bp.route("/development")
def development():
    if is_admin():
        me = session.get("shift_leader_id")
        leaders = []
        for l in shift_db.leader_course_summary():
            leaders.append(dict(l, me=l["id"] == me,
                                **_course_progress(shift_db.course_overview(l["id"]))))
        leaders.sort(key=lambda l: not l["me"])      # an admin leader sees their own first
        # The course's stages (no leader has id 0, so this is the bare
        # course) and how many leaders have finished each one.
        stages = [{"module": m["module"], "total": m["total"],
                   "finished": sum(1 for l in leaders for s in l["stages"]
                                   if s["module"] == m["module"] and s["done"] == s["total"])}
                  for m in shift_db.course_overview(0)]
        return render_template(
            "shift/development.html",
            leaders=leaders,
            stages=stages,
            total=shift_db.course_lesson_count(),
            course_url=shift_course.COURSE_FOLDER_URL,
            library=_ld_library(),
        )
    leader_id = session.get("shift_leader_id")
    if not leader_id:
        flash("Your login isn't linked to a leader profile — ask the Operator.")
        return redirect(url_for("shift.today"))
    return redirect(url_for("shift.development_leader", leader_id=leader_id))


@shift_bp.route("/development/<int:leader_id>")
def development_leader(leader_id):
    if not _can_view_development(leader_id):
        flash("You can only see your own development page.")
        return redirect(url_for("shift.today"))
    leader = shift_db.get_leader(leader_id)
    if not leader:
        flash("That leader doesn't exist.")
        return redirect(url_for("shift.development"))
    modules = shift_db.course_overview(leader_id)
    progress = _course_progress(modules)
    # The stage being worked on opens; finished and later ones fold away.
    # After a save, the lesson just touched (?lesson=) opens with its stage.
    opened = _optional_int(request.args.get("lesson"))
    return render_template(
        "shift/development_leader.html", leader=leader, modules=modules,
        done=progress["done"], total=progress["total"], next_lesson=progress["next"],
        current_stage=progress["stage"], opened_lesson=opened,
        is_me=session.get("shift_leader_id") == leader_id,
        course_url=shift_course.COURSE_FOLDER_URL, library=_ld_library(),
    )


@shift_bp.route("/development/<int:leader_id>/lesson/<int:lesson_id>", methods=["POST"])
def development_update(leader_id, lesson_id):
    if not _can_view_development(leader_id):
        flash("You can only update your own development page.")
        return redirect(url_for("shift.today"))
    action = request.form.get("action", "")
    note = " ".join((request.form.get("note") or "").split())
    if action in ("complete", "save"):
        if not shift_db.set_lesson_done(lesson_id, leader_id, note, current_name()):
            flash("That lesson doesn't exist any more — reload the page.")
    elif action == "uncomplete":
        shift_db.clear_lesson_done(lesson_id, leader_id)
    return redirect(url_for("shift.development_leader", leader_id=leader_id,
                            lesson=lesson_id) + f"#lesson-{lesson_id}")


# ---------------------------------------------------------------------------
# Leader to-dos
# ---------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _meetings_day(value: str | None, weekday: bool = False) -> str:
    """How a date reads on the Meetings pages: 'Oct 13', or 'Mon, Oct 26'
    with weekday; the year only when it isn't this one. Takes a YYYY-MM-DD
    date or a now_stamp() (its 4am-rollover business date)."""
    if not value:
        return ""
    try:
        d = date.fromisoformat(shift_db.business_date(value) if len(value) > 10
                               else value)
    except ValueError:
        return value
    text = f"{_MONTHS[d.month - 1]}\u00a0{d.day}"     # "Oct 13" never splits
    if d.year != date.fromisoformat(shift_db.today_local()).year:
        text += f", {d.year}"
    return f"{_WEEKDAYS[d.weekday()]}, {text}" if weekday else text


def _meetings_due(due: str | None) -> tuple[str, bool]:
    """(label, urgent) for a to-do's due date: urgent (red) when it is due
    today or overdue."""
    if not due:
        return "No due date", False
    today = date.fromisoformat(shift_db.today_local())
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return f"Due {due}", False
    if d < today:
        return f"Overdue · was due {_meetings_day(due)}", True
    if d == today:
        return "Due today", True
    if (d - today).days == 1:
        return "Due tomorrow", False
    return f"Due {_meetings_day(due, weekday=True)}", False


def _meetings_when(stamp: str | None) -> str:
    """'today 4:12 pm', 'yesterday 4:12 pm' or 'Oct 20' for a now_stamp()."""
    if not stamp:
        return ""
    day = shift_db.business_date(stamp)
    today = date.fromisoformat(shift_db.today_local())
    try:
        ago = (today - date.fromisoformat(day)).days
        hour, minute = int(stamp[11:13]), stamp[14:16]
    except ValueError:
        return stamp
    if ago in (0, 1):
        clock = f"{hour % 12 or 12}:{minute} {'am' if hour < 12 else 'pm'}"
        return f"{'today' if ago == 0 else 'yesterday'} {clock}"
    return _meetings_day(stamp)


# Date wording shared by every Meetings template (1:1s and to-dos).
_MEETINGS_FMT = {"day": _meetings_day, "due": _meetings_due, "when": _meetings_when}


def _back_to_meetings(default: str, anchor: str) -> str:
    """Forms on a leader's Meetings page (their agenda and to-dos inline)
    post back=meetings so the leader lands back where they tapped; without
    it a route keeps its usual page. Only ever /shift/oneonone, which runs
    its own access checks."""
    if request.form.get("back") == "meetings":
        return url_for("shift.oneonone") + anchor
    if request.form.get("back") == "today":          # ticked on the Today home
        return url_for("shift.today")
    return default


# Any leader can assign to-dos and browse the per-leader lists (the
# Operator opened this up from admin-only); completing a task stays with
# its assignee or an admin, and delete stays admin-only.
@shift_bp.route("/todo")
def todo():
    return render_template("shift/todo.html",
                           leaders=shift_db.leader_task_summary(),
                           my_leader_id=session.get("shift_leader_id"),
                           **_MEETINGS_FMT)


@shift_bp.route("/todo/<int:leader_id>")
def todo_leader(leader_id):
    leader = shift_db.get_leader(leader_id)
    if not leader:
        flash("That leader doesn't exist.")
        return redirect(url_for("shift.todo"))
    open_tasks, done_tasks = shift_db.tasks_for_leader(leader_id)
    return render_template("shift/todo_leader.html", leader=leader,
                           open_tasks=open_tasks, done_tasks=done_tasks,
                           own=session.get("shift_leader_id") == leader_id,
                           can_check=_can_view_development(leader_id),
                           **_MEETINGS_FMT)


@shift_bp.route("/todo/<int:leader_id>/assign", methods=["POST"])
def todo_assign(leader_id):
    ok = shift_db.add_task(
        leader_id,
        title=request.form.get("title", ""),
        details=request.form.get("details", ""),
        due_date=_optional_date(request.form.get("due_date")),
        assigned_by=current_name(),
    )
    if not ok:
        flash("A to-do needs a title (and a leader who still exists).")
    return redirect(_back_to_meetings(url_for("shift.todo_leader", leader_id=leader_id),
                                      "#todos"))


def send_completion_slack(task: dict, leader_name: str, remaining: int) -> None:
    """Ping the Operator in Slack that a to-do was completed, using the same
    bot token as the coverage bot (needs chat:write and the bot invited to
    the SHIFT_NOTIFY_SLACK_CHANNEL channel). Best-effort: failures are
    logged and never surface to the person tapping the checkmark."""
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN is missing, cannot send the to-do "
              "completion Slack ping.")
        return
    # Slack mrkdwn: &, < and > must be escaped or a task title could inject
    # a real mention/link (e.g. "<!channel>").
    def esc(text):
        return (str(text).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))

    name = esc(leader_name)
    mention = f"<@{SHIFT_NOTIFY_SLACK_MENTION}> " if SHIFT_NOTIFY_SLACK_MENTION else ""
    lines = [f"{mention}✅ *{name}* completed a to-do: *{esc(task['title'])}*"]
    if task.get("due_date"):
        lines.append(f"Due: {esc(task['due_date'])}")
    lines.append(f"{remaining} still open for {name}" if remaining
                 else f"That was {name}'s last open to-do 🎉")
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if base_url:
        lines.append(f"<{base_url}/shift/todo/{task['leader_id']}|Open their to-do list>")
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": SHIFT_NOTIFY_SLACK_CHANNEL,
                  "text": "\n".join(lines), "unfurl_links": False},
            timeout=15,
        )
        # Slack answers 200 even for errors — the body's ok/error field is
        # what tells the story in the Render logs.
        print(f"To-do completion Slack status: {resp.status_code}, "
              f"body: {resp.text[:300]}")
    except Exception as e:
        print(f"Error sending to-do completion Slack ping: {e}")


def send_completion_email(task: dict, leader_name: str, remaining: int) -> None:
    """Email the Operator that a to-do was completed, via FormSubmit.
    Opt-in (see SHIFT_NOTIFY_EMAIL above). Best-effort: failures are
    logged and never surface to the person tapping the checkmark."""
    payload = {
        "_subject": f"To-do completed: {task['title']}",
        "_template": "table",
        "Leader": leader_name,
        "Task": task["title"],
        "Details": task.get("details") or "—",
        "Due date": task.get("due_date") or "—",
        "Completed by": task.get("completed_by") or "—",
        "Completed at": task.get("completed_at") or "—",
        "Still open for this leader": remaining,
    }
    try:
        resp = requests.post(
            f"https://formsubmit.co/ajax/{SHIFT_NOTIFY_EMAIL}",
            json=payload, timeout=20, headers={"Accept": "application/json"},
        )
        print(f"To-do completion email status: {resp.status_code}")
    except Exception as e:
        print(f"Error sending to-do completion email: {e}")


def _notify_completion(task_id: int) -> None:
    """Fire the completion notifications (Slack and/or email, whichever is
    configured) in the background. Best-effort end to end: the snapshot
    reads and thread spawn are guarded too, so nothing in the notify path
    can turn an already-committed completion into an error page for the
    person tapping the checkmark."""
    try:
        slack_on = bool(SHIFT_NOTIFY_SLACK_CHANNEL)
        email_on = bool(SHIFT_NOTIFY_EMAIL)
        if not (slack_on or email_on):
            return
        task = shift_db.get_task(task_id)
        if not task or not task["completed_at"]:
            return
        leader = shift_db.get_leader(task["leader_id"])
        leader_name = leader["name"] if leader else "(removed)"
        remaining = len(shift_db.tasks_for_leader(task["leader_id"])[0])

        def _send():
            if slack_on:
                send_completion_slack(task, leader_name, remaining)
            if email_on:
                send_completion_email(task, leader_name, remaining)

        thread = threading.Thread(target=_send, daemon=True)
        thread.start()
        if current_app.config.get("TESTING"):
            thread.join(timeout=5)
    except Exception as e:
        print(f"Error preparing to-do completion notification: {e}")


@shift_bp.route("/todo/task/<int:task_id>/toggle", methods=["POST"])
def todo_toggle(task_id):
    task = shift_db.get_task(task_id)
    if not task or not _can_view_development(task["leader_id"]):
        flash("That to-do doesn't exist or isn't yours.")
        return redirect(url_for("shift.today"))
    done = request.form.get("done") == "1"
    shift_db.set_task_done(task_id, done, current_name())
    # Notify the Operator when a LEADER checks something off — not when the
    # Operator marks it done themself during a review.
    if done and session.get("shift_leader_id"):
        _notify_completion(task_id)
    return redirect(_back_to_meetings(
        url_for("shift.todo_leader", leader_id=task["leader_id"]) + f"#task-{task_id}",
        "#todos"))


@shift_bp.route("/todo/task/<int:task_id>/delete", methods=["POST"])
@admin_required
def todo_delete(task_id):
    task = shift_db.get_task(task_id)
    if task:
        shift_db.delete_task(task_id)
    return redirect(url_for("shift.todo_leader", leader_id=task["leader_id"])
                    if task else url_for("shift.todo"))


# ---------------------------------------------------------------------------
# Roster pull from Slack #general (Operator only)
# ---------------------------------------------------------------------------

class SlackRosterError(Exception):
    """A Slack problem worth showing the Operator as-is."""


_SLACK_SCOPE_HELP = (
    "The CFA Sidekick Slack bot needs two more permissions to read the "
    "team list: api.slack.com/apps → CFA Sidekick → OAuth & Permissions → "
    "Bot Token Scopes → add channels:read and users:read → Reinstall to "
    "Workspace. (Setup guide: docs/shift-leading-app.md.)")


def _slack_get(method: str, params: dict) -> dict:
    resp = requests.get(f"https://slack.com/api/{method}",
                        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                        params=params, timeout=15)
    try:
        data = resp.json()
    except ValueError:
        raise SlackRosterError(f"Slack answered {resp.status_code} — try again in a minute.")
    if data.get("ok"):
        return data
    error = data.get("error") or "unknown_error"
    print(f"Slack roster {method} error: {error} {data.get('needed', '')}")
    if error == "missing_scope":
        raise SlackRosterError(_SLACK_SCOPE_HELP)
    if error in ("not_authed", "invalid_auth", "account_inactive", "token_revoked"):
        raise SlackRosterError("Slack rejected the bot token — check SLACK_BOT_TOKEN on Render.")
    if error == "channel_not_found":
        raise SlackRosterError("Slack can't find that channel — check SHIFT_ROSTER_SLACK_CHANNEL on Render.")
    if error == "ratelimited":
        raise SlackRosterError("Slack is busy — try again in a minute.")
    raise SlackRosterError(f"Slack said “{error}” — try again in a minute.")


def _slack_paged(method: str, params: dict, key: str) -> list:
    items: list = []
    cursor = ""
    for _ in range(50):          # a 50-page answer means something is wrong
        data = _slack_get(method, {**params, "cursor": cursor} if cursor else params)
        items.extend(data.get(key) or [])
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return items
    raise SlackRosterError("Slack kept paging — stopped to be safe. Try again later.")


def _clean_slack_name(raw: str) -> str:
    """Collapse whitespace and capitalize all-lowercase words ("grace
    fraser" -> "Grace Fraser"); mixed-case words (MacLennan, NeHa) stay."""
    words = (raw or "").split()
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w
                    for w in words)[:60].strip()


def fetch_slack_roster() -> list[dict]:
    """Members of SHIFT_ROSTER_SLACK_CHANNEL (default: the workspace's
    #general) as [{'slack_id', 'name', 'display'}]: real, active, full
    members only — no bots, deactivated accounts, guests, or the workspace's
    primary owner (the Operator). Names come from the Slack profile's real
    name; emails and everything else stay in Slack."""
    if not SLACK_BOT_TOKEN:
        raise SlackRosterError("Slack isn't connected — SLACK_BOT_TOKEN isn't set on Render.")
    channel = SHIFT_ROSTER_SLACK_CHANNEL
    if not channel:
        channels = _slack_paged("conversations.list", {
            "types": "public_channel", "exclude_archived": "true", "limit": 1000,
        }, "channels")
        channel = next((c.get("id") for c in channels if c.get("is_general")), "")
        if not channel:
            raise SlackRosterError("Couldn't find #general — set SHIFT_ROSTER_SLACK_CHANNEL on Render.")
    member_ids = set(_slack_paged("conversations.members",
                                  {"channel": channel, "limit": 1000}, "members"))
    people = []
    for user in _slack_paged("users.list", {"limit": 200}, "members"):
        if user.get("id") not in member_ids or user.get("id") == "USLACKBOT":
            continue
        if (user.get("deleted") or user.get("is_bot") or user.get("is_app_user")
                or user.get("is_restricted") or user.get("is_ultra_restricted")
                or user.get("is_primary_owner")):
            continue
        profile = user.get("profile") or {}
        name = _clean_slack_name(profile.get("real_name") or user.get("real_name")
                                 or profile.get("display_name") or "")
        if name:
            people.append({"slack_id": user["id"], "name": name,
                           "display": _clean_slack_name(profile.get("display_name") or "")})
    return people


def _slack_sync_summary(summary: dict, pulled: int) -> str:
    def names(key: str) -> str:
        found = summary[key]
        shown = ", ".join(found[:8])
        return shown + (f" +{len(found) - 8} more" if len(found) > 8 else "")

    parts = [f"Pulled {pulled} people from Slack."]
    if summary["added"]:
        parts.append(f"Added {len(summary['added'])}: {names('added')}.")
    else:
        parts.append("Nobody new to add.")
    on_roster = len(summary["already"]) + len(summary["linked"])
    if on_roster:
        parts.append(f"{on_roster} already on the roster.")
    renamed = [x for x in summary["linked"] if "→" in x]
    if renamed:
        parts.append("Matched to existing roster names: " + ", ".join(renamed[:8])
                     + (f" +{len(renamed) - 8} more" if len(renamed) > 8 else "") + ".")
    if summary["leaders"]:
        parts.append(f"{len(summary['leaders'])} have leader logins (they're under Leaders).")
    if summary["removed_kept"]:
        parts.append(f"Left off because you removed them from the roster: "
                     f"{names('removed_kept')} — restore them on Team roster if that was a mistake.")
    if summary["ambiguous"]:
        parts.append(f"Couldn't tell which of these is a leader login, so they're "
                     f"listed as team members: {names('ambiguous')}. Setting the "
                     "leader's Slack display name to their login name sorts it "
                     "out on the next pull.")
    if summary["duplicates"]:
        parts.append(f"Skipped — same name as someone already linked: {names('duplicates')}.")
    if summary["leader_named"]:
        parts.append(f"Skipped — a roster entry with that exact name is a leader's "
                     f"login: {names('leader_named')}.")
    if summary["leader_guesses"]:
        parts.append(f"Matched leader logins by first name: {names('leader_guesses')} "
                     "— if one is wrong, set that leader's Slack display name to "
                     "their login name and pull again.")
    if summary["unmatched_leaders"]:
        parts.append(f"No Slack account matched these leader logins: "
                     f"{names('unmatched_leaders')} — set their Slack display name "
                     "to their login name so they aren't listed twice.")
    return " ".join(parts)


@shift_bp.route("/roster/slack-sync", methods=["POST"])
@operator_required
def roster_slack_sync():
    # No #team fragment: on a phone it would scroll the result (or the
    # Slack setup instructions) off-screen above the fold.
    back = (url_for("shift.oneonone")
            if request.form.get("back") == "oneonone" else url_for("shift.roster"))
    try:
        people = fetch_slack_roster()
    except SlackRosterError as e:
        flash(str(e))
        return redirect(back)
    except Exception as e:  # network trouble — never a 500 for the Operator
        print(f"Slack roster pull failed: {e}")
        flash("Couldn't reach Slack just now — try again in a minute.")
        return redirect(back)
    summary = shift_db.sync_roster_from_slack(people)
    print(f"Slack roster pull: {len(people)} people, added {len(summary['added'])}")
    flash(_slack_sync_summary(summary, len(people)))
    return redirect(back)


def _maybe_autosync_roster() -> None:
    """Once a day, after the first manual pull, refresh the roster from
    Slack in the background when the Operator opens the 1:1 page — so new
    hires show up in the picker without anyone remembering to tap."""
    if not SLACK_BOT_TOKEN or not shift_db.claim_slack_roster_autosync():
        return

    def run() -> None:
        try:
            summary = shift_db.sync_roster_from_slack(fetch_slack_roster())
            print(f"Slack roster auto-sync: added {len(summary['added'])}")
        except Exception as e:
            print(f"Slack roster auto-sync failed: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    if current_app.config.get("TESTING"):
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 1:1 meeting agendas — private between each leader and the Operator
# ---------------------------------------------------------------------------

def _oneonone_url(kind: str, subject_id: int) -> str:
    if kind == "leader":
        return url_for("shift.oneonone_leader", leader_id=subject_id)
    return url_for("shift.oneonone_member", member_id=subject_id)


def _picker_choice() -> tuple[str, int] | None:
    """The 1:1 dropdown posts "leader:<id>" or "member:<id>". ASCII digits
    only, and short: str.isdigit() also passes "²", and ids past SQLite's
    int64 range crash the target route's lookup. The target route
    re-checks existence and access, so a stale or hand-edited value just
    lands on that route's own handling."""
    kind, _, subject_id = request.args.get("who", "").partition(":")
    if kind in ("leader", "member") and re.fullmatch(r"[0-9]{1,9}", subject_id):
        return kind, int(subject_id)
    return None


@shift_bp.route("/oneonone")
def oneonone():
    choice = _picker_choice()
    if _is_operator():
        if choice:
            return redirect(_oneonone_url(*choice))
        _maybe_autosync_roster()
        return render_template("shift/oneonone.html",
                               leaders=shift_db.oneonone_summary(),
                               team=shift_db.member_oneonone_index(),
                               people=shift_db.oneonone_people(),
                               leader_threads=shift_db.leader_member_threads(),
                               slack_synced_at=shift_db.slack_roster_synced_at(),
                               assigned=shift_db.tasks_assigned_by(current_name()),
                               **_MEETINGS_FMT)
    leader_id = session.get("shift_leader_id")
    if not leader_id:
        flash("Your login isn't linked to a leader profile — ask the Operator.")
        return redirect(url_for("shift.today"))
    # A leader's picker only ever opens their OWN threads: a team member
    # (their 1:1 with that person) or their own agenda with the Operator —
    # never another leader's agenda, whatever id the URL carries.
    if choice and choice[0] == "member":
        return redirect(url_for("shift.oneonone_member", member_id=choice[1]))
    if choice:
        return redirect(url_for("shift.oneonone_leader", leader_id=leader_id))
    agenda, history = shift_db.oneonone_for_leader(leader_id, meetings=1)
    modules = shift_db.course_overview(leader_id)
    return render_template(
        "shift/oneonone_mine.html",
        me=shift_db.get_leader(leader_id),
        agenda=agenda,
        my_open=len(agenda),
        my_last_met=history[0]["date"] if history else None,
        course_done=sum(m["done"] for m in modules),
        course_total=sum(m["total"] for m in modules),
        open_tasks=shift_db.tasks_for_leader(leader_id)[0],
        # Your own to-dos already show under Yours.
        assigned=[t for t in shift_db.tasks_assigned_by(current_name())
                  if t["leader_id"] != leader_id],
        team=shift_db.member_oneonone_index(holder_id=leader_id),
        people=[p for p in shift_db.oneonone_people() if p["kind"] == "member"],
        **_MEETINGS_FMT,
    )


@shift_bp.route("/oneonone/<int:leader_id>")
def oneonone_leader(leader_id):
    if not _can_view_oneonone(leader_id):
        flash("1:1 agendas are between each leader and the Operator.")
        return redirect(url_for("shift.today"))
    leader = shift_db.get_leader(leader_id)
    if not leader:
        flash("That leader doesn't exist.")
        return redirect(url_for("shift.oneonone"))
    agenda, history = shift_db.oneonone_for_leader(leader_id)
    modules = shift_db.course_overview(leader_id)
    return render_template(
        "shift/oneonone_leader.html",
        leader=leader, agenda=agenda, history=history,
        course_done=sum(m["done"] for m in modules),
        course_total=sum(m["total"] for m in modules),
        open_tasks=shift_db.tasks_for_leader(leader_id)[0],
        own=session.get("shift_leader_id") == leader_id,
        **_MEETINGS_FMT,
    )


@shift_bp.route("/oneonone/<int:leader_id>/topics", methods=["POST"])
def oneonone_topic_add(leader_id):
    if not _can_view_oneonone(leader_id):
        flash("1:1 agendas are between each leader and the Operator.")
        return redirect(url_for("shift.today"))
    if not shift_db.add_oneonone_topic(leader_id, request.form.get("topic", ""),
                                       current_name()):
        flash("A talking point needs some words.")
        return redirect(_back_to_meetings(
            url_for("shift.oneonone_leader", leader_id=leader_id), "#mine"))
    return redirect(_back_to_meetings(
        url_for("shift.oneonone_leader", leader_id=leader_id) + "#agenda", "#mine"))


@shift_bp.route("/oneonone/topic/<int:topic_id>/toggle", methods=["POST"])
def oneonone_topic_toggle(topic_id):
    # leader_id comes from the row, never the form.
    topic = shift_db.get_topic(topic_id)
    if not topic or not _can_view_oneonone(topic["leader_id"]):
        flash("That topic doesn't exist or isn't on your agenda.")
        return redirect(url_for("shift.today"))
    discussed = request.form.get("discussed") == "1"
    ok = shift_db.set_topic_discussed(
        topic_id, discussed, request.form.get("note", ""), current_name())
    if not ok:
        fresh = shift_db.get_topic(topic_id)
        if fresh:
            flash(f"Already marked discussed by {fresh['discussed_by'] or 'someone'} "
                  "— nothing changed." if fresh["discussed_at"]
                  else "Already back on the agenda — nothing changed.")
        else:
            flash("That topic doesn't exist any more.")
            return redirect(url_for("shift.oneonone_leader",
                                    leader_id=topic["leader_id"]))
    # A discussed topic has moved to the history section (no per-row anchor
    # there), so land on the agenda to keep the meeting flowing; a reopened
    # topic is back on the agenda where its anchor exists.
    anchor = "#agenda" if discussed else f"#topic-{topic_id}"
    return redirect(_back_to_meetings(
        url_for("shift.oneonone_leader", leader_id=topic["leader_id"]) + anchor,
        "#mine"))


@shift_bp.route("/oneonone/topic/<int:topic_id>/delete", methods=["POST"])
def oneonone_topic_delete(topic_id):
    topic = shift_db.get_topic(topic_id)
    if not topic or not _can_view_oneonone(topic["leader_id"]):
        flash("That topic doesn't exist or isn't on your agenda.")
        return redirect(url_for("shift.today"))
    if not (_is_operator() or topic["added_by"] == current_name()):
        flash("Only whoever added a topic (or the Operator) can remove it.")
    elif not shift_db.delete_topic(topic_id):
        flash("That topic was already discussed — it's part of meeting history now.")
    return redirect(_back_to_meetings(
        url_for("shift.oneonone_leader", leader_id=topic["leader_id"]), "#mine"))


@shift_bp.route("/oneonone/<int:leader_id>/action", methods=["POST"])
def oneonone_action(leader_id):
    if not _can_view_oneonone(leader_id):
        flash("1:1 agendas are between each leader and the Operator.")
        return redirect(url_for("shift.today"))
    ok = shift_db.add_task(
        leader_id,
        title=request.form.get("title", ""),
        details=request.form.get("details", ""),
        due_date=_optional_date(request.form.get("due_date")),
        assigned_by=current_name(),
    )
    if not ok:
        flash("A to-do needs a title (and a leader who still exists).")
    return redirect(_back_to_meetings(
        url_for("shift.oneonone_leader", leader_id=leader_id) + "#actions", "#todos"))


# Team-member 1:1s. Each thread is (member, holder): the Operator master
# login's own (holder None — Operator-only) or one leader's (that leader +
# the Operator; never other leaders, admin-role included). Team members
# have no login. Separate table and URL prefix, so a member topic id can
# never be acted on through the leader-agenda routes.

_MEMBER_PRIVATE = ("Team-member 1:1s are private to the leader who holds "
                   "them and the Operator.")


def _member_thread_holder(url_holder_id: int | None) -> tuple[bool, int | None]:
    """(allowed, holder) for a thread URL. The plain URL is the viewer's
    own thread (the Operator's, or this leader's); /by/<leader> is a
    leader's thread, open to that leader and the Operator only."""
    if _is_operator():
        return True, url_holder_id
    me = session.get("shift_leader_id")
    if me and url_holder_id in (None, me):
        return True, me
    return False, None


def _can_touch_member_thread(holder_id: int | None) -> bool:
    return _is_operator() or (holder_id is not None
                              and session.get("shift_leader_id") == holder_id)


def _member_thread_url(member_id: int, holder_id: int | None,
                       endpoint: str = "shift.oneonone_member") -> str:
    # The Operator reaches a leader's thread via /by/<leader>; everyone's
    # own thread is the plain URL.
    if holder_id is not None and _is_operator():
        return url_for(endpoint, member_id=member_id, holder_id=holder_id)
    return url_for(endpoint, member_id=member_id)


def _member_thread_context(member_id: int, url_holder_id: int | None):
    """Shared checks for a thread's page and its add-topic form. Returns
    (member, holder_id, holder_leader) or a redirect response."""
    allowed, holder = _member_thread_holder(url_holder_id)
    if not allowed:
        flash(_MEMBER_PRIVATE)
        return redirect(url_for("shift.oneonone"))
    holder_leader = None
    if holder is not None:
        holder_leader = shift_db.get_leader(holder)
        if not holder_leader:
            flash("That leader doesn't exist.")
            return redirect(url_for("shift.oneonone"))
    member = shift_db.get_member(member_id)
    if not member:
        flash("That team member isn't on the roster.")
        return redirect(url_for("shift.oneonone") + "#team")
    return member, holder, holder_leader


@shift_bp.route("/oneonone/member/<int:member_id>", defaults={"holder_id": None})
@shift_bp.route("/oneonone/member/<int:member_id>/by/<int:holder_id>")
def oneonone_member(member_id, holder_id):
    ctx = _member_thread_context(member_id, holder_id)
    if not isinstance(ctx, tuple):
        return ctx
    member, holder, holder_leader = ctx
    agenda, history = shift_db.oneonone_for_member(member_id, holder)
    leader = shift_db.active_leader_for_member(member)
    if leader and not agenda and not history:
        # One thread per person: someone with a leader login has a shared
        # agenda with the Operator already.
        if holder is None:
            return redirect(url_for("shift.oneonone_leader", leader_id=leader["id"]))
        flash(f"{leader['name']} has a leader login — 1:1s with leaders go "
              "through the Operator.")
        return redirect(url_for("shift.oneonone"))
    return render_template(
        "shift/oneonone_member.html", member=member, agenda=agenda,
        history=history, leader=leader, holder=holder_leader,
        topics_url=_member_thread_url(member_id, holder,
                                      "shift.oneonone_member_topic_add"),
        **_MEETINGS_FMT)


@shift_bp.route("/oneonone/member/<int:member_id>/topics", methods=["POST"],
                defaults={"holder_id": None})
@shift_bp.route("/oneonone/member/<int:member_id>/by/<int:holder_id>/topics",
                methods=["POST"])
def oneonone_member_topic_add(member_id, holder_id):
    ctx = _member_thread_context(member_id, holder_id)
    if not isinstance(ctx, tuple):
        return ctx
    member, holder, _ = ctx
    leader = shift_db.active_leader_for_member(member)
    if leader:
        # A leader's 1:1 is shared with them — a shadow file on the same
        # person would go around that.
        if holder is None:
            flash(f"{leader['name']} has a leader login — add it to your shared 1:1 instead.")
            return redirect(url_for("shift.oneonone_leader", leader_id=leader["id"]))
        flash(f"{leader['name']} has a leader login — 1:1s with leaders go "
              "through the Operator.")
        return redirect(url_for("shift.oneonone"))
    if not shift_db.add_member_topic(member_id, request.form.get("topic", ""),
                                     current_name(), holder_id=holder):
        flash("A talking point needs some words.")
        return redirect(_member_thread_url(member_id, holder))
    return redirect(_member_thread_url(member_id, holder) + "#agenda")


def _member_topic_for_viewer(topic_id: int) -> dict | None:
    # holder and member come from the row, never the form.
    topic = shift_db.get_member_topic(topic_id)
    if not topic or not _can_touch_member_thread(topic["holder_leader_id"]):
        flash("That topic doesn't exist or isn't on your agenda.")
        return None
    return topic


@shift_bp.route("/oneonone/member/topic/<int:topic_id>/toggle", methods=["POST"])
def oneonone_member_topic_toggle(topic_id):
    topic = _member_topic_for_viewer(topic_id)
    if not topic:
        return redirect(url_for("shift.oneonone"))
    back = _member_thread_url(topic["member_id"], topic["holder_leader_id"])
    discussed = request.form.get("discussed") == "1"
    ok = shift_db.set_member_topic_discussed(
        topic_id, discussed, request.form.get("note", ""), current_name())
    if not ok:
        fresh = shift_db.get_member_topic(topic_id)
        if fresh:
            flash(f"Already marked discussed by {fresh['discussed_by'] or 'someone'} "
                  "— nothing changed." if fresh["discussed_at"]
                  else "Already back on the agenda — nothing changed.")
        else:
            flash("That topic doesn't exist any more.")
            return redirect(back)
    anchor = "#agenda" if discussed else f"#topic-{topic_id}"
    return redirect(back + anchor)


@shift_bp.route("/oneonone/member/topic/<int:topic_id>/delete", methods=["POST"])
def oneonone_member_topic_delete(topic_id):
    topic = _member_topic_for_viewer(topic_id)
    if not topic:
        return redirect(url_for("shift.oneonone"))
    if not (_is_operator() or topic["added_by"] == current_name()):
        flash("Only whoever added a topic (or the Operator) can remove it.")
    elif not shift_db.delete_member_topic(topic_id):
        flash("That topic was already discussed — it's part of meeting history now.")
    return redirect(_member_thread_url(topic["member_id"], topic["holder_leader_id"]))


# ---------------------------------------------------------------------------
# Shout-outs (recognition)
# ---------------------------------------------------------------------------

def send_shoutout_slack(shoutout: dict) -> None:
    """Cross-post a shout-out to the team Slack channel, with the same bot
    token as the coverage flow. Best-effort: failures are logged and never
    surface to the person posting."""
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN is missing, cannot cross-post the shout-out.")
        return

    # Slack mrkdwn: &, < and > must be escaped or a message could inject a
    # real mention (same rule as the to-do completion ping).
    def esc(text):
        return (str(text).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))

    lines = [f"🌟 *SHOUT-OUT: {esc(shoutout['member_name'])}* 🌟"]
    if shoutout.get("value_tag"):
        lines.append(shift_db.SHOUTOUT_VALUE_LABELS.get(
            shoutout["value_tag"], shoutout["value_tag"]))
    lines.append(f"“{esc(shoutout['message'])}”")
    lines.append(f"— {esc(shoutout['author'])}")
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": SHIFT_SHOUTOUT_SLACK_CHANNEL,
                  "text": "\n".join(lines), "unfurl_links": False},
            timeout=15,
        )
        # Slack answers 200 even for errors — the body tells the story.
        print(f"Shout-out Slack status: {resp.status_code}, "
              f"body: {resp.text[:300]}")
        try:
            if resp.json().get("ok"):
                shift_db.mark_shoutout_delivered(shoutout["id"])
        except ValueError:
            pass
    except Exception as e:
        print(f"Error cross-posting shout-out to Slack: {e}")


def _notify_shoutout(shoutout_id: int) -> None:
    """Fire the Slack cross-post in the background — best-effort end to
    end, same shape as _notify_completion."""
    try:
        if not SHIFT_SHOUTOUT_SLACK_CHANNEL:
            return
        shoutout = shift_db.get_shoutout(shoutout_id)
        if not shoutout or not shoutout["shared_at"]:
            return
        thread = threading.Thread(target=send_shoutout_slack,
                                  args=(shoutout,), daemon=True)
        thread.start()
        if current_app.config.get("TESTING"):
            thread.join(timeout=5)
    except Exception as e:
        print(f"Error preparing shout-out cross-post: {e}")


@shift_bp.route("/shoutouts")
def shoutouts():
    tag = request.args.get("value")
    if tag not in shift_db.SHOUTOUT_VALUES:
        tag = None
    suggest = sorted({m["name"] for m in shift_db.roster()}, key=str.lower)
    return render_template(
        "shift/shoutouts.html",
        feed=shift_db.shoutout_feed(value_tag=tag),
        value=tag,
        suggest=suggest,
        share_on=bool(SLACK_BOT_TOKEN and SHIFT_SHOUTOUT_SLACK_CHANNEL),
        counts=shift_db.shoutout_counts() if is_admin() else None,
        SHOUTOUT_VALUES=shift_db.SHOUTOUT_VALUES,
        SHOUTOUT_VALUE_LABELS=shift_db.SHOUTOUT_VALUE_LABELS,
        SHOUTOUT_VALUE_SHORT=shift_db.SHOUTOUT_VALUE_SHORT,
    )


@shift_bp.route("/shoutouts", methods=["POST"])
def shoutout_create():
    value = request.form.get("value_tag")
    if value not in shift_db.SHOUTOUT_VALUES:
        value = None
    share = (request.form.get("share") == "1"
             and bool(SLACK_BOT_TOKEN and SHIFT_SHOUTOUT_SLACK_CHANNEL))
    member_name = request.form.get("member_name", "")
    sid = shift_db.add_shoutout(member_name, value,
                                request.form.get("message", ""),
                                current_name(), share=share)
    if sid is None:
        flash("A shout-out needs a name and a message.")
        return redirect(url_for("shift.shoutouts"))
    # Autosuggest learns genuinely new names only — never reactivates or
    # re-roles an existing (possibly departed) roster member.
    shift_db.add_member(member_name, touch_existing=False)
    if share:
        _notify_shoutout(sid)
    flash("Posted 🎉")
    return redirect(url_for("shift.shoutouts") + f"#shout-{sid}")


@shift_bp.route("/shoutouts/<int:shoutout_id>/delete", methods=["POST"])
def shoutout_delete(shoutout_id):
    shout = shift_db.get_shoutout(shoutout_id)
    if shout and (is_admin() or shout["author"] == current_name()):
        shift_db.delete_shoutout(shoutout_id)
    elif shout:
        flash("Only the author or an admin can delete a shout-out.")
    return redirect(url_for("shift.shoutouts"))


# ---------------------------------------------------------------------------
# Guest recovery
# ---------------------------------------------------------------------------

@shift_bp.route("/recovery")
def recovery():
    # Opportunistic PII hygiene: contact info ages out of long-resolved rows.
    shift_db.purge_old_recovery_contacts()
    open_recs, awaiting_recs, resolved_recs = shift_db.recovery_feed()
    return render_template(
        "shift/recovery.html",
        open_recs=open_recs,
        awaiting_recs=awaiting_recs,
        resolved_recs=resolved_recs,
        issue_counts=shift_db.recovery_issue_counts(),
        RECOVERY_ISSUES=shift_db.RECOVERY_ISSUES,
        RECOVERY_ISSUE_LABELS=shift_db.RECOVERY_ISSUE_LABELS,
        RECOVERY_REMEDIES=shift_db.RECOVERY_REMEDIES,
        RECOVERY_REMEDY_LABELS=shift_db.RECOVERY_REMEDY_LABELS,
    )


@shift_bp.route("/recovery", methods=["POST"])
def recovery_create():
    issue = request.form.get("issue", "")
    remedy = request.form.get("remedy", "")
    rid = shift_db.add_recovery(
        guest_name=request.form.get("guest_name", ""),
        guest_phone=request.form.get("guest_phone", ""),
        guest_email=request.form.get("guest_email", ""),
        issue=issue if issue in shift_db.RECOVERY_ISSUES else "other",
        remedy=remedy if remedy in shift_db.RECOVERY_REMEDIES else "other",
        details=request.form.get("details", ""),
        follow_up=request.form.get("follow_up") == "1",
        by=current_name(),
        resolved_now=request.form.get("resolved_now") == "1",
        contacted_now=request.form.get("contacted_now") == "1",
    )
    if rid is None:
        flash("A recovery needs the guest's name.")
        return redirect(url_for("shift.recovery"))
    flash("Logged. Make it right!")
    return redirect(url_for("shift.recovery") + f"#rec-{rid}")


@shift_bp.route("/recovery/<int:recovery_id>/contact", methods=["POST"])
def recovery_contact(recovery_id):
    contacted = request.form.get("contacted") == "1"
    ok = shift_db.set_recovery_contacted(
        recovery_id, contacted, request.form.get("note", ""), current_name())
    if not ok:
        rec = shift_db.get_recovery(recovery_id)
        if rec:
            if rec["resolved_at"]:
                flash("That one is already fully resolved — nothing changed.")
            elif rec["contacted_at"]:
                flash(f"Already marked contacted by "
                      f"{rec['contacted_by'] or 'someone'} — nothing changed.")
            else:
                flash("That one wasn't marked contacted — nothing changed.")
            return redirect(url_for("shift.recovery") + f"#rec-{recovery_id}")
        flash("That recovery doesn't exist any more.")
        return redirect(url_for("shift.recovery"))
    return redirect(url_for("shift.recovery") + f"#rec-{recovery_id}")


@shift_bp.route("/recovery/<int:recovery_id>/resolve", methods=["POST"])
def recovery_resolve(recovery_id):
    resolved = request.form.get("resolved") == "1"
    ok = shift_db.set_recovery_resolved(
        recovery_id, resolved, request.form.get("note", ""), current_name())
    if not ok:
        # Either the row is gone, or another leader beat them to the flip —
        # in which case the first resolver's stamp and note are kept.
        rec = shift_db.get_recovery(recovery_id)
        if rec:
            if rec["resolved_at"]:
                flash(f"Already resolved by {rec['resolved_by'] or 'someone'} "
                      "— nothing changed.")
            else:
                flash("Already reopened — nothing changed.")
            return redirect(url_for("shift.recovery") + f"#rec-{recovery_id}")
        flash("That recovery doesn't exist any more.")
        return redirect(url_for("shift.recovery"))
    return redirect(url_for("shift.recovery") + f"#rec-{recovery_id}")


@shift_bp.route("/recovery/<int:recovery_id>/delete", methods=["POST"])
@admin_required
def recovery_delete(recovery_id):
    shift_db.delete_recovery(recovery_id)
    return redirect(url_for("shift.recovery"))


# ---------------------------------------------------------------------------
# Equipment & repairs
# ---------------------------------------------------------------------------

@shift_bp.route("/equipment")
def equipment():
    to_call, waiting, fixed = shift_db.equipment_feed()
    return render_template(
        "shift/equipment.html",
        to_call=to_call,
        waiting=waiting,
        fixed=fixed,
        repeats=shift_db.equipment_repeats(),
        AREAS=shift_db.EQUIPMENT_AREAS,
        AREA_LABELS=shift_db.EQUIPMENT_AREA_LABELS,
        IMPACTS=shift_db.EQUIPMENT_IMPACTS,
        IMPACT_LABELS=shift_db.EQUIPMENT_IMPACT_LABELS,
        SUGGESTIONS=shift_db.EQUIPMENT_SUGGESTIONS,
        equipment_max=shift_db.EQUIPMENT_MAX,
    )


@shift_bp.route("/equipment", methods=["POST"])
def equipment_create():
    issue_id = shift_db.add_equipment_issue(
        equipment=request.form.get("equipment", ""),
        area=request.form.get("area", ""),
        impact=request.form.get("impact", ""),
        food_safety=request.form.get("food_safety") == "1",
        problem=request.form.get("problem", ""),
        by=current_name(),
        service_note=request.form.get("service_note", ""),
        fixed_now=request.form.get("fixed_now") == "1",
    )
    if issue_id is None:
        flash("Say what's broken first.")
        return redirect(url_for("shift.equipment"))
    flash("Reported. Thanks for flagging it.")
    return redirect(url_for("shift.equipment") + f"#eq-{issue_id}")


@shift_bp.route("/equipment/<int:issue_id>/called", methods=["POST"])
def equipment_called(issue_id):
    called = request.form.get("called") == "1"
    if not shift_db.set_equipment_called(issue_id, called, request.form.get("note", ""),
                                         current_name()):
        issue = shift_db.get_equipment_issue(issue_id)
        if not issue:
            flash("That problem isn't on the list any more.")
            return redirect(url_for("shift.equipment"))
        if issue["fixed_at"]:
            flash("That one's already fixed — nothing changed.")
        elif issue["called_at"]:
            flash(f"Already called in by {issue['called_by'] or 'someone'} "
                  "— nothing changed.")
        else:
            flash("That one wasn't marked called in — nothing changed.")
    return redirect(url_for("shift.equipment") + f"#eq-{issue_id}")


@shift_bp.route("/equipment/<int:issue_id>/fixed", methods=["POST"])
def equipment_fixed(issue_id):
    fixed = request.form.get("fixed") == "1"
    if not shift_db.set_equipment_fixed(issue_id, fixed, request.form.get("note", ""),
                                        current_name()):
        issue = shift_db.get_equipment_issue(issue_id)
        if not issue:
            flash("That problem isn't on the list any more.")
            return redirect(url_for("shift.equipment"))
        if issue["fixed_at"]:
            flash(f"Already marked fixed by {issue['fixed_by'] or 'someone'} "
                  "— nothing changed.")
        else:
            flash("Already reopened — nothing changed.")
    return redirect(url_for("shift.equipment") + f"#eq-{issue_id}")


@shift_bp.route("/equipment/<int:issue_id>/delete", methods=["POST"])
@admin_required
def equipment_delete(issue_id):
    shift_db.delete_equipment_issue(issue_id)
    return redirect(url_for("shift.equipment"))


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@shift_bp.route("/history")
def history():
    day = _valid_date(request.args.get("date"))
    runs = []
    for summary in shift_db.runs_for_date(day):
        if summary["frequency"] != "daily":
            continue
        run, items = shift_db.run_with_items(summary["id"])
        run.update(total=summary["total"], done=summary["done"])
        runs.append((run, items))
    # A weekly/monthly list spans many days: it shows under each day its
    # items were checked, with just those — as does an Operator correction
    # to a past list, under the day it was made.
    for run_id in shift_db.runs_worked_on(day):
        run, items = shift_db.run_with_items(run_id)
        run.update(total=len(items), done=sum(1 for i in items if i["done"]))
        runs.append((run, [i for i in items if i["done"]
                           and shift_db.business_date(i["done_at"]) == day]))
    return render_template(
        "shift/history.html", day=day, runs=runs,
        notes=shift_db.notes_for_date(day),
        recent=shift_db.recent_dates(7),
    )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@shift_bp.route("/admin")
@admin_required
def admin():
    return render_template("shift/admin.html",
                           leaders=shift_db.leaders(include_inactive=True),
                           admin_pin_set=bool(SHIFT_ADMIN_PIN))


@shift_bp.route("/admin/leaders/add", methods=["POST"])
@admin_required
def admin_leader_add():
    error = shift_db.add_leader(
        request.form.get("name", ""), request.form.get("pin", ""),
        role="admin" if request.form.get("role") == "admin" else "lead",
        may_replace=_is_operator(),
    )
    if error:
        flash(error)
    else:
        _flash_leader_relinks()
    return redirect(url_for("shift.admin"))


def _flash_leader_relinks() -> None:
    """A new or reactivated login may be someone already on the team
    roster (from Slack): link them so the 1:1 picker lists them once, and
    say so — it's a name-based match the Operator should be able to see."""
    try:
        made = shift_db.relink_leaders_from_roster()
    except Exception as e:  # never block the admin action itself
        print(f"Leader relink failed: {e}")
        return
    if made:
        flash("Matched to the team roster, so they're listed once in the 1:1 "
              f"picker: {', '.join(made)}. If that's not the same person, set "
              "the leader's Slack display name to their login name and pull "
              "from Slack.")


@shift_bp.route("/admin/leaders/<int:leader_id>/toggle", methods=["POST"])
@admin_required
def admin_leader_toggle(leader_id):
    active = request.form.get("active") == "1"
    shift_db.set_leader_active(leader_id, active)
    if active:
        _flash_leader_relinks()
    return redirect(url_for("shift.admin"))


@shift_bp.route("/admin/leaders/<int:leader_id>/reset-pin", methods=["POST"])
@operator_required
def admin_leader_reset_pin(leader_id):
    error = shift_db.reset_leader_pin(leader_id, request.form.get("pin", ""))
    flash(error if error else "PIN updated.")
    return redirect(url_for("shift.admin"))


@shift_bp.route("/admin/leaders/<int:leader_id>/role", methods=["POST"])
@admin_required
def admin_leader_role(leader_id):
    role = request.form.get("role", "")
    leader = shift_db.get_leader(leader_id)
    # Without the Operator master PIN, admin leaders are the only way into
    # this page — demoting the last one would lock the whole store out.
    if (role == "lead" and leader and leader["role"] == "admin"
            and leader["active"] and not SHIFT_ADMIN_PIN
            and shift_db.active_admin_count() <= 1):
        flash("That's the only admin login and SHIFT_ADMIN_PIN isn't set — "
              "demoting them would lock everyone out of this page.")
        return redirect(url_for("shift.admin"))
    if leader and shift_db.set_leader_role(leader_id, role):
        flash(f"{leader['name']} is now {'an admin' if role == 'admin' else 'a lead'}.")
    return redirect(url_for("shift.admin"))


@shift_bp.route("/admin/leaders/<int:leader_id>/checklists", methods=["POST"])
@admin_required
def admin_leader_checklists(leader_id):
    """Which side's checklists a leader sees: every list, front of house or
    back of house (plus the lists that are both)."""
    leader = shift_db.get_leader(leader_id)
    view = request.form.get("view", "")
    if leader and shift_db.set_leader_checklist_view(leader_id, view):
        flash(f"{leader['name']} sees "
              f"{shift_db.CHECKLIST_VIEW_LABELS[view].lower()} checklists"
              f"{'' if view == 'all' else ' (and the ones for both)'}.")
    return redirect(url_for("shift.admin") + f"#leader-{leader_id}")


def _parse_items(raw: str) -> list[tuple[str, bool]]:
    """One checklist item per line; a leading '!' marks it critical."""
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        critical = line.startswith("!")
        items.append((line.lstrip("! ").strip(), critical))
    return [(label, crit) for label, crit in items if label]


def _items_to_text(items: list[dict]) -> str:
    return "\n".join(("! " if i["critical"] else "") + i["label"] for i in items)


@shift_bp.route("/admin/templates")
@admin_required
def admin_templates():
    return render_template("shift/admin_templates.html",
                           templates=shift_db.all_templates(include_inactive=True))


def _template_schedule() -> tuple[str, int | None]:
    frequency = request.form.get("frequency") or "daily"
    due = request.form.get("due_weekday") or ""
    return frequency, (int(due) if re.fullmatch(r"[0-6]", due) else None)


@shift_bp.route("/admin/templates/new", methods=["GET", "POST"])
@admin_required
def admin_template_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        items = _parse_items(request.form.get("items", ""))
        if not name or not items:
            flash("A checklist needs a name and at least one item.")
            return redirect(url_for("shift.admin_template_new"))
        shift_db.create_template(name, _valid_checklist_daypart(request.form.get("daypart")),
                                 (request.form.get("area") or "All").strip() or "All",
                                 items, *_template_schedule(),
                                 side=request.form.get("side"))
        return redirect(url_for("shift.admin_templates"))
    return render_template("shift/admin_template_edit.html", template=None, items_text="")


@shift_bp.route("/admin/templates/<int:template_id>", methods=["GET", "POST"])
@admin_required
def admin_template_edit(template_id):
    template, items = shift_db.template_with_items(template_id)
    if not template:
        flash("That checklist doesn't exist.")
        return redirect(url_for("shift.admin_templates"))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        parsed = _parse_items(request.form.get("items", ""))
        if not name or not parsed:
            flash("A checklist needs a name and at least one item.")
            return redirect(url_for("shift.admin_template_edit", template_id=template_id))
        shift_db.update_template(template_id, name,
                                 _valid_checklist_daypart(request.form.get("daypart")),
                                 (request.form.get("area") or "All").strip() or "All",
                                 parsed, *_template_schedule(),
                                 side=request.form.get("side"))
        period = {"weekly": "week", "monthly": "month"}.get(
            shift_db.template_with_items(template_id)[0]["frequency"], "day")
        flash(f"Saved. A list already started keeps its items (history is never "
              f"rewritten); the change shows on the next one started — next "
              f"{period}'s, or this {period}'s if it hasn't been opened yet.")
        return redirect(url_for("shift.admin_templates"))
    return render_template("shift/admin_template_edit.html", template=template,
                           items_text=_items_to_text(items))


@shift_bp.route("/admin/templates/<int:template_id>/toggle", methods=["POST"])
@admin_required
def admin_template_toggle(template_id):
    shift_db.set_template_active(template_id, request.form.get("active") == "1")
    return redirect(url_for("shift.admin_templates"))


@shift_bp.route("/admin/export")
@operator_required
def admin_export():
    payload = shift_db.export_json()
    return Response(payload, mimetype="application/json", headers={
        "Content-Disposition":
            f"attachment; filename=shift-backup-{shift_db.today_local()}.json"
    })


@shift_bp.route("/admin/import", methods=["POST"])
@operator_required
def admin_import():
    if request.form.get("confirm") != "1":
        flash("Tick the confirmation box to restore — it replaces current data.")
        return redirect(url_for("shift.admin"))
    upload = request.files.get("backup")
    if not upload:
        flash("Choose a backup file first.")
        return redirect(url_for("shift.admin"))
    try:
        raw = upload.read().decode("utf-8")
    except UnicodeDecodeError:
        flash("That file is not a CFA Sidekick shift-app backup.")
        return redirect(url_for("shift.admin"))
    error = shift_db.import_json(raw)
    flash(error if error else "Backup restored.")
    return redirect(url_for("shift.admin"))


# ---------------------------------------------------------------------------
# PWA bits (public: fetched without cookies by some launchers)
# ---------------------------------------------------------------------------

@shift_bp.route("/manifest.webmanifest")
def manifest():
    return teamhub_nav.manifest(), 200, {"Content-Type": "application/manifest+json"}


@shift_bp.route("/icon.svg")
def icon():
    # Old "Shift Lead" home-screen installs keep resolving their icon.
    return redirect(url_for("static", filename="teamhub/icon-180.png"))
