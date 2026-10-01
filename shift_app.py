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
    return render_template(
        "shift/today.html",
        runs=runs,
        current_runs=[r for r in runs if r["daypart"] == daypart],
        announcements=announcements,
        lineup=shift_db.lineup_for(day, daypart),
        positions=shift_db.active_positions(),
        goals=shift_db.goals_by_status("active"),
        notes=shift_db.notes_for_date(day)[:3],
        my_tasks=shift_db.open_tasks_for_leader(leader_id) if leader_id else [],
        my_leader_id=leader_id,
        open_recoveries=shift_db.open_recoveries(),
        RECOVERY_ISSUE_LABELS=shift_db.RECOVERY_ISSUE_LABELS,
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
    return render_template("shift/goals.html", status=status,
                           goals=shift_db.goals_by_status(status),
                           periods=shift_db.GOAL_PERIODS)


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
    )
    return redirect(url_for("shift.goal_detail", goal_id=goal_id))


@shift_bp.route("/goals/<int:goal_id>")
def goal_detail(goal_id):
    goal, updates = shift_db.goal_with_updates(goal_id)
    if not goal:
        flash("That goal doesn't exist.")
        return redirect(url_for("shift.goals"))
    return render_template("shift/goal_detail.html", goal=goal, updates=updates)


@shift_bp.route("/goals/<int:goal_id>/update", methods=["POST"])
def goal_update(goal_id):
    goal, _ = shift_db.goal_with_updates(goal_id)
    if not goal:
        flash("That goal doesn't exist.")
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
    return render_template("shift/more.html",
                           open_recoveries=shift_db.open_recovery_count())


# ---------------------------------------------------------------------------
# Leadership development course
# ---------------------------------------------------------------------------

def _can_view_development(leader_id: int) -> bool:
    return is_admin() or session.get("shift_leader_id") == leader_id


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

@shift_bp.route("/todo")
def todo():
    if is_admin():
        return render_template("shift/todo.html",
                               leaders=shift_db.leader_task_summary())
    leader_id = session.get("shift_leader_id")
    if not leader_id:
        flash("Your login isn't linked to a leader profile — ask the Operator.")
        return redirect(url_for("shift.today"))
    return redirect(url_for("shift.todo_leader", leader_id=leader_id))


@shift_bp.route("/todo/<int:leader_id>")
def todo_leader(leader_id):
    if not _can_view_development(leader_id):
        flash("You can only see your own to-do list.")
        return redirect(url_for("shift.today"))
    leader = shift_db.get_leader(leader_id)
    if not leader:
        flash("That leader doesn't exist.")
        return redirect(url_for("shift.todo"))
    open_tasks, done_tasks = shift_db.tasks_for_leader(leader_id)
    return render_template("shift/todo_leader.html", leader=leader,
                           open_tasks=open_tasks, done_tasks=done_tasks)


@shift_bp.route("/todo/<int:leader_id>/assign", methods=["POST"])
@admin_required
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
# Guest recovery
# ---------------------------------------------------------------------------

@shift_bp.route("/recovery")
def recovery():
    # Opportunistic PII hygiene: contact info ages out of long-resolved rows.
    shift_db.purge_old_recovery_contacts()
    open_recs, resolved_recs = shift_db.recovery_feed()
    return render_template(
        "shift/recovery.html",
        open_recs=open_recs,
        resolved_recs=resolved_recs,
        issue_counts=shift_db.recovery_issue_counts() if is_admin() else [],
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
    )
    if rid is None:
        flash("A recovery needs the guest's name.")
        return redirect(url_for("shift.recovery"))
    flash("Logged. Make it right!")
    return redirect(url_for("shift.recovery") + f"#rec-{rid}")


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
@admin_required
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
@admin_required
def admin_export():
    payload = shift_db.export_json()
    return Response(payload, mimetype="application/json", headers={
        "Content-Disposition":
            f"attachment; filename=shift-backup-{shift_db.today_local()}.json"
    })


@shift_bp.route("/admin/import", methods=["POST"])
@admin_required
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
