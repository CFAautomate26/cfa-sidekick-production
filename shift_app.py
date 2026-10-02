"""Shift leading app for Chick-fil-A Wharncliffe & Wonderland.

A phone-first Huddle-style tool for the leadership team, mounted at /shift:
daily shift checklists (opening/transition/closing with who-did-what
accountability), position lineups per daypart, goal setting & tracking,
shift notes, and announcements with "Got it" acknowledgments.

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

import requests
from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, session, url_for)

import shift_course
import shift_db

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
    if not session.get("shift_name"):
        return redirect(url_for("shift.login", next=request.path))
    # Deactivated leaders lose access — and role changes (a demoted admin)
    # take effect — on their next request, not when the cookie expires.
    leader_id = session.get("shift_leader_id")
    if leader_id:
        leader = shift_db.get_leader(leader_id)
        if not leader or not leader["active"]:
            session.clear()
            return redirect(url_for("shift.login"))
        # A backup restore can renumber leader ids; sessions minted before
        # the restore must re-login rather than attach to a different row.
        if session.get("shift_epoch", "") != shift_db.session_epoch():
            session.clear()
            return redirect(url_for("shift.login"))
        session["shift_role"] = leader["role"]
    return None


# Blueprint-scoped (not app-wide): the bot's own pages must never touch the
# shift database, so a shift-DB failure can't break them.
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
        "NOTE_CATEGORIES": shift_db.NOTE_CATEGORIES,
        "data_at_risk": DATA_AT_RISK,
        "unread_announcements": (
            shift_db.unacked_count(current_name()) if signed_in else 0
        ),
    }


@shift_bp.app_template_filter("num")
def fmt_num(value):
    """Render 240.0 as 240 but keep real decimals (3.5 stays 3.5)."""
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return str(int(f)) if f.is_integer() else f"{f:g}"


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


def _valid_daypart(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in shift_db.DAYPARTS else shift_db.current_daypart()


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
    """Only redirect back to same-app paths, never off-site."""
    target = request.form.get("next", "") or request.args.get("next", "")
    if target.startswith("/shift") and "//" not in target:
        return redirect(target)
    return redirect(url_for(default_endpoint))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@shift_bp.route("/login", methods=["GET", "POST"])
def login():
    if not app_configured():
        return render_template("shift/not_configured.html"), 200

    names = [l["name"] for l in shift_db.leaders()]
    if SHIFT_ADMIN_PIN:
        names.append(OPERATOR_NAME)

    if request.method == "POST":
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
                session.permanent = True
                session["shift_name"] = OPERATOR_NAME
                session["shift_role"] = "admin"
                return _safe_next()
            error = "Wrong PIN."
            _record_fail(name)
        else:
            leader = shift_db.verify_leader(name, pin)
            if leader:
                _clear_fails(name)
                session.clear()
                session.permanent = True
                session["shift_name"] = leader["name"]
                session["shift_role"] = leader["role"]
                session["shift_leader_id"] = leader["id"]
                session["shift_epoch"] = shift_db.session_epoch()
                return _safe_next()
            error = "Wrong name or PIN."
            _record_fail(name)

        return render_template("shift/login.html", names=names, error=error,
                               picked=name), 401

    return render_template("shift/login.html", names=names, error=None, picked="")


@shift_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("shift.login"))


# ---------------------------------------------------------------------------
# Today dashboard
# ---------------------------------------------------------------------------

@shift_bp.route("/")
def today():
    day = shift_db.today_local()
    shift_db.ensure_runs_for_date(day)
    runs = shift_db.runs_for_date(day)
    daypart = shift_db.current_daypart()
    announcements = [a for a in shift_db.active_announcements(reader=current_name())
                     if not a["acked"]]
    leader_id = session.get("shift_leader_id")
    open_recs, awaiting_recs, _ = shift_db.recovery_feed(resolved_limit=0)
    return render_template(
        "shift/today.html",
        runs=runs,
        current_runs=[r for r in runs if r["daypart"] == daypart],
        announcements=announcements,
        lineup=shift_db.lineup_for(day, daypart),
        positions=shift_db.active_positions(),
        goals=shift_db.goals_by_status("active", store_only=True),
        notes=shift_db.notes_for_date(day)[:3],
        my_tasks=shift_db.open_tasks_for_leader(leader_id) if leader_id else [],
        my_leader_id=leader_id,
        open_recoveries=open_recs,
        awaiting_recoveries=awaiting_recs,
        RECOVERY_ISSUE_LABELS=shift_db.RECOVERY_ISSUE_LABELS,
        shoutouts=shift_db.recent_shoutouts(),
        SHOUTOUT_VALUE_LABELS=shift_db.SHOUTOUT_VALUE_LABELS,
    )


# ---------------------------------------------------------------------------
# Checklists
# ---------------------------------------------------------------------------

@shift_bp.route("/checklists")
def checklists():
    day = _valid_date(request.args.get("date"))
    if day == shift_db.today_local():
        shift_db.ensure_runs_for_date(day)
    return render_template("shift/checklists.html", day=day,
                           runs=shift_db.runs_for_date(day))


@shift_bp.route("/checklists/run/<int:run_id>")
def run_detail(run_id):
    run, items = shift_db.run_with_items(run_id)
    if not run:
        flash("That checklist doesn't exist.")
        return redirect(url_for("shift.checklists"))
    return render_template("shift/run.html", run=run, items=items)


@shift_bp.route("/item/<int:item_id>/toggle", methods=["POST"])
def toggle_item(item_id):
    done = request.form.get("done") == "1"
    run_id = shift_db.item_run_id(item_id)
    if run_id is None:
        if request.form.get("ajax") == "1":
            return {"ok": False}, 404
        flash("That item doesn't exist.")
        return redirect(url_for("shift.checklists"))

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
# Lineup / setup sheet
# ---------------------------------------------------------------------------

@shift_bp.route("/lineup")
def lineup():
    day = _valid_date(request.args.get("date"))
    daypart = _valid_daypart(request.args.get("daypart"))
    positions = shift_db.active_positions()
    areas: dict[str, list[dict]] = {}
    for p in positions:
        areas.setdefault(p["area"], []).append(p)
    prev_date = shift_db.previous_lineup_date(day, daypart)
    prev_daypart = None
    idx = shift_db.DAYPARTS.index(daypart)
    if idx > 0 and shift_db.lineup_for(day, shift_db.DAYPARTS[idx - 1]):
        prev_daypart = shift_db.DAYPARTS[idx - 1]
    suggest = sorted(
        {m["name"] for m in shift_db.roster()} | set(shift_db.recent_lineup_names()),
        key=str.lower,
    )
    return render_template(
        "shift/lineup.html", day=day, daypart=daypart, areas=areas,
        assignments=shift_db.lineup_for(day, daypart),
        prev_date=prev_date, prev_daypart=prev_daypart, suggest=suggest,
    )


@shift_bp.route("/lineup/assign", methods=["POST"])
def lineup_assign():
    day = _valid_date(request.form.get("date"))
    daypart = _valid_daypart(request.form.get("daypart"))
    name = " ".join((request.form.get("member_name") or "").split())
    try:
        position_id = int(request.form.get("position_id", ""))
    except ValueError:
        position_id = 0
    if name and position_id:
        if shift_db.assign_position(day, daypart, position_id, name):
            # Names typed here quietly join the roster so autosuggest learns.
            shift_db.add_member(name)
        else:
            flash("That position no longer exists — reload the lineup.")
    return redirect(url_for("shift.lineup", date=day, daypart=daypart))


@shift_bp.route("/lineup/unassign/<int:assignment_id>", methods=["POST"])
def lineup_unassign(assignment_id):
    day = _valid_date(request.form.get("date"))
    daypart = _valid_daypart(request.form.get("daypart"))
    shift_db.unassign(assignment_id)
    return redirect(url_for("shift.lineup", date=day, daypart=daypart))


@shift_bp.route("/lineup/copy", methods=["POST"])
def lineup_copy():
    to_date = _valid_date(request.form.get("to_date"))
    to_daypart = _valid_daypart(request.form.get("to_daypart"))
    from_date = _valid_date(request.form.get("from_date"))
    from_daypart = _valid_daypart(request.form.get("from_daypart"))
    copied = shift_db.copy_lineup(from_date, from_daypart, to_date, to_daypart)
    flash(f"Copied {copied} assignment{'s' if copied != 1 else ''}."
          if copied else "Nothing new to copy.")
    return redirect(url_for("shift.lineup", date=to_date, daypart=to_daypart))


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

@shift_bp.route("/goals")
def goals():
    status = request.args.get("status", "active")
    if status not in ("active", "achieved", "archived"):
        status = "active"
    # Leader-tagged goals are 1:1 material: each is listed only for its
    # leader and the Operator. Store-wide goals stay shared.
    visible = [g for g in shift_db.goals_by_status(status)
               if not g["leader_id"] or _can_view_oneonone(g["leader_id"])]
    taggable = [l for l in shift_db.leaders()
                if _is_operator() or l["id"] == session.get("shift_leader_id")]
    return render_template("shift/goals.html", status=status,
                           goals=visible,
                           periods=shift_db.GOAL_PERIODS,
                           leaders=taggable)


@shift_bp.route("/goals", methods=["POST"])
def goal_create():
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("A goal needs a title.")
        return redirect(url_for("shift.goals"))
    target_raw = (request.form.get("target_value") or "").strip()
    try:
        target = float(target_raw) if target_raw else None
    except ValueError:
        target = None
    period = (request.form.get("period") or "weekly").strip()
    if period not in shift_db.GOAL_PERIODS:
        period = "weekly"
    direction = "down" if request.form.get("direction") == "down" else "up"
    goal_id = shift_db.create_goal(
        title=title,
        why=(request.form.get("why") or "").strip(),
        metric=(request.form.get("metric") or "").strip(),
        unit=(request.form.get("unit") or "").strip(),
        target_value=target,
        direction=direction,
        period=period,
        due_date=_optional_date(request.form.get("due_date")),
        created_by=current_name(),
        leader_id=_taggable_leader_id(request.form.get("leader_id")),
    )
    return redirect(url_for("shift.goal_detail", goal_id=goal_id))


def _taggable_leader_id(raw: str | None) -> int | None:
    """A leader tag the current session may set: the Operator tags anyone,
    a leader only themselves; anything else becomes a store-wide goal."""
    lid = _optional_int(raw)
    return lid if lid is not None and _can_view_oneonone(lid) else None


def _goal_hidden(goal: dict) -> bool:
    return bool(goal.get("leader_id")
                and not _can_view_oneonone(goal["leader_id"]))


@shift_bp.route("/goals/<int:goal_id>")
def goal_detail(goal_id):
    goal, updates = shift_db.goal_with_updates(goal_id)
    if not goal or _goal_hidden(goal):
        flash("That goal doesn't exist."
              if not goal else
              "That's a personal goal — it's between that leader and the Operator.")
        return redirect(url_for("shift.goals"))
    return render_template("shift/goal_detail.html", goal=goal, updates=updates)


@shift_bp.route("/goals/<int:goal_id>/update", methods=["POST"])
def goal_update(goal_id):
    goal, _ = shift_db.goal_with_updates(goal_id)
    if not goal or _goal_hidden(goal):
        flash("That goal doesn't exist or isn't yours to update.")
        return redirect(url_for("shift.goals"))
    value_raw = (request.form.get("value") or "").strip()
    note = (request.form.get("note") or "").strip()
    try:
        value = float(value_raw) if value_raw else None
    except ValueError:
        flash("The progress number wasn't a number.")
        return redirect(url_for("shift.goal_detail", goal_id=goal_id))
    if value is None and not note:
        flash("Add a number or a note.")
        return redirect(url_for("shift.goal_detail", goal_id=goal_id))
    shift_db.add_goal_update(goal_id, value, note, current_name())
    return redirect(url_for("shift.goal_detail", goal_id=goal_id))


@shift_bp.route("/goals/<int:goal_id>/status", methods=["POST"])
def goal_status(goal_id):
    goal, _ = shift_db.goal_with_updates(goal_id)
    if not goal or _goal_hidden(goal):
        flash("That goal doesn't exist or isn't yours to change.")
        return redirect(url_for("shift.goals"))
    status = request.form.get("status", "")
    if status in ("active", "achieved", "archived"):
        shift_db.set_goal_status(goal_id, status)
    return redirect(url_for("shift.goal_detail", goal_id=goal_id))


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
    items = shift_db.active_announcements(reader=current_name())
    readers = {a["id"]: shift_db.announcement_readers(a["id"]) for a in items} \
        if is_admin() else {}
    return render_template("shift/announcements.html", items=items, readers=readers)


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
    open_recs, awaiting_recs, _ = shift_db.recovery_feed(resolved_limit=0)
    return render_template(
        "shift/more.html",
        open_recoveries=len(open_recs),
        awaiting_recoveries=len(awaiting_recs),
        my_open_topics=shift_db.open_topic_count(session.get("shift_leader_id")),
    )


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


@shift_bp.route("/development")
def development():
    if is_admin():
        return render_template(
            "shift/development.html",
            leaders=shift_db.leader_course_summary(),
            total=shift_db.course_lesson_count(),
            course_url=shift_course.COURSE_FOLDER_URL,
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
    done = sum(m["done"] for m in modules)
    total = sum(m["total"] for m in modules)
    return render_template(
        "shift/development_leader.html", leader=leader, modules=modules,
        done=done, total=total, course_url=shift_course.COURSE_FOLDER_URL,
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
    return redirect(url_for("shift.development_leader", leader_id=leader_id)
                    + f"#lesson-{lesson_id}")


# ---------------------------------------------------------------------------
# Leader to-dos
# ---------------------------------------------------------------------------

# Any leader can assign to-dos and browse the per-leader lists (the
# Operator opened this up from admin-only); completing a task stays with
# its assignee or an admin, and delete stays admin-only.
@shift_bp.route("/todo")
def todo():
    return render_template("shift/todo.html",
                           leaders=shift_db.leader_task_summary(),
                           my_leader_id=session.get("shift_leader_id"))


@shift_bp.route("/todo/<int:leader_id>")
def todo_leader(leader_id):
    leader = shift_db.get_leader(leader_id)
    if not leader:
        flash("That leader doesn't exist.")
        return redirect(url_for("shift.todo"))
    open_tasks, done_tasks = shift_db.tasks_for_leader(leader_id)
    return render_template("shift/todo_leader.html", leader=leader,
                           open_tasks=open_tasks, done_tasks=done_tasks)


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
    return redirect(url_for("shift.todo_leader", leader_id=leader_id))


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
    return redirect(url_for("shift.todo_leader", leader_id=task["leader_id"])
                    + f"#task-{task_id}")


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
    if summary["leaders"]:
        parts.append(f"{len(summary['leaders'])} have leader logins (they're under Leaders).")
    if summary["removed_kept"]:
        parts.append(f"Left off because you removed them from the roster: "
                     f"{names('removed_kept')} — restore them on Team roster if that was a mistake.")
    if summary["ambiguous"]:
        parts.append(f"Couldn't tell which of these is a leader login, so they were "
                     f"added as team members: {names('ambiguous')}.")
    if summary["duplicates"]:
        parts.append(f"Skipped — same name as someone already linked: {names('duplicates')}.")
    return " ".join(parts)


@shift_bp.route("/roster/slack-sync", methods=["POST"])
@operator_required
def roster_slack_sync():
    back = (url_for("shift.oneonone") + "#team"
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


@shift_bp.route("/oneonone")
def oneonone():
    if _is_operator():
        # The picker posts "leader:<id>" or "member:<id>"; the target route
        # re-checks existence and access, so a stale or hand-edited value
        # just lands on that route's own "doesn't exist" flash.
        kind, _, subject_id = request.args.get("who", "").partition(":")
        # ASCII digits only, and short: str.isdigit() also passes "²", and
        # ids past SQLite's int64 range crash the target route's lookup.
        if kind in ("leader", "member") and re.fullmatch(r"[0-9]{1,9}", subject_id):
            return redirect(_oneonone_url(kind, int(subject_id)))
        _maybe_autosync_roster()
        return render_template("shift/oneonone.html",
                               leaders=shift_db.oneonone_summary(),
                               team=shift_db.member_oneonone_index(),
                               people=shift_db.oneonone_people(),
                               slack_synced_at=shift_db.slack_roster_synced_at())
    # Leaders never see the picker or any lookup — straight to their own
    # agenda, so ?who= reveals nothing about who exists.
    leader_id = session.get("shift_leader_id")
    if not leader_id:
        flash("Your login isn't linked to a leader profile — ask the Operator.")
        return redirect(url_for("shift.today"))
    return redirect(url_for("shift.oneonone_leader", leader_id=leader_id))


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
        goals=shift_db.goals_by_status("active", leader_id=leader_id),
        course_done=sum(m["done"] for m in modules),
        course_total=sum(m["total"] for m in modules),
        open_tasks=shift_db.tasks_for_leader(leader_id)[0],
    )


@shift_bp.route("/oneonone/<int:leader_id>/topics", methods=["POST"])
def oneonone_topic_add(leader_id):
    if not _can_view_oneonone(leader_id):
        flash("1:1 agendas are between each leader and the Operator.")
        return redirect(url_for("shift.today"))
    if not shift_db.add_oneonone_topic(leader_id, request.form.get("topic", ""),
                                       current_name()):
        flash("A talking point needs some words.")
        return redirect(url_for("shift.oneonone_leader", leader_id=leader_id))
    return redirect(url_for("shift.oneonone_leader", leader_id=leader_id)
                    + "#agenda")


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
    return redirect(url_for("shift.oneonone_leader",
                            leader_id=topic["leader_id"]) + anchor)


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
    return redirect(url_for("shift.oneonone_leader",
                            leader_id=topic["leader_id"]))


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
    return redirect(url_for("shift.oneonone_leader", leader_id=leader_id)
                    + "#actions")


# Team-member 1:1s — the Operator master login's alone. Team members have
# no login, and leaders (admin-role included) never reach these routes:
# operator_required runs before any lookup. Separate table and URL prefix,
# so a member topic id can never be acted on through the leader routes.

@shift_bp.route("/oneonone/member/<int:member_id>")
@operator_required
def oneonone_member(member_id):
    member = shift_db.get_member(member_id)
    if not member:
        flash("That team member isn't on the roster.")
        return redirect(url_for("shift.oneonone") + "#team")
    agenda, history = shift_db.oneonone_for_member(member_id)
    leader = shift_db.active_leader_named(member["name"])
    if leader and not agenda and not history:
        # One thread per person: someone with a leader login has a shared
        # agenda already.
        return redirect(url_for("shift.oneonone_leader", leader_id=leader["id"]))
    return render_template("shift/oneonone_member.html", member=member,
                           agenda=agenda, history=history, leader=leader)


@shift_bp.route("/oneonone/member/<int:member_id>/topics", methods=["POST"])
@operator_required
def oneonone_member_topic_add(member_id):
    member = shift_db.get_member(member_id)
    if not member:
        flash("That team member isn't on the roster.")
        return redirect(url_for("shift.oneonone") + "#team")
    leader = shift_db.active_leader_named(member["name"])
    if leader:
        # A leader's 1:1 is shared with them — an Operator-only shadow file
        # on the same person would go around that.
        flash(f"{leader['name']} has a leader login — add it to your shared 1:1 instead.")
        return redirect(url_for("shift.oneonone_leader", leader_id=leader["id"]))
    if not shift_db.add_member_topic(member_id, request.form.get("topic", ""),
                                     current_name()):
        flash("A talking point needs some words.")
        return redirect(url_for("shift.oneonone_member", member_id=member_id))
    return redirect(url_for("shift.oneonone_member", member_id=member_id)
                    + "#agenda")


@shift_bp.route("/oneonone/member/topic/<int:topic_id>/toggle", methods=["POST"])
@operator_required
def oneonone_member_topic_toggle(topic_id):
    # member_id comes from the row, never the form.
    topic = shift_db.get_member_topic(topic_id)
    if not topic:
        flash("That topic doesn't exist any more.")
        return redirect(url_for("shift.oneonone") + "#team")
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
            return redirect(url_for("shift.oneonone_member",
                                    member_id=topic["member_id"]))
    anchor = "#agenda" if discussed else f"#topic-{topic_id}"
    return redirect(url_for("shift.oneonone_member",
                            member_id=topic["member_id"]) + anchor)


@shift_bp.route("/oneonone/member/topic/<int:topic_id>/delete", methods=["POST"])
@operator_required
def oneonone_member_topic_delete(topic_id):
    topic = shift_db.get_member_topic(topic_id)
    if not topic:
        flash("That topic doesn't exist any more.")
        return redirect(url_for("shift.oneonone") + "#team")
    if not shift_db.delete_member_topic(topic_id):
        flash("That topic was already discussed — it's part of meeting history now.")
    return redirect(url_for("shift.oneonone_member", member_id=topic["member_id"]))


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
    suggest = sorted({m["name"] for m in shift_db.roster()}
                     | set(shift_db.recent_lineup_names()), key=str.lower)
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
# History
# ---------------------------------------------------------------------------

@shift_bp.route("/history")
def history():
    day = _valid_date(request.args.get("date"))
    runs = []
    for summary in shift_db.runs_for_date(day):
        run, items = shift_db.run_with_items(summary["id"])
        run.update(total=summary["total"], done=summary["done"])
        runs.append((run, items))
    lineups = {dp: shift_db.lineup_for(day, dp) for dp in shift_db.DAYPARTS}
    lineups = {dp: v for dp, v in lineups.items() if v}
    return render_template(
        "shift/history.html", day=day, runs=runs, lineups=lineups,
        positions={p["id"]: p for p in shift_db.active_positions()},
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
    return redirect(url_for("shift.admin"))


@shift_bp.route("/admin/leaders/<int:leader_id>/toggle", methods=["POST"])
@admin_required
def admin_leader_toggle(leader_id):
    shift_db.set_leader_active(leader_id, request.form.get("active") == "1")
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


@shift_bp.route("/admin/templates/new", methods=["GET", "POST"])
@admin_required
def admin_template_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        items = _parse_items(request.form.get("items", ""))
        if not name or not items:
            flash("A checklist needs a name and at least one item.")
            return redirect(url_for("shift.admin_template_new"))
        shift_db.create_template(name, _valid_daypart(request.form.get("daypart")),
                                 (request.form.get("area") or "All").strip() or "All",
                                 items)
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
                                 _valid_daypart(request.form.get("daypart")),
                                 (request.form.get("area") or "All").strip() or "All",
                                 parsed)
        flash("Saved. Past days keep their history; the change starts tomorrow "
              "(or the next day that hasn't been opened yet).")
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
    return {
        "name": "CFA Shift Lead",
        "short_name": "Shift Lead",
        "start_url": "/shift/",
        "display": "standalone",
        "background_color": "#FFFAF2",
        "theme_color": "#E31937",
        "icons": [{"src": "/shift/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, 200, {"Content-Type": "application/manifest+json"}


_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
<rect width="180" height="180" rx="40" fill="#E31937"/>
<text x="90" y="88" font-family="Helvetica, Arial, sans-serif" font-size="56"
 font-weight="bold" fill="#fff" text-anchor="middle">CFA</text>
<text x="90" y="132" font-family="Helvetica, Arial, sans-serif" font-size="30"
 fill="#FFFAF2" text-anchor="middle">Shift</text>
</svg>"""


@shift_bp.route("/icon.svg")
def icon():
    return Response(_ICON_SVG, mimetype="image/svg+xml")
