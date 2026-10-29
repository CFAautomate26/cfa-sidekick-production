"""SQLite storage for the shift leading app (see shift_app.py).

Stdlib-only by design: sqlite3 + zoneinfo, no ORM. Every public function
opens a short-lived connection so callers never manage transactions or
cross-thread handles; WAL mode keeps concurrent leader phones from
blocking each other.

The database lives at SHIFT_DB_PATH (default: shift_data.db next to the
app). On Render the service disk is wiped on every deploy, so production
should attach a persistent disk and point SHIFT_DB_PATH at it, e.g.
/var/data/shift_data.db — see docs/shift-leading-app.md.
"""

import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from werkzeug.security import check_password_hash, generate_password_hash

import shift_cleaning
import shift_course

# .strip() guards against invisible whitespace pasted into the Render
# dashboard (same hardening as the Slack env vars).
DB_PATH = os.getenv("SHIFT_DB_PATH", "").strip() or os.path.join(
    os.path.dirname(__file__), "shift_data.db")

# Store-local clock. "Today" must roll over at midnight in London, Ontario,
# not UTC, or the 9pm close would write into tomorrow's checklists.
STORE_TZ = ZoneInfo(os.getenv("SHIFT_TZ", "").strip() or "America/Toronto")

DAYPARTS = ["breakfast", "lunch", "afternoon", "dinner"]
DAYPART_LABELS = {
    "breakfast": "Breakfast (6:30–10:30)",
    "lunch": "Lunch (10:30–2)",
    "afternoon": "Afternoon (2–5)",
    "dinner": "Dinner (5–close)",
    "allday": "All day",
}
# Checklists can also run all day (station cleaning lists); notes and the
# current-daypart guess stay on the four real dayparts.
CHECKLIST_DAYPARTS = DAYPARTS + ["allday"]
FREQUENCIES = ["daily", "weekly", "monthly"]
FREQUENCY_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday"]
# The store is closed Sundays: the scorecard never counts them as missed.
CLOSED_WEEKDAYS = (6,)

NOTE_CATEGORIES = ["general", "win", "issue", "equipment", "staffing", "guest", "food-safety"]

# Which side of the store a checklist belongs to, and which side each leader
# sees on Checklists (the Operator always sees all). A front-of-house leader
# sees front + both; back of house, back + both. Not a lock: "Show all"
# is one tap away — it just keeps a leader's list to their own area.
CHECKLIST_SIDES = ["foh", "boh", "both"]
CHECKLIST_SIDE_LABELS = {"foh": "Front of house", "boh": "Back of house",
                         "both": "Front and back"}
CHECKLIST_VIEWS = ["all", "foh", "boh"]
CHECKLIST_VIEW_LABELS = {"all": "Every checklist", "foh": "Front of house",
                         "boh": "Back of house"}
_FRONT_AREAS = {"front counter", "dining room", "drive thru", "drive-thru", "washrooms",
                "front of house", "foh", "hospitality"}
_BACK_AREAS = {"kitchen", "machines", "boards", "produce", "breading & raw", "breading",
               "prep", "back of house", "boh"}


def side_for(area: str | None, name: str | None = "") -> str:
    """A list's side from its area (the Operator can change it after). The
    posted Food Safety Daily Commitment sheet is front-counter and drive-thru
    stations, whatever its area says."""
    a = (area or "").strip().lower()
    if a in _FRONT_AREAS or (name or "").lower().startswith("food safety commitment"):
        return "foh"
    if a in _BACK_AREAS:
        return "boh"
    return "both"


def sides_for_view(view: str | None) -> tuple[str, ...] | None:
    """The list sides a leader's checklist view shows (None: every list)."""
    return {"foh": ("foh", "both"), "boh": ("boh", "both")}.get(view or "all")

RECOVERY_ISSUES = ["order-error", "food-quality", "wait-time", "service",
                   "cleanliness", "spill-accident", "catering", "other"]
RECOVERY_ISSUE_LABELS = {
    "order-error": "Order error (wrong / missing item)",
    "food-quality": "Food quality",
    "wait-time": "Long wait",
    "service": "Service issue",
    "cleanliness": "Cleanliness",
    "spill-accident": "Spill / accident",
    "catering": "Catering problem",
    "other": "Other",
}
RECOVERY_REMEDIES = ["remade-now", "refund", "free-entree-card",
                     "free-dessert-drink", "catering-credit", "apology-only",
                     "other"]
SHOUTOUT_VALUES = ["2nd-mile-service", "speed", "food-safety", "teamwork",
                   "hospitality", "cleanliness"]
SHOUTOUT_VALUE_LABELS = {
    "2nd-mile-service": "⭐ 2nd-mile service", "speed": "⚡ Speed of service",
    "food-safety": "🧤 Food safety", "teamwork": "🤝 Teamwork",
    "hospitality": "❤️ Hospitality", "cleanliness": "✨ Cleanliness",
}
# Short forms for the filter chip row, so most chips fit a phone width.
SHOUTOUT_VALUE_SHORT = {
    "2nd-mile-service": "⭐ 2nd-mile", "speed": "⚡ Speed",
    "food-safety": "🧤 Safety", "teamwork": "🤝 Teamwork",
    "hospitality": "❤️ Hospitality", "cleanliness": "✨ Clean",
}

RECOVERY_REMEDY_LABELS = {
    "remade-now": "Remade / replaced on the spot",
    "refund": "Refund (at the register — no card info here)",
    "free-entree-card": "Free entrée card (next visit)",
    "free-dessert-drink": "Free dessert / drink",
    "catering-credit": "Catering credit",
    "apology-only": "Apology accepted — nothing owed",
    "other": "Other (spell it out in details)",
}

# Equipment & repair tracker: where the problem is and how bad. "down" (out
# of service) is flagged red, and a food-safety flag (temps, sanitizer, hand
# sinks) puts a problem at the top of its list whatever its impact.
EQUIPMENT_AREAS = ["kitchen", "front-counter", "drive-thru", "dining-room",
                   "washrooms", "outside", "office", "other"]
EQUIPMENT_AREA_LABELS = {
    "kitchen": "Kitchen", "front-counter": "Front counter", "drive-thru": "Drive-thru",
    "dining-room": "Dining room", "washrooms": "Washrooms",
    "outside": "Outside (patio, lot, signs)", "office": "Office", "other": "Other",
}
EQUIPMENT_IMPACTS = ["down", "limited", "minor"]
EQUIPMENT_IMPACT_LABELS = {
    "down": "Out of service",
    "limited": "Working, needs repair",
    "minor": "Minor / cosmetic",
}
EQUIPMENT_MAX = 60                              # characters in "what's broken"
# Suggestions for what's broken (leaders can type anything, e.g. "Fryer 3").
EQUIPMENT_SUGGESTIONS = [
    "Pressure fryer", "Fry station", "Grill", "Toaster", "Holding cabinet",
    "Walk-in cooler", "Walk-in freezer", "Reach-in / lowboy", "Ice machine",
    "Icedream machine", "Beverage tower", "Tea brewer", "Lemonade dispenser",
    "Coffee brewer", "Breading table", "Dish machine", "Hand sink",
    "Registers", "iPOS tablets", "KPS screens", "Drive-thru headsets",
    "Drive-thru menu board", "Heating / AC", "Lights", "Plumbing / drains",
    "Doors / locks", "Playground",
]


def now_local() -> datetime:
    return datetime.now(STORE_TZ)


def today_local() -> str:
    """Store business date. The day rolls over at 4am, not midnight, so
    close work finishing after 12am still lands on that shift's date."""
    return (now_local() - timedelta(hours=4)).strftime("%Y-%m-%d")


def now_stamp() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M")


def current_daypart() -> str:
    """Best-guess daypart for defaulting pickers, by store-local time."""
    now = now_local()
    hour = now.hour + now.minute / 60
    if hour < 4:
        hour += 24  # past-midnight close work still belongs to dinner
    if hour < 10.5:
        return "breakfast"
    if hour < 14:
        return "lunch"
    if hour < 17:
        return "afternoon"
    return "dinner"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    role TEXT NOT NULL DEFAULT 'team',          -- team | leader
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    slack_id TEXT                               -- set by the Slack #general sync
);

-- NOTE: COLLATE NOCASE case-folds ASCII only, so "José" and "josé" count as
-- different names. Accepted limitation — worst case is a duplicate roster
-- entry.

-- People who can log in to the shift app. Separate from team_members: the
-- roster is who gets positioned on the floor; leaders are who run the app.
CREATE TABLE IF NOT EXISTS leaders (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    pin_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'lead',          -- lead | admin
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    slack_id TEXT                               -- remembered by the Slack roster pull
);

CREATE TABLE IF NOT EXISTS checklist_templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    daypart TEXT NOT NULL,                      -- one of CHECKLIST_DAYPARTS
    area TEXT NOT NULL DEFAULT 'All',
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    frequency TEXT NOT NULL DEFAULT 'daily',    -- daily | weekly | monthly
    due_weekday INTEGER                         -- weekly "assign a day": 0 = Monday
);

CREATE TABLE IF NOT EXISTS checklist_template_items (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    critical INTEGER NOT NULL DEFAULT 0,        -- food-safety/cash items, flagged red
    sort INTEGER NOT NULL DEFAULT 0
);

-- A list's schedule over time: one row when it's created and one per
-- retire/restore or frequency change, on the business date it happened.
-- The scorecard asks it whether a list owed a given day/week/month.
CREATE TABLE IF NOT EXISTS checklist_schedule_log (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE CASCADE,
    changed_on TEXT NOT NULL,                   -- '' = since before the log existed
    frequency TEXT NOT NULL,
    active INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_log_template
    ON checklist_schedule_log(template_id, changed_on);

-- One run of a template for one period. Items are snapshotted from the
-- template at creation so later template edits never rewrite past history.
-- run_date is the period's first business day: the day itself (daily), its
-- Monday (weekly) or the 1st (monthly); frequency is snapshotted too and is
-- part of the key, so a list switched from daily to weekly still gets its
-- weekly run on a Monday that already has a daily one.
CREATE TABLE IF NOT EXISTS checklist_runs (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE CASCADE,
    run_date TEXT NOT NULL,                     -- YYYY-MM-DD store-local
    frequency TEXT NOT NULL DEFAULT 'daily',
    owner TEXT,                                 -- the paper sheet's "Owner"
    verified_by TEXT,                           -- the paper sheet's "Manager check"
    verified_at TEXT,
    UNIQUE (template_id, frequency, run_date)
);

CREATE TABLE IF NOT EXISTS checklist_run_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES checklist_runs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    critical INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    done_by TEXT,
    done_at TEXT
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    why TEXT,                                   -- what winning looks like / why it matters
    metric TEXT,                                -- e.g. 'Avg DT time', blank for yes/no goals
    unit TEXT,                                  -- e.g. 'sec', '%', '$'
    target_value REAL,                          -- NULL for yes/no goals
    direction TEXT NOT NULL DEFAULT 'up',       -- up: higher is better, down: lower is better
    period TEXT NOT NULL DEFAULT 'weekly',      -- daily | weekly | monthly | one-time
    due_date TEXT,                              -- YYYY-MM-DD or NULL
    status TEXT NOT NULL DEFAULT 'active',      -- active | achieved | archived
    created_by TEXT,
    created_at TEXT NOT NULL,
    leader_id INTEGER REFERENCES leaders(id) ON DELETE SET NULL
                                                -- NULL = store-wide; set = rides on that leader's 1:1 page
);

CREATE TABLE IF NOT EXISTS goal_updates (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    value REAL,
    note TEXT,
    recorded_by TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,                         -- Front Counter | Drive-Thru | Kitchen | Dining Room | Leadership
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lineup_assignments (
    id INTEGER PRIMARY KEY,
    lineup_date TEXT NOT NULL,                  -- YYYY-MM-DD store-local
    daypart TEXT NOT NULL,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    member_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shift_notes (
    id INTEGER PRIMARY KEY,
    note_date TEXT NOT NULL,                    -- YYYY-MM-DD store-local
    daypart TEXT,
    category TEXT NOT NULL DEFAULT 'general',
    body TEXT NOT NULL,
    author TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    author TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    expires_on TEXT,                            -- YYYY-MM-DD or NULL = never
    created_at TEXT NOT NULL
);

-- Explicit "Got it" acknowledgments, per person.
CREATE TABLE IF NOT EXISTS announcement_reads (
    announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    reader TEXT NOT NULL COLLATE NOCASE,
    read_at TEXT NOT NULL,
    PRIMARY KEY (announcement_id, reader)
);

-- The Operator's leadership development course (seeded from shift_course.py)
-- and each leader's progress through it, reviewed in 1-on-1s.
CREATE TABLE IF NOT EXISTS course_lessons (
    id INTEGER PRIMARY KEY,
    module TEXT NOT NULL,
    title TEXT NOT NULL,
    doc_url TEXT,
    slides_url TEXT,
    video_url TEXT,
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lesson_progress (
    lesson_id INTEGER NOT NULL REFERENCES course_lessons(id) ON DELETE CASCADE,
    leader_id INTEGER NOT NULL REFERENCES leaders(id) ON DELETE CASCADE,
    completed_at TEXT NOT NULL,
    note TEXT,
    recorded_by TEXT,
    PRIMARY KEY (lesson_id, leader_id)
);

-- To-dos the Operator/admins assign to individual leaders.
CREATE TABLE IF NOT EXISTS leader_tasks (
    id INTEGER PRIMARY KEY,
    leader_id INTEGER NOT NULL REFERENCES leaders(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    details TEXT,
    due_date TEXT,                              -- YYYY-MM-DD or NULL
    assigned_by TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,                          -- NULL = still open
    completed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_leader_tasks ON leader_tasks(leader_id, completed_at);

-- Guest recovery log: a make-it-right promise to a guest, tracked until the
-- guest has been taken care of. Guest name + phone/email ONLY — never
-- payment/card info, and never HR/discipline/medical content (per CLAUDE.md
-- those stay out of this DB; injury claims go to the Operator directly).
CREATE TABLE IF NOT EXISTS guest_recoveries (
    id INTEGER PRIMARY KEY,
    recovery_date TEXT NOT NULL,                -- YYYY-MM-DD store-local (4am rollover)
    guest_name TEXT NOT NULL,
    guest_phone TEXT,                           -- as typed; rendered as a digits-only tel: link
    guest_email TEXT,
    issue TEXT NOT NULL DEFAULT 'other',        -- one of RECOVERY_ISSUES
    remedy TEXT NOT NULL DEFAULT 'other',       -- one of RECOVERY_REMEDIES
    details TEXT,                               -- what happened / order # / exactly what was promised
    follow_up INTEGER NOT NULL DEFAULT 0,       -- 1 = guest expects a call-back
    logged_by TEXT,
    created_at TEXT NOT NULL,
    contacted_at TEXT,                          -- guest reached; coming back for the remedy
    contacted_by TEXT,
    contact_note TEXT,                          -- what was agreed, e.g. "coming Sat for remake"
    resolved_at TEXT,                           -- NULL = promise still outstanding
    resolved_by TEXT,
    resolution_note TEXT                        -- e.g. "picked up replacement"
);
CREATE INDEX IF NOT EXISTS idx_recoveries_status
    ON guest_recoveries(resolved_at, recovery_date);

-- Equipment & repair tracker: something broken or not working right,
-- tracked from reported → service called → fixed. Equipment and vendor
-- details only — never anything about a person.
CREATE TABLE IF NOT EXISTS equipment_issues (
    id INTEGER PRIMARY KEY,
    report_date TEXT NOT NULL,                  -- YYYY-MM-DD store-local (4am rollover)
    equipment TEXT NOT NULL,                    -- what's broken, e.g. "Fryer 3"
    area TEXT NOT NULL DEFAULT 'other',         -- one of EQUIPMENT_AREAS
    impact TEXT NOT NULL DEFAULT 'limited',     -- one of EQUIPMENT_IMPACTS
    food_safety INTEGER NOT NULL DEFAULT 0,     -- 1 = temps, sanitizer, hand sink...
    problem TEXT,                               -- what's wrong + any workaround
    reported_by TEXT,
    created_at TEXT NOT NULL,
    called_at TEXT,                             -- service called / repair booked
    called_by TEXT,
    service_note TEXT,                          -- who's coming + when, ticket #
    fixed_at TEXT,                              -- NULL = still not right
    fixed_by TEXT,
    fix_note TEXT                               -- what was done
);
CREATE INDEX IF NOT EXISTS idx_equipment_status
    ON equipment_issues(fixed_at, report_date);

-- 1:1 meeting agendas. A topic with discussed_at NULL IS the agenda, so
-- unchecked topics carry forward automatically; checking one off stamps
-- who/when plus the business date, and grouping by discussed_on forms the
-- browsable past-meetings thread (no meetings table). Private between each
-- leader and the Operator. Growth/operational topics ONLY — per CLAUDE.md,
-- conduct/discipline/wage/health content never goes in this database
-- (those live under docs/legal-counsel).
CREATE TABLE IF NOT EXISTS oneonone_topics (
    id INTEGER PRIMARY KEY,
    leader_id INTEGER NOT NULL REFERENCES leaders(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    added_by TEXT,                              -- Operator or the leader — both feed the agenda
    created_at TEXT NOT NULL,
    discussed_at TEXT,                          -- NULL = still on the agenda
    discussed_by TEXT,
    discussed_on TEXT,                          -- YYYY-MM-DD store-local meeting date (4am rollover)
    outcome_note TEXT                           -- optional "what we decided"
);
CREATE INDEX IF NOT EXISTS idx_oneonone_topics
    ON oneonone_topics(leader_id, discussed_at);

-- 1:1 threads with roster team members (they have no login). Same shape
-- and semantics as oneonone_topics. Each thread belongs to whoever holds
-- it: the Operator master login (holder NULL — Operator-only) or one
-- leader (that leader + the Operator; never other leaders, admin-role
-- included). A separate table so the live oneonone_topics table is never
-- rebuilt and the leader-agenda queries and routes never touch these rows.
-- ON DELETE CASCADE is restore-critical: import_json runs DELETE FROM
-- team_members under foreign_keys=ON before re-inserting these rows
-- (NO ACTION would abort restores; SET NULL violates NOT NULL). The roster
-- is deactivate-only — a hard delete of team_members would erase 1:1
-- history. Growth/coaching topics ONLY: conduct, discipline, attendance,
-- wage, and health content never goes in this database.
CREATE TABLE IF NOT EXISTS oneonone_member_topics (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES team_members(id) ON DELETE CASCADE,
    -- Who holds this 1:1: a leader login, or NULL for the Operator master
    -- login. A thread is (member_id, holder); each is private to its holder
    -- + the Operator. SET NULL (not NO ACTION) so import_json's DELETE FROM
    -- leaders can't abort a restore.
    holder_leader_id INTEGER REFERENCES leaders(id) ON DELETE SET NULL,
    topic TEXT NOT NULL,
    added_by TEXT,
    created_at TEXT NOT NULL,
    discussed_at TEXT,                          -- NULL = still on the agenda
    discussed_by TEXT,
    discussed_on TEXT,                          -- YYYY-MM-DD business date (4am rollover)
    outcome_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_oneonone_member_topics
    ON oneonone_member_topics(member_id, discussed_at);

-- Daily goals (retired from the app in Oct 2026; rows kept, and backed
-- up): the two goals a leader set for the business day, a leadership goal
-- and an operational goal for their area of ownership. Private to that
-- leader + the Operator master login, like 1:1 agendas. Work goals only: never
-- HR/discipline/wage/medical content. CASCADE: a deleted leader's goals
-- go with them (import_json re-inserts them from the backup).
CREATE TABLE IF NOT EXISTS daily_goals (
    id INTEGER PRIMARY KEY,
    holder_leader_id INTEGER REFERENCES leaders(id) ON DELETE CASCADE,
                                                -- NULL = the Operator's own
    day TEXT NOT NULL,                          -- YYYY-MM-DD business date (4am rollover)
    kind TEXT NOT NULL,                         -- leadership | operational
    body TEXT NOT NULL,
    area TEXT,                                  -- operational: the area of ownership
    done_at TEXT,                               -- NULL = not ticked off
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_goals_slot
    ON daily_goals(COALESCE(holder_leader_id, 0), day, kind);

-- Recognition / shout-outs, posted by any leader, optionally cross-posted
-- to the team Slack channel (best-effort; shared_at records the REQUEST,
-- not a confirmed delivery).
CREATE TABLE IF NOT EXISTS shoutouts (
    id INTEGER PRIMARY KEY,
    shout_date TEXT NOT NULL,                   -- YYYY-MM-DD store-local (4am rollover)
    member_name TEXT NOT NULL,                  -- who's being recognized (roster autosuggest)
    value_tag TEXT,                             -- one of SHOUTOUT_VALUES, or NULL = untagged
    message TEXT NOT NULL,
    author TEXT,
    created_at TEXT NOT NULL,
    shared_at TEXT,                             -- NULL = kept in-app only (cross-post REQUESTED)
    delivered_at TEXT                           -- set by the background post on Slack ok:true
);
CREATE INDEX IF NOT EXISTS idx_shoutouts_date ON shoutouts(shout_date, id);

CREATE INDEX IF NOT EXISTS idx_run_items_run ON checklist_run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_lineup_date ON lineup_assignments(lineup_date, daypart);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lineup_slot
    ON lineup_assignments(lineup_date, daypart, position_id, member_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_notes_date ON shift_notes(note_date);
CREATE INDEX IF NOT EXISTS idx_goal_updates_goal ON goal_updates(goal_id);

-- ---------------------------------------------------------------------------
-- Team Hub (/hub): the store's "how we do it" library — see hub_db.py and
-- docs/team-hub.md. Processes and policies only, never personnel records.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hub_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '📘',
    blurb TEXT NOT NULL DEFAULT '',
    leaders_only INTEGER NOT NULL DEFAULT 0,
    policy INTEGER NOT NULL DEFAULT 0,          -- HR-style: only the Operator publishes
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

-- A page's live state; every published version is also kept, immutably,
-- in hub_page_versions. status: draft (editors only) | published | retired.
-- version 0 = never published. rev changes on every write, so a form
-- (edit, publish, file change) saved from a stale copy is refused. Ids are
-- never reused (AUTOINCREMENT): a deleted draft's history and links can't
-- attach to a later page.
CREATE TABLE IF NOT EXISTS hub_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES hub_categories(id) ON DELETE CASCADE,
    section TEXT NOT NULL DEFAULT '',           -- e.g. Training, Procedures
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',          -- "also called", for search
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    leaders_only INTEGER NOT NULL DEFAULT 0,
    required INTEGER NOT NULL DEFAULT 0,        -- everyone with access acknowledges it
    needs_counsel INTEGER NOT NULL DEFAULT 0,   -- blocks publishing until cleared
    annual_review INTEGER NOT NULL DEFAULT 0,   -- e.g. OHSA harassment policy
    review_by TEXT,                             -- YYYY-MM-DD
    version INTEGER NOT NULL DEFAULT 0,
    ack_version INTEGER NOT NULL DEFAULT 0,     -- read confirmations count from here
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    published_at TEXT,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hub_pages_category ON hub_pages(category_id, status);

CREATE TABLE IF NOT EXISTS hub_page_versions (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES hub_pages(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    change_note TEXT NOT NULL DEFAULT '',       -- shown to earlier readers on re-read
    content_hash TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    saved_by TEXT NOT NULL,
    reviewed_by TEXT,                           -- policy pages: who checked it, when
    reviewed_at TEXT,
    files TEXT NOT NULL DEFAULT '[]',           -- JSON: the page's files at this version
    UNIQUE (page_id, version)
);

-- A file taken off a published page is only marked removed (earlier
-- versions and read confirmations still point at it); the Operator can
-- purge it, which deletes the bytes but keeps this row as a tombstone.
CREATE TABLE IF NOT EXISTS hub_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES hub_pages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,                     -- the uploader's name, display only
    stored_name TEXT NOT NULL UNIQUE,           -- 32 hex chars + no extension, on disk
    kind TEXT NOT NULL,                         -- pdf|png|jpg|gif|webp|docx|xlsx|pptx
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    alt TEXT NOT NULL DEFAULT '',               -- images: what it shows
    text TEXT NOT NULL DEFAULT '',              -- extracted words, for search
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    photo_no INTEGER,                           -- images: [photo: N], never reused
    removed_at TEXT,
    removed_by TEXT,
    purged_at TEXT
);

-- Append-only proof a person received a version: never edited or deleted.
CREATE TABLE IF NOT EXISTS hub_acks (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES hub_pages(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    reader_key TEXT NOT NULL,                   -- member:<id> | leader:<id> | operator
    reader_name TEXT NOT NULL,
    wording TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    acked_at TEXT NOT NULL,
    UNIQUE (page_id, version, reader_key)
);

CREATE TABLE IF NOT EXISTS hub_reviews (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES hub_pages(id) ON DELETE CASCADE,
    reviewed_at TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS hub_audit (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    who TEXT NOT NULL,
    action TEXT NOT NULL,
    page_id INTEGER,
    detail TEXT NOT NULL DEFAULT ''
);

-- Who may sign in to the Hub with first name + clock-in number. Kept apart
-- from team_members (whose rows are read and exported whole) and never in
-- backups; a restore re-attaches each grant by name (see import_json).
-- code_hash is an HMAC under meta.hub_pepper, so the same number can't be
-- given to two people. Sessions carry `nonce`, which is new on every grant
-- and rotates when the person leaves the roster: removing access (or the
-- person) signs out every phone they used, for good.
CREATE TABLE IF NOT EXISTS hub_access (
    member_id INTEGER PRIMARY KEY REFERENCES team_members(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    nonce TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS hub_access_member_off
AFTER UPDATE OF active ON team_members WHEN NEW.active = 0
BEGIN
    UPDATE hub_access SET nonce = lower(hex(randomblob(16))) WHERE member_id = NEW.id;
END;
"""


# ---------------------------------------------------------------------------
# Seed content — Chick-fil-A Wharncliffe & Wonderland defaults. Inserted once
# (guarded by meta.seeded); everything is editable in the app afterwards.
# Items are (label, critical) — critical marks food-safety/cash tasks that the
# UI flags red until done.
# ---------------------------------------------------------------------------

# Nightly drain cleaning, its own red item on both closing lists so the
# scorecard shows it was done every night (live databases get it from
# _seed_closing_drains).
CLOSING_DRAINS = {
    "FOH Closing": ("Drains cleaned: every front-of-house floor & sink drain — dining room, "
                    "washrooms, front counter, drive-thru (clear debris, scrub, flush)"),
    "BOH Closing": ("Drains cleaned: every back-of-house floor & sink drain — centerline "
                    "(×4), breading-area receiving drain, dish & prep sinks (clear debris, "
                    "scrub, flush)"),
}

# Later one-line additions to lists the live database already has:
# (meta key that marks it applied, list name, label, critical, "already
# covered" pattern). Each lands once, before the list's last lock-up/alarm
# line, on the active list by that exact name — see _seed_list_additions.
# New databases get them in SEED_TEMPLATES.
MANAGER_OFFICE = ("Manager's office cleaned: desk + counters wiped, trash emptied, "
                  "floor swept + mopped")
_CLEANING = r"(clean|wip|tid|mop|sweep|swept|dust|vacuum)"
LIST_ADDITIONS = [
    ("closing_office_added", "FOH Closing", MANAGER_OFFICE, False,
     rf"\boffice\b.*\b{_CLEANING}|\b{_CLEANING}\w*\b.*\boffice\b"),
]

SEED_TEMPLATES = [
    ("FOH Opening", "breakfast", "Front Counter", [
        ("Registers + ServicePoint logged in and tested", False),
        ("Cash drawers counted and loaded", True),
        ("Iced tea brewed (sweet + unsweet) and lemonade stocked", False),
        ("Sauce + condiment station fully stocked", False),
        ("Dining room walkthrough: tables, chairs, floors, high chairs", False),
        ("Washrooms checked and stocked", False),
        ("Front doors unlocked at 6:30 sharp", False),
        ("Patio + parking lot walk: litter, lights, curb appeal", False),
        ("Drive-thru headsets charged, tested, batteries swapped", False),
        ("Music + menu boards on breakfast", False),
    ]),
    ("BOH Opening", "breakfast", "Kitchen", [
        ("Handwash + fresh gloves before any product handling", True),
        ("Sanitizer buckets mixed and verified (200 ppm)", True),
        ("Cooler + freezer temps checked and logged", True),
        ("Chicken thaw pulled per thaw chart", False),
        ("Breading station set up and sifted", False),
        ("Fryers on, filtered status verified, hash brown station set", False),
        ("Grills preheated and verified at temp", True),
        ("Biscuit + Grill breakfast timeline started", False),
        ("Prep list reviewed and started (produce, sauces)", False),
        ("Date labels checked on all open product (FIFO)", True),
    ]),
    ("Breakfast → Lunch Transition", "lunch", "All", [
        ("Menu boards + kiosks switched to lunch by 10:30", False),
        ("Breakfast product pulled and waste logged at 10:30", True),
        ("Fry station switched: hash browns → waffle fries", False),
        ("Breading station rotated for lunch volume", False),
        ("Boards stocked: buns, cheese, produce for rush", False),
        ("Sauces + lids + bags restocked front and DT", False),
        ("Dining room reset before 11:30 rush", False),
        ("Lunch lineup reviewed with team + breaks planned", False),
        ("Headset battery swap for rush", False),
    ]),
    ("Afternoon Reset", "afternoon", "All", [
        ("Dining room + washroom deep check after lunch", False),
        ("Restock front counter + DT for dinner (cups, lids, bags, sauces)", False),
        ("Fryer filtering per schedule", False),
        ("Prep levels checked against dinner projections", False),
        ("Breaks completed before 4:45", False),
        ("Trash pulled: lobby, DT, kitchen", False),
        ("Afternoon temp checks logged", True),
    ]),
    ("FOH Closing", "dinner", "Front Counter", [
        ("Dining room closed: tables, chairs up, floors swept + mopped", False),
        ("Washrooms cleaned and restocked", False),
        ("Condiment + sauce station broken down and wiped", False),
        ("Tea urns + lemonade dispensers emptied and sanitized", False),
        ("Registers counted, tills to safe, deposit prepped", True),
        ("Play area / high chairs sanitized", False),
        ("Patio furniture secured, exterior litter walk", False),
        (CLOSING_DRAINS["FOH Closing"], True),
        (MANAGER_OFFICE, False),
        ("Doors locked, lights + signage off, alarm checklist", True),
    ]),
    ("BOH Closing", "dinner", "Kitchen", [
        ("All fryers filtered and boiled out per schedule", False),
        ("Breading table broken down, flour sifted and stored", False),
        ("Grills scraped, cleaned and re-seasoned", False),
        ("All product dated, wrapped, rotated (FIFO) into walk-in", True),
        ("Prep surfaces + boards sanitized", True),
        ("Floors swept and mopped", False),
        (CLOSING_DRAINS["BOH Closing"], True),
        ("Closing temp checks logged", True),
        ("Waste log completed and verified", True),
        ("Sanitizer buckets emptied, towels to laundry", False),
    ]),
]

# Team Hub categories, seeded once (the Operator renames/adds them in the
# Hub). (name, icon, blurb, leaders_only, policy)
HUB_SEED_CATEGORIES = [
    ("HR", "📋", "Policies, the handbook, and how HR works here", 0, 1),
    ("FOH Operations", "🥤",
     "Front counter, drive-thru and dining room — procedures and training", 0, 0),
    ("BOH Operations", "🍳", "The kitchen — procedures, food safety and training", 0, 0),
    ("Leadership Development", "🌱", "LDP — for our leaders", 1, 0),
]
HUB_PAGE_STATUSES = ("draft", "published", "retired")
HUB_FILE_KINDS = ("pdf", "png", "jpg", "gif", "webp", "docx", "xlsx", "pptx")

SEED_POSITIONS = [
    # (name, area, sort)
    ("Shift Lead — FOH", "Leadership", 0),
    ("Shift Lead — BOH", "Leadership", 1),
    ("Register 1", "Front Counter", 10),
    ("Register 2", "Front Counter", 11),
    ("Bagging", "Front Counter", 12),
    ("Expo / Meal Delivery", "Front Counter", 13),
    ("Dining Room Host", "Dining Room", 20),
    ("DT Order Taker (iPOS)", "Drive-Thru", 30),
    ("DT Window", "Drive-Thru", 31),
    ("DT Bagger", "Drive-Thru", 32),
    ("DT Runner", "Drive-Thru", 33),
    ("DT Drinks", "Drive-Thru", 34),
    ("Breading", "Kitchen", 40),
    ("Primary (Fries)", "Kitchen", 41),
    ("Secondary", "Kitchen", 42),
    ("Boards 1", "Kitchen", 43),
    ("Boards 2", "Kitchen", 44),
    ("Prep", "Kitchen", 45),
]


def init_db() -> None:
    """Create tables and seed defaults on first run. Safe to call at every
    boot; each seed block has its own guard so upgrades of an existing
    database still seed newly added features."""
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        # Take the write lock before checking the seed guards: concurrent
        # gunicorn workers' first boots then serialize instead of both
        # passing a guard and one crashing on the meta PRIMARY KEY.
        conn.execute("BEGIN IMMEDIATE")
        try:
            _migrate(conn)
            _seed_base(conn)
            _seed_closing_drains(conn)
            _seed_list_additions(conn)
            _seed_course(conn)
            _seed_cleaning(conn)
            _seed_schedule_log(conn)
            _seed_hub(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        _rekey_checklist_runs(conn)


def _rekey_checklist_runs(conn) -> None:
    """Rebuild an old checklist_runs table keyed UNIQUE(template_id,
    run_date) as UNIQUE(template_id, frequency, run_date): under the old key
    a list switched from daily to weekly/monthly could never get its run on
    a Monday/1st that already had a daily one. SQLite can't alter a table
    constraint, so this is its documented copy-and-swap rebuild. Row ids are
    kept, so every run's items stay attached; foreign keys must be OFF (set
    outside the transaction, where the pragma takes effect) or dropping the
    old table would cascade-delete all of them."""
    def old_key() -> bool:
        for idx in conn.execute("PRAGMA index_list(checklist_runs)").fetchall():
            cols = [c["name"] for c in conn.execute(f"PRAGMA index_info({idx['name']})")]
            if idx["unique"] and cols == ["template_id", "run_date"]:
                return True
        return False

    if not old_key():
        return
    table_sql = re.search(r"CREATE TABLE IF NOT EXISTS checklist_runs \(.*?\n\);",
                          SCHEMA, re.S).group(0)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if old_key():  # another worker may have rebuilt it meanwhile
                orphans = len(conn.execute(
                    "PRAGMA foreign_key_check(checklist_run_items)").fetchall())
                conn.execute(table_sql.replace("IF NOT EXISTS checklist_runs",
                                               "checklist_runs_rekey"))
                conn.execute(
                    "INSERT INTO checklist_runs_rekey (id, template_id, run_date, "
                    "frequency, owner, verified_by, verified_at) SELECT id, "
                    "template_id, run_date, frequency, owner, verified_by, "
                    "verified_at FROM checklist_runs")
                conn.execute("DROP TABLE checklist_runs")
                conn.execute("ALTER TABLE checklist_runs_rekey RENAME TO checklist_runs")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_checklist_runs_date "
                             "ON checklist_runs(run_date)")
                if len(conn.execute("PRAGMA foreign_key_check(checklist_run_items)"
                                    ).fetchall()) > orphans:
                    raise sqlite3.IntegrityError("checklist_runs rebuild orphaned rows")
                print("shift_db: re-keyed checklist_runs by frequency")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate(conn) -> None:
    """Additive column migrations for existing databases — executescript's
    CREATE TABLE IF NOT EXISTS only shapes brand-new tables. Runs under
    init_db's write lock so concurrent workers can't double-ALTER.
    executescript(SCHEMA) runs FIRST, so never put an index in SCHEMA on a
    column this function ALTERs in — old databases would fail to boot."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(guest_recoveries)")}
    for col_def in ("contacted_at TEXT", "contacted_by TEXT", "contact_note TEXT"):
        if col_def.split()[0] not in cols:
            conn.execute(f"ALTER TABLE guest_recoveries ADD COLUMN {col_def}")
    # Goals ride-along: optional tag to one leader, shown on their 1:1 page.
    # ON DELETE SET NULL is restore-critical: import_json deletes leaders
    # under foreign_keys=ON while old goals rows still exist.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(goals)")}
    if "leader_id" not in cols:
        conn.execute("ALTER TABLE goals ADD COLUMN leader_id INTEGER "
                     "REFERENCES leaders(id) ON DELETE SET NULL")
    # Roster ride-along: the Slack user a roster row was pulled from or
    # linked to, so re-syncing never duplicates anyone.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(team_members)")}
    if "slack_id" not in cols:
        conn.execute("ALTER TABLE team_members ADD COLUMN slack_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_slack "
                 "ON team_members(slack_id) WHERE slack_id IS NOT NULL")
    # The pull remembers which Slack account is which leader login, so a
    # promoted team member's roster row drops out of the 1:1 picker.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leaders)")}
    if "slack_id" not in cols:
        conn.execute("ALTER TABLE leaders ADD COLUMN slack_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_leaders_slack "
                 "ON leaders(slack_id) WHERE slack_id IS NOT NULL")
    # Daily goals: each leader's area of ownership, remembered for their
    # operational goal (NULL until they or the Operator set one).
    if "area" not in cols:
        conn.execute("ALTER TABLE leaders ADD COLUMN area TEXT")
    # Which side's checklists each leader sees (the Operator sets it).
    if "checklist_side" not in cols:
        conn.execute("ALTER TABLE leaders ADD COLUMN "
                     "checklist_side TEXT NOT NULL DEFAULT 'all'")
    # Checklists gained weekly/monthly lists, the paper sheets' Owner and
    # Manager check, and run-level frequency snapshots. Existing templates
    # and runs are daily, exactly as before.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checklist_templates)")}
    if "frequency" not in cols:
        conn.execute("ALTER TABLE checklist_templates ADD COLUMN "
                     "frequency TEXT NOT NULL DEFAULT 'daily'")
    if "due_weekday" not in cols:
        conn.execute("ALTER TABLE checklist_templates ADD COLUMN due_weekday INTEGER")
    # Each list's side of the store, worked out from its area once (lists
    # added later get theirs when they're made).
    if "side" not in cols:
        conn.execute("ALTER TABLE checklist_templates ADD COLUMN "
                     "side TEXT NOT NULL DEFAULT 'both'")
        _backfill_sides(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checklist_runs)")}
    for col_def in ("frequency TEXT NOT NULL DEFAULT 'daily'", "owner TEXT",
                    "verified_by TEXT", "verified_at TEXT"):
        if col_def.split()[0] not in cols:
            conn.execute(f"ALTER TABLE checklist_runs ADD COLUMN {col_def}")
    if "verified_at" not in cols:
        # Older runs never had a Manager check to tap: the scorecard counts
        # manager checks only from here on.
        conn.execute("INSERT OR IGNORE INTO meta (key, value) "
                     "VALUES ('manager_check_since', ?)", (today_local(),))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checklist_runs_date "
                 "ON checklist_runs(run_date)")
    # Team-member 1:1s gained a holder: existing rows stay NULL = the
    # Operator's own (Operator-only), exactly as before.
    cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(oneonone_member_topics)")}
    if "holder_leader_id" not in cols:
        conn.execute("ALTER TABLE oneonone_member_topics ADD COLUMN "
                     "holder_leader_id INTEGER REFERENCES leaders(id) "
                     "ON DELETE SET NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oneonone_member_holder "
                 "ON oneonone_member_topics(holder_leader_id, member_id, discussed_at)")
    # Team Hub columns added after the first Hub builds (a database made by
    # an early build keeps the old shape until these run).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hub_pages)")}
    if "rev" not in cols:
        conn.execute("ALTER TABLE hub_pages ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hub_page_versions)")}
    if "files" not in cols:
        conn.execute("ALTER TABLE hub_page_versions ADD COLUMN files TEXT NOT NULL "
                     "DEFAULT '[]'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hub_attachments)")}
    for col_def in ("photo_no INTEGER", "removed_at TEXT", "removed_by TEXT",
                    "purged_at TEXT"):
        if col_def.split()[0] not in cols:
            conn.execute(f"ALTER TABLE hub_attachments ADD COLUMN {col_def}")
    if "photo_no" not in cols:
        # Number the photos already there in upload order, per page.
        conn.execute("UPDATE hub_attachments SET photo_no = (SELECT COUNT(*) FROM "
                     "hub_attachments b WHERE b.page_id = hub_attachments.page_id "
                     "AND b.kind IN ('png','jpg','gif','webp') AND b.id <= hub_attachments.id) "
                     "WHERE kind IN ('png','jpg','gif','webp')")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hub_access)")}
    if "nonce" not in cols:
        conn.execute("ALTER TABLE hub_access ADD COLUMN nonce TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE hub_access SET nonce = lower(hex(randomblob(16)))")


def _backfill_sides(conn, template_ids: list[int] | None = None) -> None:
    """Set lists' side from their area (all lists, or just these ids)."""
    rows = conn.execute("SELECT id, name, area FROM checklist_templates").fetchall()
    for r in rows:
        if template_ids is None or r["id"] in template_ids:
            conn.execute("UPDATE checklist_templates SET side=? WHERE id=?",
                         (side_for(r["area"], r["name"]), r["id"]))


def _seed_base(conn) -> None:
    seeded = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
    if seeded:
        return
    stamp = now_stamp()
    for sort, (name, daypart, area, items) in enumerate(SEED_TEMPLATES):
        cur = conn.execute(
            "INSERT INTO checklist_templates (name, daypart, area, sort, side) "
            "VALUES (?,?,?,?,?)",
            (name, daypart, area, sort, side_for(area, name)),
        )
        conn.executemany(
            "INSERT INTO checklist_template_items (template_id, label, critical, sort) "
            "VALUES (?,?,?,?)",
            [(cur.lastrowid, label, 1 if critical else 0, i)
             for i, (label, critical) in enumerate(items)],
        )
    conn.executemany(
        "INSERT INTO positions (name, area, sort) VALUES (?,?,?)", SEED_POSITIONS
    )
    conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', ?)", (stamp,))
    print(f"shift_db: seeded default checklists and positions at {stamp}")


def _active_lists_named(conn, name: str) -> list:
    """Active templates named `name`, ignoring case and outer spaces."""
    return conn.execute(
        "SELECT id FROM checklist_templates WHERE active=1 "
        "AND lower(trim(name)) = lower(?) ORDER BY id", (name,)).fetchall()


def _template_items(conn, template_id: int) -> list:
    return conn.execute(
        "SELECT id, label FROM checklist_template_items WHERE template_id=? "
        "ORDER BY sort, id", (template_id,)).fetchall()


def _before_last_lockup(items: list) -> int:
    """Position just before a list's last lock-up/alarm line (else its end)."""
    pos = len(items)
    for n, item in enumerate(items):
        if re.search(r"\b(lock|alarm)", item["label"], re.I):
            pos = n
    return pos


def _insert_template_item(conn, template_id: int, items: list, pos: int,
                          label: str, critical: bool) -> None:
    """Insert an item at `pos` in `items` (the list in order), renumbering."""
    for n, item in enumerate(items):
        conn.execute("UPDATE checklist_template_items SET sort=? WHERE id=?",
                     (n if n < pos else n + 1, item["id"]))
    conn.execute(
        "INSERT INTO checklist_template_items (template_id, label, critical, sort) "
        "VALUES (?,?,?,?)", (template_id, label, 1 if critical else 0, pos))


def _seed_list_additions(conn) -> None:
    """Apply each LIST_ADDITIONS entry once: only ACTIVE lists with that
    exact name (any case or spacing) are touched — renamed or rebuilt ones
    are left for the Operator — and one already covering it (its pattern
    matches a line) is left alone. Runs already started keep their items,
    and an item the Operator later removes stays removed."""
    for key, name, label, critical, covered in LIST_ADDITIONS:
        if conn.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
            continue
        done = []
        lists = _active_lists_named(conn, name)
        if not lists:
            done.append("no active list by that name, not changed")
        for t in lists:
            items = _template_items(conn, t["id"])
            if any(re.search(covered, i["label"], re.I) for i in items):
                done.append(f"#{t['id']} already covers it")
                continue
            _insert_template_item(conn, t["id"], items, _before_last_lockup(items),
                                  label, critical)
            done.append(f"#{t['id']} added")
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, now_stamp()))
        print(f"shift_db: {name} + {label.split(':')[0]!r} — {'; '.join(done)}")


def _seed_closing_drains(conn) -> None:
    """Once: give the live closing lists their own nightly drain item (red).
    Only ACTIVE lists named exactly "FOH Closing" / "BOH Closing" (any case
    or spacing) are touched — no guessing at renamed or rebuilt lists, which
    the Operator can update in Admin — and one already tracking drains in a
    line of its own is left alone. BOH's bundled "Floors swept, mopped,
    drains cleaned" becomes "Floors swept and mopped" with the drain item
    after it; FOH's goes before its last lock-up/alarm line. Runs already
    started keep their items; the deploy log says what was done."""
    if conn.execute(
        "SELECT 1 FROM meta WHERE key='closing_drains_added'"
    ).fetchone():
        return
    bundled = "Floors swept, mopped, drains cleaned"
    done = []
    for name, label in CLOSING_DRAINS.items():
        lists = _active_lists_named(conn, name)
        if not lists:
            done.append(f"{name}: no active list by that name, not changed")
        for t in lists:
            items = _template_items(conn, t["id"])
            if any(re.search(r"\bdrains?\b", i["label"], re.I) and i["label"] != bundled
                   for i in items):
                done.append(f"{name} #{t['id']}: already has a drains line")
                continue
            pos = len(items)
            if name == "BOH Closing":
                for n, item in enumerate(items):
                    if item["label"] == bundled:
                        conn.execute(
                            "UPDATE checklist_template_items SET label=? WHERE id=?",
                            ("Floors swept and mopped", item["id"]))
                        pos = n + 1
            else:
                pos = _before_last_lockup(items)
            _insert_template_item(conn, t["id"], items, pos, label, True)
            done.append(f"{name} #{t['id']}: added")
    conn.execute("INSERT INTO meta (key, value) VALUES ('closing_drains_added', ?)",
                 (now_stamp(),))
    print(f"shift_db: nightly drain items — {'; '.join(done)}")


def _seed_course(conn) -> None:
    seeded = conn.execute(
        "SELECT value FROM meta WHERE key='course_seeded'"
    ).fetchone()
    if seeded:
        return
    stamp = now_stamp()
    if conn.execute("SELECT 1 FROM course_lessons LIMIT 1").fetchone():
        # Lessons already exist (e.g. restored from a backup) with the flag
        # missing — record the flag rather than seeding duplicates.
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('course_seeded', ?)",
            (stamp,),
        )
        return
    sort = 0
    for module, lessons in shift_course.COURSE:
        for title, doc_id, slides_id, video_id in lessons:
            conn.execute(
                "INSERT INTO course_lessons (module, title, doc_url, slides_url, "
                "video_url, sort) VALUES (?,?,?,?,?,?)",
                (module, title, shift_course.drive_url(doc_id),
                 shift_course.drive_url(slides_id), shift_course.drive_url(video_id),
                 sort),
            )
            sort += 1
    conn.execute("INSERT INTO meta (key, value) VALUES ('course_seeded', ?)", (stamp,))
    print(f"shift_db: seeded leadership course ({sort} lessons) at {stamp}")


def _seed_cleaning(conn) -> None:
    """Seed each batch of posted station cleaning sheets (shift_cleaning.
    BATCHES) once, as ordinary templates after the existing ones. Each batch
    has its own meta key, so a batch shipped later still reaches an existing
    database. A name already in use is skipped, so a hand-made copy is never
    duplicated."""
    for key, lists in shift_cleaning.BATCHES:
        if conn.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
            continue
        stamp = now_stamp()
        sort = conn.execute(
            "SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM checklist_templates"
        ).fetchone()["s"]
        added = 0
        for name, frequency, daypart, area, due_weekday, items in lists:
            if conn.execute("SELECT 1 FROM checklist_templates WHERE name=?",
                            (name,)).fetchone():
                continue
            cur = conn.execute(
                "INSERT INTO checklist_templates (name, daypart, area, sort, frequency, "
                "due_weekday, side) VALUES (?,?,?,?,?,?,?)",
                (name, daypart, area, sort, frequency, due_weekday, side_for(area, name)),
            )
            conn.executemany(
                "INSERT INTO checklist_template_items (template_id, label, critical, "
                "sort) VALUES (?,?,?,?)",
                [(cur.lastrowid, label, 1 if critical else 0, i)
                 for i, (label, critical) in enumerate(items)],
            )
            sort += 1
            added += 1
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, stamp))
        print(f"shift_db: seeded {added} station cleaning lists ({key}) at {stamp}")


def _seed_hub(conn) -> None:
    """Seed the Team Hub's starting categories once."""
    if conn.execute("SELECT 1 FROM meta WHERE key='hub_seeded'").fetchone():
        return
    if not conn.execute("SELECT 1 FROM hub_categories LIMIT 1").fetchone():
        conn.executemany(
            "INSERT INTO hub_categories (name, icon, blurb, leaders_only, policy, sort) "
            "VALUES (?,?,?,?,?,?)",
            [(*c, n) for n, c in enumerate(HUB_SEED_CATEGORIES)])
    conn.execute("INSERT INTO meta (key, value) VALUES ('hub_seeded', ?)", (now_stamp(),))


def _seed_schedule_log(conn) -> None:
    """Give every list without schedule history a baseline row: its current
    schedule, as of always (existing lists, seeds, restored backups)."""
    conn.execute(
        "INSERT INTO checklist_schedule_log (template_id, changed_on, frequency, active) "
        "SELECT t.id, '', t.frequency, t.active FROM checklist_templates t "
        "WHERE NOT EXISTS (SELECT 1 FROM checklist_schedule_log l "
        "WHERE l.template_id = t.id)")


def _log_schedule(conn, template_id: int) -> None:
    """Record a list's schedule as of today if it differs from its last
    logged one (call after changing frequency or active)."""
    t = conn.execute("SELECT frequency, active FROM checklist_templates WHERE id=?",
                     (template_id,)).fetchone()
    last = conn.execute(
        "SELECT frequency, active FROM checklist_schedule_log WHERE template_id=? "
        "ORDER BY changed_on DESC, id DESC LIMIT 1", (template_id,)).fetchone()
    if t and (not last or (last["frequency"], last["active"])
              != (t["frequency"], t["active"])):
        conn.execute(
            "INSERT INTO checklist_schedule_log (template_id, changed_on, frequency, "
            "active) VALUES (?,?,?,?)",
            (template_id, today_local(), t["frequency"], t["active"]))


# ---------------------------------------------------------------------------
# Checklists
# ---------------------------------------------------------------------------

def period_start(frequency: str, day: str) -> str:
    """The first business day of the period `day` falls in: the day itself,
    its Monday, or the 1st of its month."""
    d = date.fromisoformat(day)
    if frequency == "weekly":
        d -= timedelta(days=d.weekday())
    elif frequency == "monthly":
        d = d.replace(day=1)
    return d.isoformat()


def period_end(frequency: str, start: str) -> str:
    """Last business day of the period beginning at `start`."""
    d = date.fromisoformat(start)
    if frequency == "weekly":
        return (d + timedelta(days=6)).isoformat()
    if frequency == "monthly":
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return (nxt - timedelta(days=1)).isoformat()
    return start


def period_label(frequency: str, start: str) -> str:
    d = date.fromisoformat(start)
    if frequency == "weekly":
        return f"Week of {d.strftime('%b')} {d.day}"
    if frequency == "monthly":
        return d.strftime("%B %Y")
    return start


def run_is_current(frequency: str, run_date: str, today: str | None = None) -> bool:
    """A run can be checked off only while its period is open — so the
    consistency scorecard measures what happened on the day, not backfill."""
    today = today or today_local()
    return period_start(frequency, today) == run_date


def _owed(log_rows: list | None, frequency: str, start: str,
          has_run: bool = False) -> bool:
    """Did a list owe the period starting `start` — was it active at this
    frequency at the close of the period's last open day? `log_rows` are
    its checklist_schedule_log rows in order (none: always as it is).
    The undated baseline only says how a list stood when the log began, so
    where it decides, a run for the period (only ever made for an active
    list on that schedule) is the proof it was owed."""
    if not log_rows:
        return True
    last_open = date.fromisoformat(period_end(frequency, start))
    while last_open.weekday() in CLOSED_WEEKDAYS and last_open.isoformat() > start:
        last_open -= timedelta(days=1)
    state = None
    for row in log_rows:
        if row["changed_on"] > last_open.isoformat():
            break
        state = row
    if state and state["changed_on"] == "" and has_run:
        return True
    return bool(state and state["active"] and state["frequency"] == frequency)


def _schedule_logs(conn) -> dict[int, list]:
    logs: dict[int, list] = {}
    for row in conn.execute(
            "SELECT template_id, changed_on, frequency, active "
            "FROM checklist_schedule_log ORDER BY template_id, changed_on, id"):
        logs.setdefault(row["template_id"], []).append(row)
    return logs


def _open_day_left(frequency: str, day: str) -> bool:
    """Is any day from `day` to the end of its period an open day?"""
    d = date.fromisoformat(day)
    end = date.fromisoformat(period_end(frequency, period_start(frequency, day)))
    while d <= end:
        if d.weekday() not in CLOSED_WEEKDAYS:
            return True
        d += timedelta(days=1)
    return False


def ensure_runs_for_date(run_date: str) -> None:
    """Instantiate a run (with snapshotted items) for every active template
    that doesn't have one for the period containing run_date yet. A weekly
    or monthly run isn't started once no open day is left in its period
    (opening the app on a closed Sunday would otherwise charge the
    scorecard a whole week nobody could work)."""
    with closing(connect()) as conn, conn:
        templates = conn.execute(
            "SELECT id, frequency FROM checklist_templates WHERE active=1"
        ).fetchall()
        for t in templates:
            frequency = t["frequency"] if t["frequency"] in FREQUENCIES else "daily"
            if frequency != "daily" and not _open_day_left(frequency, run_date):
                continue
            start = period_start(frequency, run_date)
            # INSERT OR IGNORE + UNIQUE(template_id, frequency, run_date)
            # makes concurrent first-loads race-safe.
            cur = conn.execute(
                "INSERT OR IGNORE INTO checklist_runs (template_id, run_date, frequency) "
                "VALUES (?,?,?)",
                (t["id"], start, frequency),
            )
            if cur.rowcount:
                items = conn.execute(
                    "SELECT label, critical, sort FROM checklist_template_items "
                    "WHERE template_id=? ORDER BY sort, id",
                    (t["id"],),
                ).fetchall()
                conn.executemany(
                    "INSERT INTO checklist_run_items (run_id, label, critical, sort) "
                    "VALUES (?,?,?,?)",
                    [(cur.lastrowid, i["label"], i["critical"], i["sort"]) for i in items],
                )


def _decorate_run(run: dict, today: str) -> dict:
    run["period_end"] = period_end(run["frequency"], run["run_date"])
    run["period_label"] = period_label(run["frequency"], run["run_date"])
    run["is_current"] = run_is_current(run["frequency"], run["run_date"], today)
    return run


def runs_for_date(run_date: str) -> list[dict]:
    """The runs covering a date — that day's daily runs plus the weekly and
    monthly runs whose period contains it — with progress counts, ordered
    daily (by daypart) then weekly then monthly. "scheduled" is False for a
    run whose list was since retired or moved to another frequency. A run
    with nothing checked shows only while its list owes the period (the
    scorecard's rule) — so a list taken off mid-period doesn't linger (or
    nag on Today), now or in History later; a missed one always shows."""
    daypart_order = {d: i for i, d in enumerate(["allday"] + DAYPARTS)}
    freq_order = {f: i for i, f in enumerate(FREQUENCIES)}
    today = today_local()
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.template_id, r.run_date, r.frequency, r.owner, r.verified_by,
                   r.verified_at, t.name, t.daypart, t.area, t.sort, t.due_weekday,
                   t.side, t.active, t.frequency AS template_frequency, COUNT(i.id) AS total,
                   COALESCE(SUM(i.done), 0) AS done,
                   COALESCE(SUM(CASE WHEN i.critical=1 AND i.done=0 THEN 1 ELSE 0 END), 0)
                       AS critical_remaining
            FROM checklist_runs r
            JOIN checklist_templates t ON t.id = r.template_id
            LEFT JOIN checklist_run_items i ON i.run_id = r.id
            WHERE (r.frequency = 'daily' AND r.run_date = ?)
               OR (r.frequency = 'weekly' AND r.run_date = ?)
               OR (r.frequency = 'monthly' AND r.run_date = ?)
            GROUP BY r.id
            """,
            (run_date, period_start("weekly", run_date),
             period_start("monthly", run_date)),
        ).fetchall()
        logs = _schedule_logs(conn)
    runs = []
    for row in rows:
        run = _decorate_run(dict(row), today)
        run["scheduled"] = bool(run["active"]) and (
            run["frequency"] == run.pop("template_frequency"))
        if run["done"] or (run["scheduled"] if run["is_current"] else _owed(
                logs.get(run["template_id"]), run["frequency"], run["run_date"],
                has_run=True)):
            runs.append(run)
    runs.sort(key=lambda r: (freq_order.get(r["frequency"], 9),
                             daypart_order.get(r["daypart"], 99), r["sort"], r["id"]))
    return runs


def runs_worked_on(day: str) -> list[int]:
    """Runs other than `day`'s own daily ones with an item checked during
    business day `day` (4am to 4am) — weekly/monthly work and Operator
    corrections to past lists — oldest period first, for History."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    with closing(connect()) as conn:
        return [r["id"] for r in conn.execute(
            """
            SELECT DISTINCT r.id, r.run_date FROM checklist_runs r
            JOIN checklist_run_items i ON i.run_id = r.id
            WHERE NOT (r.frequency = 'daily' AND r.run_date = ?) AND i.done = 1
              AND i.done_at >= ? AND i.done_at < ?
            ORDER BY r.run_date, r.id
            """, (day, f"{day} 04:00", f"{nxt} 04:00")).fetchall()]


def run_with_items(run_id: int) -> tuple[dict | None, list[dict]]:
    with closing(connect()) as conn:
        run = conn.execute(
            """
            SELECT r.id, r.run_date, r.frequency, r.owner, r.verified_by,
                   r.verified_at, t.name, t.daypart, t.area, t.due_weekday
            FROM checklist_runs r JOIN checklist_templates t ON t.id = r.template_id
            WHERE r.id = ?
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None, []
        items = conn.execute(
            "SELECT * FROM checklist_run_items WHERE run_id=? ORDER BY sort, id",
            (run_id,),
        ).fetchall()
    return _decorate_run(dict(run), today_local()), [dict(i) for i in items]


def set_item_done(item_id: int, done: bool, by: str) -> bool:
    """Check or uncheck one run item. A no-op when it's already in that
    state, so a stale screen can't restamp someone else's earlier check.
    Returns False if the item is unknown."""
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE checklist_run_items SET done=?, done_by=?, done_at=? "
            "WHERE id=? AND done != ?",
            (1 if done else 0, by if done else None, now_stamp() if done else None,
             item_id, 1 if done else 0),
        )
        return conn.execute("SELECT 1 FROM checklist_run_items WHERE id=?",
                            (item_id,)).fetchone() is not None


def set_run_owner(run_id: int, owner: str) -> bool:
    """The paper sheet's "Owner": who's responsible for the station this
    period (a roster name — team members have no login)."""
    owner = " ".join((owner or "").split())[:60] or None
    with closing(connect()) as conn, conn:
        return conn.execute("UPDATE checklist_runs SET owner=? WHERE id=?",
                            (owner, run_id)).rowcount > 0


def set_run_verified(run_id: int, verified: bool, by: str) -> bool:
    """The paper sheet's "Manager check": a leader walked the station and
    confirms the list. Guarded flip — the first stamp wins."""
    guard = "IS NULL" if verified else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        return conn.execute(
            f"UPDATE checklist_runs SET verified_by=?, verified_at=? "
            f"WHERE id=? AND verified_at {guard}",
            (by if verified else None, now_stamp() if verified else None, run_id),
        ).rowcount > 0


def checklist_scorecard(today: str | None = None) -> list[dict]:
    """How consistently each active checklist gets done, for the scorecard.

    Periods are measured once they're over (today, this week and this month
    show separately as "in progress"): the last 7 and 28 open days for daily
    lists (closed Sundays never count), the last 4 weeks for weekly, the
    last 3 months for monthly. Only runs at the list's current frequency
    count, from its first one, so a new or re-scheduled list isn't
    penalized for periods before it existed.

    A list owes a period only if, at the close of the period's last open
    day, it was active at that frequency (checklist_schedule_log) — so a
    list retired or moved off a schedule mid-period owes nothing for it,
    and changes made on a closed Sunday apply from the next period.
    Opening the app creates the current run of EVERY active list at once,
    so an owed period with no run but with app use in it (another list's
    run on an open day) means the list was only scheduled after the app was
    last opened — skipped. An owed period with no use at all means nobody
    opened the app: that counts as nothing done. Completion = items checked
    / items expected; a period is "complete" when every item was checked.
    Manager checks count only from when the button existed."""
    today = today or today_local()
    windows = {"daily": 28, "weekly": 4, "monthly": 3}

    def periods(frequency: str, count: int) -> list[str]:
        """Starts of the last `count` FINISHED periods, newest first."""
        starts, cur = [], period_start(frequency, today)
        while len(starts) < count:
            prev = date.fromisoformat(cur) - timedelta(days=1)
            cur = period_start(frequency, prev.isoformat())
            if frequency == "daily" and prev.weekday() in CLOSED_WEEKDAYS:
                continue
            starts.append(cur)
        return starts

    window_starts = {f: periods(f, n) for f, n in windows.items()}
    oldest = min(starts[-1] for starts in window_starts.values())
    with closing(connect()) as conn:
        templates = [dict(t) for t in conn.execute(
            """
            SELECT t.id, t.name, t.area, t.daypart, t.frequency, t.due_weekday, t.sort,
                   (SELECT COUNT(*) FROM checklist_template_items i
                    WHERE i.template_id = t.id) AS item_count
            FROM checklist_templates t WHERE t.active = 1
            ORDER BY t.sort, t.id
            """).fetchall()]
        first_runs = {(r["template_id"], r["frequency"]): r["first"] for r in conn.execute(
            "SELECT template_id, frequency, MIN(run_date) AS first FROM checklist_runs "
            "GROUP BY template_id, frequency")}
        runs = [dict(r) for r in conn.execute(
            """
            SELECT r.id, r.template_id, r.run_date, r.frequency, r.owner,
                   r.verified_at, COUNT(i.id) AS total, COALESCE(SUM(i.done), 0) AS done
            FROM checklist_runs r LEFT JOIN checklist_run_items i ON i.run_id = r.id
            WHERE r.run_date >= ?
            GROUP BY r.id
            """, (oldest,)
        ).fetchall()]
        misses = [dict(m) for m in conn.execute(
            """
            SELECT r.template_id, r.frequency, r.run_date, i.label, i.done
            FROM checklist_runs r JOIN checklist_run_items i ON i.run_id = r.id
            WHERE r.run_date >= ?
            """, (oldest,)
        ).fetchall()]
        schedule = _schedule_logs(conn)
        since = conn.execute(
            "SELECT value FROM meta WHERE key='manager_check_since'").fetchone()
    checks_since = since["value"] if since else ""
    by_key = {(r["template_id"], r["frequency"], r["run_date"]): r for r in runs}
    app_days = sorted({r["run_date"] for r in runs if r["frequency"] == "daily"
                       and date.fromisoformat(r["run_date"]).weekday()
                       not in CLOSED_WEEKDAYS})
    period_runs = {(r["frequency"], r["run_date"]) for r in runs}

    def app_used(frequency: str, start: str) -> bool:
        """Did anyone open the app on an open day of this period? (Any
        list's daily run in it, or another list's run for the same
        week/month — those are never started on a closed day.)"""
        end = period_end(frequency, start)
        return ((frequency, start) in period_runs
                or any(start <= d <= end for d in app_days))


    cards = []
    for t in templates:
        freq = t["frequency"] if t["frequency"] in FREQUENCIES else "daily"
        current_start = period_start(freq, today)
        cur_run = by_key.get((t["id"], freq, current_start))
        first_run = first_runs.get((t["id"], freq))
        history = []
        if first_run:
            for start in window_starts[freq]:
                if start < period_start(freq, first_run):
                    break
                run = by_key.get((t["id"], freq, start))
                if not _owed(schedule.get(t["id"]), freq, start, has_run=bool(run)):
                    continue  # retired or on another schedule by then
                if not run and app_used(freq, start):
                    continue  # only scheduled after the app was last used
                total = run["total"] if run else t["item_count"]
                done = run["done"] if run else 0
                history.append({
                    "start": start, "label": period_label(freq, start),
                    "opened": bool(run), "total": total, "done": done,
                    "complete": bool(run) and total > 0 and done == total,
                    "checkable": period_end(freq, start) >= checks_since,
                    "verified": bool(run and run["verified_at"]),
                    "owner": run["owner"] if run else None,
                    "pct": round(done / total * 100) if total else 0,
                })

        def summarize(rows: list[dict]) -> dict | None:
            if not rows:
                return None
            total = sum(r["total"] for r in rows)
            return {
                "periods": len(rows),
                "pct": round(sum(r["done"] for r in rows) / total * 100) if total else 0,
                "complete": sum(1 for r in rows if r["complete"]),
                "verified": sum(1 for r in rows if r["verified"] and r["checkable"]),
                "verified_of": sum(1 for r in rows if r["checkable"]),
                "not_opened": sum(1 for r in rows if not r["opened"]),
            }

        streak = 0
        for row in history:
            if not row["complete"]:
                break
            streak += 1
        counted = {r["start"] for r in history}
        missed: dict[str, list[int]] = {}
        for m in misses:
            if (m["template_id"] == t["id"] and m["frequency"] == freq
                    and m["run_date"] in counted):
                tally = missed.setdefault(m["label"], [0, 0])
                tally[1] += 1
                if not m["done"]:
                    tally[0] += 1
        most_missed = sorted(((label, n, of) for label, (n, of) in missed.items() if n),
                             key=lambda x: (-x[1] / x[2], -x[1], x[0]))[:3]
        cards.append({
            **t, "frequency": freq, "first_run": first_run,
            "current": ({"total": cur_run["total"], "done": cur_run["done"],
                         "verified": bool(cur_run["verified_at"]),
                         "owner": cur_run["owner"], "run_id": cur_run["id"],
                         "label": period_label(freq, current_start)}
                        if cur_run else None),
            "history": history,
            "recent": summarize(history[:7] if freq == "daily" else history),
            "window": summarize(history),
            "streak": streak,
            "most_missed": [{"label": label, "missed": n, "of": of}
                            for label, n, of in most_missed],
        })
    return cards


def item_run_id(item_id: int) -> int | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT run_id FROM checklist_run_items WHERE id=?", (item_id,)
        ).fetchone()
    return row["run_id"] if row else None


# --- template management (admin) ---

def all_templates(include_inactive: bool = False) -> list[dict]:
    q = "SELECT t.*, COUNT(i.id) AS item_count FROM checklist_templates t " \
        "LEFT JOIN checklist_template_items i ON i.template_id = t.id "
    if not include_inactive:
        q += "WHERE t.active=1 "
    q += "GROUP BY t.id ORDER BY t.sort, t.id"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def template_with_items(template_id: int) -> tuple[dict | None, list[dict]]:
    with closing(connect()) as conn:
        t = conn.execute(
            "SELECT * FROM checklist_templates WHERE id=?", (template_id,)
        ).fetchone()
        if not t:
            return None, []
        items = conn.execute(
            "SELECT * FROM checklist_template_items WHERE template_id=? ORDER BY sort, id",
            (template_id,),
        ).fetchall()
    return dict(t), [dict(i) for i in items]


def _clean_schedule(frequency: str, due_weekday) -> tuple[str, int | None]:
    frequency = frequency if frequency in FREQUENCIES else "daily"
    if (frequency != "weekly" or due_weekday not in range(7)
            or due_weekday in CLOSED_WEEKDAYS):
        due_weekday = None
    return frequency, due_weekday


def create_template(name: str, daypart: str, area: str,
                    items: list[tuple[str, bool]], frequency: str = "daily",
                    due_weekday: int | None = None, side: str | None = None) -> int:
    """items are (label, critical) pairs. No (or an unknown) side: worked
    out from the area."""
    frequency, due_weekday = _clean_schedule(frequency, due_weekday)
    side = side if side in CHECKLIST_SIDES else side_for(area, name)
    with closing(connect()) as conn, conn:
        sort = conn.execute(
            "SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM checklist_templates"
        ).fetchone()["s"]
        cur = conn.execute(
            "INSERT INTO checklist_templates (name, daypart, area, sort, frequency, "
            "due_weekday, side) VALUES (?,?,?,?,?,?,?)",
            (name, daypart, area, sort, frequency, due_weekday, side),
        )
        conn.executemany(
            "INSERT INTO checklist_template_items (template_id, label, critical, sort) "
            "VALUES (?,?,?,?)",
            [(cur.lastrowid, label, 1 if critical else 0, i)
             for i, (label, critical) in enumerate(items)],
        )
        _seed_schedule_log(conn)   # a new list's history starts at its first run
        return cur.lastrowid


def update_template(template_id: int, name: str, daypart: str, area: str,
                    items: list[tuple[str, bool]], frequency: str = "daily",
                    due_weekday: int | None = None, side: str | None = None) -> None:
    """Replace a template's fields and item list ((label, critical) pairs).
    Existing runs keep their snapshotted items (and frequency, and their
    item ids — a page left open must never check a different item); only
    runs started later pick up the change."""
    frequency, due_weekday = _clean_schedule(frequency, due_weekday)
    side = side if side in CHECKLIST_SIDES else side_for(area, name)
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE checklist_templates SET name=?, daypart=?, area=?, frequency=?, "
            "due_weekday=?, side=? WHERE id=?",
            (name, daypart, area, frequency, due_weekday, side, template_id),
        )
        conn.execute(
            "DELETE FROM checklist_template_items WHERE template_id=?", (template_id,)
        )
        conn.executemany(
            "INSERT INTO checklist_template_items (template_id, label, critical, sort) "
            "VALUES (?,?,?,?)",
            [(template_id, label, 1 if critical else 0, i)
             for i, (label, critical) in enumerate(items)],
        )
        _log_schedule(conn, template_id)


def set_template_active(template_id: int, active: bool) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE checklist_templates SET active=? WHERE id=?",
            (1 if active else 0, template_id),
        )
        _log_schedule(conn, template_id)


# ---------------------------------------------------------------------------
# Leaders (app logins)
# ---------------------------------------------------------------------------

def leaders(include_inactive: bool = False) -> list[dict]:
    q = ("SELECT id, name, role, active, created_at, slack_id, area, checklist_side "
         "FROM leaders ")
    if not include_inactive:
        q += "WHERE active=1 "
    q += "ORDER BY name COLLATE NOCASE"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_leader(name: str, pin: str, role: str = "lead",
               may_replace: bool = True) -> str | None:
    """Create (or reactivate) a leader login. Returns an error message or None.
    Re-adding an existing name REPLACES its PIN and role, so only the
    Operator master login may do it (may_replace=False refuses): for anyone
    else it would be a PIN reset by another name — log in as that leader,
    read their private 1:1."""
    name = " ".join(name.split())
    if not name:
        return "Name is required."
    if name.lower() == "operator":
        return "“Operator” is reserved for the admin PIN login."
    if not pin.isdigit() or len(pin) < 4:
        return "PIN must be at least 4 digits."
    pin_hash = generate_password_hash(pin)
    with closing(connect()) as conn, conn:
        existing = conn.execute(
            "SELECT id FROM leaders WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing and not may_replace:
            return (f"{name} already has a login — only the Operator can "
                    "reset a PIN.")
        if existing:
            conn.execute(
                "UPDATE leaders SET pin_hash=?, role=?, active=1 WHERE id=?",
                (pin_hash, role, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO leaders (name, pin_hash, role, active, created_at) "
                "VALUES (?,?,?,1,?)",
                (name, pin_hash, role, now_stamp()),
            )
    return None


def verify_leader(name: str, pin: str) -> dict | None:
    """Return the leader row if name+PIN check out, else None."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM leaders WHERE name=? COLLATE NOCASE AND active=1", (name,)
        ).fetchone()
    if row and check_password_hash(row["pin_hash"], pin):
        return dict(row)
    return None


def leader_is_active(leader_id: int) -> bool:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT active FROM leaders WHERE id=?", (leader_id,)
        ).fetchone()
    return bool(row and row["active"])


def get_leader(leader_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, name, role, active, area, checklist_side FROM leaders WHERE id=?",
            (leader_id,)
        ).fetchone()
    return dict(row) if row else None


def set_leader_active(leader_id: int, active: bool) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE leaders SET active=? WHERE id=?", (1 if active else 0, leader_id)
        )


def reset_leader_pin(leader_id: int, pin: str) -> str | None:
    if not pin.isdigit() or len(pin) < 4:
        return "PIN must be at least 4 digits."
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE leaders SET pin_hash=? WHERE id=?",
            (generate_password_hash(pin), leader_id),
        )
    return None


def set_leader_checklist_view(leader_id: int, view: str) -> bool:
    """Which side's checklists a leader sees: all, foh or boh."""
    if view not in CHECKLIST_VIEWS:
        return False
    with closing(connect()) as conn, conn:
        return conn.execute("UPDATE leaders SET checklist_side=? WHERE id=?",
                            (view, leader_id)).rowcount > 0


def set_leader_role(leader_id: int, role: str) -> bool:
    """Promote/demote an existing leader. The before_request role refresh
    makes the change effective on their next request, no re-login needed."""
    if role not in ("lead", "admin"):
        return False
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE leaders SET role=? WHERE id=?", (role, leader_id)
        )
        return cur.rowcount > 0


def active_admin_count() -> int:
    """Active admin leader accounts — the lockout guard when the Operator
    master PIN isn't configured."""
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM leaders WHERE role='admin' AND active=1"
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Leadership development course
# ---------------------------------------------------------------------------

def course_overview(leader_id: int) -> list[dict]:
    """[{module, lessons: [{lesson fields + done/completed_at/note/recorded_by}],
    done, total}] in course order, for one leader."""
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT l.*, p.completed_at, p.note, p.recorded_by,
                   p.leader_id IS NOT NULL AS done
            FROM course_lessons l
            LEFT JOIN lesson_progress p
                ON p.lesson_id = l.id AND p.leader_id = ?
            WHERE l.active = 1
            ORDER BY l.sort, l.id
            """,
            (leader_id,),
        ).fetchall()
    modules: list[dict] = []
    for r in rows:
        r = dict(r)
        if not modules or modules[-1]["module"] != r["module"]:
            modules.append({"module": r["module"], "lessons": [], "done": 0, "total": 0})
        modules[-1]["lessons"].append(r)
        modules[-1]["total"] += 1
        modules[-1]["done"] += 1 if r["done"] else 0
    return modules


def course_lesson_count() -> int:
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM course_lessons WHERE active=1"
        ).fetchone()["c"]


def leader_course_summary() -> list[dict]:
    """Active leaders with their completed-lesson counts, for the admin index."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT ld.id, ld.name, ld.role,
                   COUNT(cl.id) AS done,
                   MAX(CASE WHEN cl.id IS NOT NULL THEN p.completed_at END)
                       AS last_completed
            FROM leaders ld
            LEFT JOIN lesson_progress p ON p.leader_id = ld.id
            LEFT JOIN course_lessons cl ON cl.id = p.lesson_id AND cl.active = 1
            WHERE ld.active = 1
            GROUP BY ld.id
            ORDER BY ld.name COLLATE NOCASE
            """
        ).fetchall()]


def set_lesson_done(lesson_id: int, leader_id: int, note: str, by: str) -> bool:
    """Mark a lesson complete for a leader (or update its note if already
    complete — the original completion date is kept). Returns False for
    unknown lesson/leader ids instead of raising."""
    with closing(connect()) as conn, conn:
        if not conn.execute(
            "SELECT 1 FROM course_lessons WHERE id=? AND active=1", (lesson_id,)
        ).fetchone() or not conn.execute(
            "SELECT 1 FROM leaders WHERE id=?", (leader_id,)
        ).fetchone():
            return False
        conn.execute(
            "INSERT INTO lesson_progress (lesson_id, leader_id, completed_at, note, "
            "recorded_by) VALUES (?,?,?,?,?) "
            "ON CONFLICT(lesson_id, leader_id) DO UPDATE SET "
            "note=excluded.note, recorded_by=excluded.recorded_by",
            (lesson_id, leader_id, now_stamp(), note or None, by),
        )
    return True


def clear_lesson_done(lesson_id: int, leader_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "DELETE FROM lesson_progress WHERE lesson_id=? AND leader_id=?",
            (lesson_id, leader_id),
        )


# ---------------------------------------------------------------------------
# Leader to-dos
# ---------------------------------------------------------------------------

def add_task(leader_id: int, title: str, details: str, due_date: str,
             assigned_by: str) -> bool:
    """Assign a to-do. Returns False for an unknown leader or empty title."""
    title = " ".join((title or "").split())
    if not title:
        return False
    with closing(connect()) as conn, conn:
        if not conn.execute(
            "SELECT 1 FROM leaders WHERE id=?", (leader_id,)
        ).fetchone():
            return False
        conn.execute(
            "INSERT INTO leader_tasks (leader_id, title, details, due_date, "
            "assigned_by, created_at) VALUES (?,?,?,?,?,?)",
            (leader_id, title, (details or "").strip() or None, due_date or None,
             assigned_by, now_stamp()),
        )
    return True


def tasks_for_leader(leader_id: int) -> tuple[list[dict], list[dict]]:
    """(open, completed) task lists: open by due date then age, completed
    newest first."""
    with closing(connect()) as conn:
        open_tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM leader_tasks WHERE leader_id=? AND completed_at IS NULL "
            "ORDER BY due_date IS NULL, due_date, id",
            (leader_id,),
        ).fetchall()]
        done_tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM leader_tasks WHERE leader_id=? AND completed_at IS NOT NULL "
            "ORDER BY completed_at DESC, id DESC",
            (leader_id,),
        ).fetchall()]
    today = today_local()
    for t in open_tasks:
        t["overdue"] = bool(t["due_date"] and t["due_date"] < today)
    return open_tasks, done_tasks


def tasks_assigned_by(name: str, days: int = 7) -> list[dict]:
    """To-dos this person handed to active leaders (assigned_by holds the
    assigner's sign-in name), for Meetings › You assigned: every open one,
    newest first, then those done in the last `days` days, most recently
    done first. Each carries the assignee's leader_name. Read-only."""
    name = " ".join((name or "").split())
    if not name:
        return []
    since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT t.*, ld.name AS leader_name
            FROM leader_tasks t
            JOIN leaders ld ON ld.id = t.leader_id
            WHERE ld.active = 1 AND t.assigned_by = ?
              AND (t.completed_at IS NULL OR t.completed_at >= ?)
            ORDER BY t.completed_at IS NOT NULL,
                     COALESCE(t.completed_at, t.created_at) DESC, t.id DESC
            """,
            (name, since),
        ).fetchall()]
    today = today_local()
    for t in rows:
        t["overdue"] = bool(not t["completed_at"] and t["due_date"]
                            and t["due_date"] < today)
    return rows


def open_tasks_for_leader(leader_id: int, limit: int = 5) -> list[dict]:
    """The leader's open to-dos for the Today dashboard."""
    open_tasks, _ = tasks_for_leader(leader_id)
    return open_tasks[:limit]


def get_task(task_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM leader_tasks WHERE id=?", (task_id,)
        ).fetchone()
    return dict(row) if row else None


def set_task_done(task_id: int, done: bool, by: str) -> bool:
    """Complete or reopen a to-do. Returns False for an unknown id."""
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE leader_tasks SET completed_at=?, completed_by=? WHERE id=?",
            (now_stamp() if done else None, by if done else None, task_id),
        )
        return cur.rowcount > 0


def delete_task(task_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM leader_tasks WHERE id=?", (task_id,))


def open_task_counts(leader_id: int | None = None) -> tuple[int, int]:
    """(open, overdue) to-dos for one leader, or with None for every active
    leader — the To-dos tab and ☰ count. Overdue as leader_task_summary
    counts it (a due date before today)."""
    q = ("SELECT COUNT(*), COALESCE(SUM(t.due_date < ?), 0) FROM leader_tasks t "
         "JOIN leaders ld ON ld.id = t.leader_id "
         "WHERE ld.active = 1 AND t.completed_at IS NULL")
    args: list = [today_local()]
    if leader_id is not None:
        q += " AND t.leader_id = ?"
        args.append(leader_id)
    with closing(connect()) as conn:
        row = conn.execute(q, args).fetchone()
    return row[0], row[1]


def leader_task_summary() -> list[dict]:
    """Active leaders with open/overdue to-do counts, for the admin index."""
    today = today_local()
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT ld.id, ld.name, ld.role,
                   -- t.id IS NOT NULL: don't count the LEFT JOIN's null row
                   -- for leaders with no tasks at all
                   COUNT(CASE WHEN t.id IS NOT NULL AND t.completed_at IS NULL
                              THEN 1 END) AS open,
                   COUNT(CASE WHEN t.completed_at IS NULL AND t.due_date < ?
                              THEN 1 END) AS overdue,
                   MAX(t.completed_at) AS last_completed
            FROM leaders ld
            LEFT JOIN leader_tasks t ON t.leader_id = ld.id
            WHERE ld.active = 1
            GROUP BY ld.id
            ORDER BY ld.name COLLATE NOCASE
            """,
            (today,),
        ).fetchall()]


# ---------------------------------------------------------------------------
# 1:1 meeting agendas
# ---------------------------------------------------------------------------

def check_stamp(done_at: str | None, on_day: str | None) -> str:
    """How a check's time reads beside a list: just the time when it was
    made on business day `on_day` (a daily list's own day — a 1am close
    check included), else with the date (a week/month-long list, or an
    Operator correction made later)."""
    if not done_at:
        return ""
    return done_at[11:] if on_day and business_date(done_at) == on_day else done_at[5:]


def business_date(stamp: str) -> str:
    """A now_stamp() value -> the 4am-rollover business date it belongs to."""
    try:
        return (datetime.strptime(stamp, "%Y-%m-%d %H:%M")
                - timedelta(hours=4)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return (stamp or "")[:10]


# kind -> (topic table, owner column, owner table). These are the ONLY
# identifiers ever interpolated into the 1:1 SQL below; an unknown kind is a
# programming error (KeyError), never reachable from request data.
_ONEONONE_KINDS = {
    "leader": ("oneonone_topics", "leader_id", "leaders"),
    "member": ("oneonone_member_topics", "member_id", "team_members"),
}


def _thread_where(kind: str, holder: int | None) -> tuple[str, tuple]:
    """Member threads are per holder (NULL = the Operator's own); leader
    agendas have one thread per leader. `IS ?` matches NULL safely."""
    if kind == "member":
        return " AND holder_leader_id IS ?", (holder,)
    return "", ()


def _add_topic(kind: str, owner_id: int, topic: str, by: str,
               holder: int | None = None) -> bool:
    """Add a talking point to an agenda. Returns False for an empty topic,
    an unknown owner, or (member threads) an unknown holder."""
    table, col, owner = _ONEONONE_KINDS[kind]
    topic = " ".join((topic or "").split())
    if not topic:
        return False
    with closing(connect()) as conn, conn:
        if not conn.execute(
            f"SELECT 1 FROM {owner} WHERE id=?", (owner_id,)
        ).fetchone():
            return False
        if kind == "member":
            if holder is not None and not conn.execute(
                "SELECT 1 FROM leaders WHERE id=?", (holder,)
            ).fetchone():
                return False
            conn.execute(
                f"INSERT INTO {table} ({col}, holder_leader_id, topic, added_by, "
                "created_at) VALUES (?,?,?,?,?)",
                (owner_id, holder, topic, by, now_stamp()),
            )
        else:
            conn.execute(
                f"INSERT INTO {table} ({col}, topic, added_by, created_at) "
                "VALUES (?,?,?,?)",
                (owner_id, topic, by, now_stamp()),
            )
    return True


def _topics_for(kind: str, owner_id: int, meetings: int = 12,
                holder: int | None = None) \
        -> tuple[list[dict], list[dict]]:
    """(agenda, history). agenda: undiscussed topics, longest-waiting first,
    each flagged carried=True when it survived at least one past 1:1.
    history: [{'date', 'topics'}] newest meeting first, capped at
    `meetings` distinct dates. One transaction so a check-off landing
    mid-read can't show a topic in both lists."""
    table, col, _ = _ONEONONE_KINDS[kind]
    where, params = _thread_where(kind, holder)
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        agenda = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {col}=?{where} "
            "AND discussed_at IS NULL ORDER BY created_at, id",
            (owner_id, *params),
        ).fetchall()]
        discussed = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {col}=?{where} "
            "AND discussed_at IS NOT NULL "
            "ORDER BY discussed_on DESC, discussed_at, id",
            (owner_id, *params),
        ).fetchall()]
        conn.commit()
    last_met = max((t["discussed_on"] for t in discussed if t["discussed_on"]),
                   default=None)
    for t in agenda:
        # Strictly before the last meeting's business date: a topic added on
        # meeting day (before or after the sit-down) was never "carried".
        t["carried"] = bool(last_met and business_date(t["created_at"]) < last_met)
    history: list[dict] = []
    for t in discussed:
        if not history or history[-1]["date"] != t["discussed_on"]:
            if len(history) >= meetings:
                break
            history.append({"date": t["discussed_on"], "topics": []})
        history[-1]["topics"].append(t)
    return agenda, history


def _get_topic(kind: str, topic_id: int) -> dict | None:
    table = _ONEONONE_KINDS[kind][0]
    with closing(connect()) as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (topic_id,)
        ).fetchone()
    return dict(row) if row else None


def _set_discussed(kind: str, topic_id: int, discussed: bool, note: str,
                   by: str) -> bool:
    """Check a topic off during the 1:1 (or reopen it). Guarded flip like
    set_recovery_resolved: only rows in the opposite state change, so the
    first stamp wins. Reopening clears all four columns."""
    table = _ONEONONE_KINDS[kind][0]
    guard = "IS NULL" if discussed else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            f"UPDATE {table} SET discussed_at=?, discussed_by=?, "
            f"discussed_on=?, outcome_note=? WHERE id=? AND discussed_at {guard}",
            (now_stamp() if discussed else None,
             by if discussed else None,
             today_local() if discussed else None,
             (" ".join((note or "").split()) or None) if discussed else None,
             topic_id),
        )
        return cur.rowcount > 0


def _delete_topic(kind: str, topic_id: int) -> bool:
    """Remove an undiscussed topic. Discussed topics are meeting history
    and can only leave via reopen — guarded at the SQL level."""
    table = _ONEONONE_KINDS[kind][0]
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE id=? AND discussed_at IS NULL",
            (topic_id,),
        )
        return cur.rowcount > 0


# Leader agendas — shared between each leader and the Operator.

def add_oneonone_topic(leader_id: int, topic: str, by: str) -> bool:
    return _add_topic("leader", leader_id, topic, by)


def oneonone_for_leader(leader_id: int, meetings: int = 12) \
        -> tuple[list[dict], list[dict]]:
    return _topics_for("leader", leader_id, meetings)


def get_topic(topic_id: int) -> dict | None:
    return _get_topic("leader", topic_id)


def set_topic_discussed(topic_id: int, discussed: bool, note: str,
                        by: str) -> bool:
    return _set_discussed("leader", topic_id, discussed, note, by)


def delete_topic(topic_id: int) -> bool:
    return _delete_topic("leader", topic_id)


# Team-member threads — per holder: the Operator (holder None) or a leader.

def add_member_topic(member_id: int, topic: str, by: str,
                     holder_id: int | None = None) -> bool:
    return _add_topic("member", member_id, topic, by, holder=holder_id)


def oneonone_for_member(member_id: int, holder_id: int | None = None,
                        meetings: int = 12) -> tuple[list[dict], list[dict]]:
    return _topics_for("member", member_id, meetings, holder=holder_id)


def get_member_topic(topic_id: int) -> dict | None:
    return _get_topic("member", topic_id)


def set_member_topic_discussed(topic_id: int, discussed: bool, note: str,
                               by: str) -> bool:
    return _set_discussed("member", topic_id, discussed, note, by)


def delete_member_topic(topic_id: int) -> bool:
    return _delete_topic("member", topic_id)


def get_member(member_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, name, role, active, created_at, slack_id FROM team_members "
            "WHERE id=?", (member_id,)
        ).fetchone()
    return dict(row) if row else None


def active_leader_named(name: str) -> dict | None:
    """The active leader login with this name (case-insensitive), if any.
    Matched against `leaders`, never team_members.role — nothing writes
    role='leader' there (add_member writes 'team')."""
    name = " ".join((name or "").split())
    if not name:
        return None
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, name, role FROM leaders "
            "WHERE active=1 AND name=? COLLATE NOCASE", (name,)
        ).fetchone()
    return dict(row) if row else None


def active_leader_for_member(member: dict) -> dict | None:
    """The active leader login this roster row is — same name, or the
    Slack account the pull matched to that login."""
    leader = active_leader_named(member.get("name") or "")
    if leader or not member.get("slack_id"):
        return leader
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, name, role FROM leaders WHERE active=1 AND slack_id=?",
            (member["slack_id"],)
        ).fetchone()
    return dict(row) if row else None


def oneonone_people() -> list[dict]:
    """Everyone the Operator can open a 1:1 with, for the picker: active
    leader logins (kind 'leader') plus active roster members who aren't
    also an active leader (one entry per person)."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT 'leader' AS kind, id, name FROM leaders WHERE active=1
            UNION ALL
            SELECT 'member', m.id, m.name FROM team_members m
            WHERE m.active=1 AND NOT EXISTS (
                SELECT 1 FROM leaders ld
                WHERE ld.active=1 AND (ld.name = m.name COLLATE NOCASE
                                       OR ld.slack_id = m.slack_id))
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()]


def member_oneonone_index(holder_id: int | None = None) -> dict:
    """One holder's team-member section: {'threads', 'everyone', 'former'}
    — the Operator's own threads (holder None) or one leader's. Never
    counts anyone else's topics.
    threads: active members with any 1:1 topic, open agendas first then
    longest since a 1:1. everyone: every active member (A–Z, with an
    `initial` for letter dividers). former: deactivated members who still
    have 1:1 history. A roster name matching an active leader login is
    skipped unless an older member thread exists (that person lives under
    Leaders)."""
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT m.id, m.name, m.active, COUNT(t.id) AS topics,
                   COUNT(CASE WHEN t.id IS NOT NULL AND t.discussed_at IS NULL
                              THEN 1 END) AS open,
                   MAX(t.discussed_on) AS last_met,
                   (SELECT ld.id FROM leaders ld WHERE ld.active=1
                    AND (ld.name = m.name COLLATE NOCASE OR ld.slack_id = m.slack_id)
                    LIMIT 1) AS leader_id
            FROM team_members m
            LEFT JOIN oneonone_member_topics t
                   ON t.member_id = m.id AND t.holder_leader_id IS ?
            GROUP BY m.id
            HAVING m.active = 1 OR COUNT(t.id) > 0
            ORDER BY m.name COLLATE NOCASE
            """, (holder_id,)
        ).fetchall()]
    threads, everyone, former = [], [], []
    for r in rows:
        first = r["name"][:1].upper()
        r["initial"] = first if first.isalpha() else "#"
        if r["leader_id"] and not r["topics"]:
            continue
        if not r["active"]:
            former.append(r)
            continue
        everyone.append(r)
        if r["topics"]:
            threads.append(r)
    threads.sort(key=lambda r: (r["open"] == 0, r["last_met"] or "",
                                r["name"].casefold()))
    return {"threads": threads, "everyone": everyone, "former": former}


def leader_member_threads() -> list[dict]:
    """Every leader-held team-member 1:1, for the Operator's overview (the
    Operator sees all agendas): one row per (leader, member) thread, open
    agendas first."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT t.member_id, m.name AS member_name, m.active AS member_active,
                   t.holder_leader_id AS holder_id, ld.name AS holder_name,
                   COUNT(CASE WHEN t.discussed_at IS NULL THEN 1 END) AS open,
                   MAX(t.discussed_on) AS last_met
            FROM oneonone_member_topics t
            JOIN team_members m ON m.id = t.member_id
            JOIN leaders ld ON ld.id = t.holder_leader_id
            GROUP BY t.holder_leader_id, t.member_id
            ORDER BY ld.name COLLATE NOCASE, open = 0,
                     m.name COLLATE NOCASE
            """
        ).fetchall()]


def open_member_topic_count(leader_id: int | None) -> int:
    """Open topics across one leader's own team-member 1:1s."""
    if leader_id is None:
        return 0
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM oneonone_member_topics "
            "WHERE holder_leader_id=? AND discussed_at IS NULL", (leader_id,)
        ).fetchone()[0]


def oneonone_summary() -> list[dict]:
    """Active leaders with open-agenda counts and last-meeting dates, for
    the Operator's 1:1 index."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT ld.id, ld.name, ld.role,
                   COUNT(CASE WHEN t.id IS NOT NULL AND t.discussed_at IS NULL
                              THEN 1 END) AS open,
                   MAX(t.discussed_on) AS last_met
            FROM leaders ld
            LEFT JOIN oneonone_topics t ON t.leader_id = ld.id
            WHERE ld.active = 1
            GROUP BY ld.id
            ORDER BY ld.name COLLATE NOCASE
            """
        ).fetchall()]


def open_topic_total() -> int:
    """Every leader's open agenda topics plus the Operator's own team-member
    threads — the Operator's Meetings badge (matches that page's tabs)."""
    with closing(connect()) as conn:
        agendas = conn.execute(
            "SELECT COUNT(*) FROM oneonone_topics t JOIN leaders l ON l.id = t.leader_id "
            "WHERE l.active=1 AND t.discussed_at IS NULL").fetchone()[0]
        own_threads = conn.execute(
            "SELECT COUNT(*) FROM oneonone_member_topics "
            "WHERE holder_leader_id IS NULL AND discussed_at IS NULL").fetchone()[0]
    return agendas + own_threads


def open_topic_count(leader_id: int | None) -> int:
    """Open-agenda badge for the More card; 0 for the Operator session."""
    if leader_id is None:
        return 0
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM oneonone_topics "
            "WHERE leader_id=? AND discussed_at IS NULL",
            (leader_id,),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Shout-outs (recognition)
# ---------------------------------------------------------------------------

def add_shoutout(member_name: str, value_tag: str | None, message: str,
                 by: str, share: bool = False) -> int | None:
    """Post recognition for a team member. Returns the new row id, or None
    when name or message is empty. share stamps that a Slack cross-post was
    REQUESTED (delivery is best-effort, handled by the route)."""
    member_name = " ".join((member_name or "").split())
    message = (message or "").strip()
    if not member_name or not message:
        return None
    stamp = now_stamp()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO shoutouts (shout_date, member_name, value_tag, "
            "message, author, created_at, shared_at) VALUES (?,?,?,?,?,?,?)",
            (today_local(), member_name,
             value_tag if value_tag in SHOUTOUT_VALUES else None,
             message, by, stamp, stamp if share else None),
        )
        return cur.lastrowid


def shoutout_feed(limit: int = 100, value_tag: str | None = None) -> list[dict]:
    q = "SELECT * FROM shoutouts "
    params: list = []
    if value_tag:
        q += "WHERE value_tag=? "
        params.append(value_tag)
    q += "ORDER BY shout_date DESC, id DESC LIMIT ?"
    params.append(limit)
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def recent_shoutouts(days: int = 7, limit: int = 3) -> list[dict]:
    """The Today-screen strip — fresh praise only, so it ages off the
    dashboard by itself."""
    since = (date.fromisoformat(today_local())
             - timedelta(days=days - 1)).isoformat()
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM shoutouts WHERE shout_date>=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (since, limit),
        ).fetchall()]


def mark_shoutout_delivered(shoutout_id: int) -> None:
    """Called by the background poster once Slack answers ok:true — the
    feed's '📣 Slack' badge renders from this, not from the request."""
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE shoutouts SET delivered_at=? WHERE id=?",
                     (now_stamp(), shoutout_id))


def get_shoutout(shoutout_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM shoutouts WHERE id=?", (shoutout_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_shoutout(shoutout_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM shoutouts WHERE id=?", (shoutout_id,))


def shoutout_counts(days: int = 28) -> dict:
    """{'receivers': [{'name','count'}], 'givers': [...]}, biggest first —
    the Operator's recognition radar (who earns praise, who gives it)."""
    since = (date.fromisoformat(today_local())
             - timedelta(days=days - 1)).isoformat()
    with closing(connect()) as conn:
        receivers = [dict(r) for r in conn.execute(
            "SELECT member_name AS name, COUNT(*) AS count FROM shoutouts "
            "WHERE shout_date>=? GROUP BY member_name COLLATE NOCASE "
            "ORDER BY count DESC, name",
            (since,),
        ).fetchall()]
        givers = [dict(r) for r in conn.execute(
            "SELECT author AS name, COUNT(*) AS count FROM shoutouts "
            "WHERE shout_date>=? AND author IS NOT NULL "
            "GROUP BY author COLLATE NOCASE ORDER BY count DESC, name",
            (since,),
        ).fetchall()]
    return {"receivers": receivers, "givers": givers}


# ---------------------------------------------------------------------------
# Guest recovery
# ---------------------------------------------------------------------------

def _annotate_recovery(r: dict) -> dict:
    """Adds age_days (business days open, 4am rollover honored via
    today_local), stale (open 2+ days — the queue's overdue analog), and
    phone_digits (digits plus a leading '+' kept, for a safe tel: href)."""
    try:
        age = (date.fromisoformat(today_local())
               - date.fromisoformat(r["recovery_date"])).days
    except ValueError:
        age = 0
    r["age_days"] = max(0, age)
    r["stale"] = r["age_days"] >= 2
    phone = (r["guest_phone"] or "").strip()
    digits = "".join(c for c in phone if c.isdigit())
    r["phone_digits"] = ("+" + digits if phone.startswith("+") else digits) \
        if digits else ""
    return r


def add_recovery(guest_name: str, guest_phone: str, guest_email: str,
                 issue: str, remedy: str, details: str, follow_up: bool,
                 by: str, resolved_now: bool = False,
                 contacted_now: bool = False) -> int | None:
    """Log a recovery. Returns the new row id, or None for an empty guest
    name. Unknown issue/remedy values are stored as 'other' (double-guard
    under the route's coercion). resolved_now stamps the resolution in the
    same INSERT, so handled-on-the-spot never shows in the open queue;
    contacted_now stamps the contact, for "already talked to the guest —
    they'll come back for it" (resolved_now wins if both are set)."""
    guest_name = " ".join((guest_name or "").split())
    if not guest_name:
        return None
    stamp = now_stamp()
    contacted = contacted_now and not resolved_now
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO guest_recoveries (recovery_date, guest_name, "
            "guest_phone, guest_email, issue, remedy, details, follow_up, "
            "logged_by, created_at, contacted_at, contacted_by, "
            "resolved_at, resolved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (today_local(), guest_name,
             (guest_phone or "").strip() or None,
             (guest_email or "").strip() or None,
             issue if issue in RECOVERY_ISSUES else "other",
             remedy if remedy in RECOVERY_REMEDIES else "other",
             (details or "").strip() or None,
             1 if follow_up else 0, by, stamp,
             stamp if contacted else None,
             by if contacted else None,
             stamp if resolved_now else None,
             by if resolved_now else None),
        )
        return cur.lastrowid


def recovery_feed(resolved_limit: int = 50) \
        -> tuple[list[dict], list[dict], list[dict]]:
    """(open, awaiting, resolved): open (nobody has reached the guest yet)
    and awaiting (contacted — guest coming back for the remedy) oldest-first
    so the longest-waiting guest is on top; resolved newest-first, capped.
    All reads share one transaction (same reason as export_json) so a flip
    landing between them can't show one recovery in two lists."""
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        open_recs = [_annotate_recovery(dict(r)) for r in conn.execute(
            "SELECT * FROM guest_recoveries WHERE resolved_at IS NULL "
            "AND contacted_at IS NULL ORDER BY recovery_date, id"
        ).fetchall()]
        awaiting = [_annotate_recovery(dict(r)) for r in conn.execute(
            "SELECT * FROM guest_recoveries WHERE resolved_at IS NULL "
            "AND contacted_at IS NOT NULL ORDER BY recovery_date, id"
        ).fetchall()]
        resolved = [dict(r) for r in conn.execute(
            "SELECT * FROM guest_recoveries WHERE resolved_at IS NOT NULL "
            "ORDER BY resolved_at DESC, id DESC LIMIT ?",
            (resolved_limit,),
        ).fetchall()]
        conn.commit()
    # How long each contacted guest has been expected back — measured from
    # the contact, not from when the issue was first logged.
    today = date.fromisoformat(today_local())
    for r in awaiting:
        try:
            r["waiting_days"] = max(0, (today - date.fromisoformat(
                business_date(r["contacted_at"]))).days)
        except ValueError:
            r["waiting_days"] = 0
    return open_recs, awaiting, resolved


def open_recovery_count() -> int:
    """Truly-open recoveries (nobody has reached the guest yet) — matches
    recovery_feed's open bucket, so badges never contradict the page."""
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM guest_recoveries "
            "WHERE resolved_at IS NULL AND contacted_at IS NULL"
        ).fetchone()[0]


def get_recovery(recovery_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM guest_recoveries WHERE id=?", (recovery_id,)
        ).fetchone()
    return dict(row) if row else None


def set_recovery_contacted(recovery_id: int, contacted: bool, note: str,
                           by: str) -> bool:
    """Mark that the guest was reached and will come back for the remedy
    (or undo a mis-tap). Guarded like set_recovery_resolved: only flips
    unresolved rows that are in the opposite contact state, so two leaders
    can't overwrite each other's stamp. Returns False for an unknown id or
    a no-op flip."""
    guard = "IS NULL" if contacted else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE guest_recoveries SET contacted_at=?, contacted_by=?, "
            f"contact_note=? WHERE id=? AND resolved_at IS NULL "
            f"AND contacted_at {guard}",
            (now_stamp() if contacted else None,
             by if contacted else None,
             ((note or "").strip() or None) if contacted else None,
             recovery_id),
        )
        return cur.rowcount > 0


def set_recovery_resolved(recovery_id: int, resolved: bool, note: str,
                          by: str) -> bool:
    """Resolve or reopen. Reopening clears all three resolution columns so
    no stale stamp survives. Only flips rows in the opposite state, so two
    leaders resolving from stale pages can't overwrite each other's
    who/when/note stamp. Returns False for an unknown id or a no-op flip."""
    guard = "IS NULL" if resolved else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE guest_recoveries SET resolved_at=?, resolved_by=?, "
            f"resolution_note=? WHERE id=? AND resolved_at {guard}",
            (now_stamp() if resolved else None,
             by if resolved else None,
             ((note or "").strip() or None) if resolved else None,
             recovery_id),
        )
        return cur.rowcount > 0


def delete_recovery(recovery_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM guest_recoveries WHERE id=?", (recovery_id,))


def recovery_issue_counts(days: int = 28) -> list[dict]:
    """[{'issue', 'count'}] for recent recoveries, biggest first — the
    Operator's repeat-issue radar on the recovery page. The window is
    exactly `days` business dates including today (hence days - 1)."""
    since = (date.fromisoformat(today_local())
             - timedelta(days=days - 1)).isoformat()
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT issue, COUNT(*) AS count FROM guest_recoveries "
            "WHERE recovery_date>=? GROUP BY issue ORDER BY count DESC, issue",
            (since,),
        ).fetchall()]


def purge_old_recovery_contacts(days: int = 90) -> int:
    """Clear guest phone/email (and the free-text contact note) on
    recoveries resolved more than `days` ago, and on contacted-but-never-
    came-back rows whose contact is older than that — otherwise the
    awaiting state would park guest PII forever. The row itself (name,
    issue, remedy, outcome) stays for history. Called on every
    recovery-page view, so old contact info ages out of the database and
    future backups without any cron. Returns rows purged."""
    cutoff = (date.fromisoformat(today_local()) - timedelta(days=days)).isoformat()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE guest_recoveries SET guest_phone=NULL, guest_email=NULL, "
            "contact_note=NULL WHERE "
            "((resolved_at IS NOT NULL AND resolved_at < ?) "
            " OR (resolved_at IS NULL AND contacted_at IS NOT NULL "
            "     AND contacted_at < ?)) "
            "AND (guest_phone IS NOT NULL OR guest_email IS NOT NULL "
            "     OR contact_note IS NOT NULL)",
            (cutoff, cutoff),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Equipment & repairs
# ---------------------------------------------------------------------------

# Food-safety problems first, then out-of-service ones, then oldest first.
_EQUIPMENT_ORDER = ("ORDER BY food_safety DESC, CASE impact WHEN 'down' THEN 0 "
                    "WHEN 'limited' THEN 1 ELSE 2 END, report_date, id")


def _days_since(day: str, today: date) -> int:
    try:
        return max(0, (today - date.fromisoformat(day)).days)
    except (TypeError, ValueError):
        return 0


def add_equipment_issue(equipment: str, area: str, impact: str, food_safety: bool,
                        problem: str, by: str, service_note: str = "",
                        fixed_now: bool = False) -> int | None:
    """Report a problem. Returns the new row id, or None for a blank
    equipment name. Unknown area/impact values are stored as 'other' /
    'limited'. A service_note ("Hobart, Thu AM") means service is already
    called, stamped in the same INSERT; fixed_now logs a problem already
    sorted out on the spot, for the repeat-repairs history (it wins over
    the called stamp, but a service note given with it is still kept)."""
    equipment = " ".join((equipment or "").split())[:EQUIPMENT_MAX]
    if not equipment:
        return None
    note = (service_note or "").strip() or None
    called = bool(note) and not fixed_now
    stamp = now_stamp()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO equipment_issues (report_date, equipment, area, impact, "
            "food_safety, problem, reported_by, created_at, called_at, called_by, "
            "service_note, fixed_at, fixed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (today_local(), equipment,
             area if area in EQUIPMENT_AREAS else "other",
             impact if impact in EQUIPMENT_IMPACTS else "limited",
             1 if food_safety else 0, (problem or "").strip() or None, by, stamp,
             stamp if called else None, by if called else None, note,
             stamp if fixed_now else None, by if fixed_now else None),
        )
        return cur.lastrowid


def equipment_feed(fixed_limit: int = 50) -> tuple[list[dict], list[dict], list[dict]]:
    """(to_call, waiting, fixed): to_call (nobody has called for service
    yet) and waiting (service called) in _EQUIPMENT_ORDER, each with
    days_down since it was reported (waiting also gets waiting_days since
    the call); fixed newest-first, capped, with days_down to the fix. One
    read transaction, like recovery_feed, so a flip can't show a problem
    in two lists."""
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        to_call = [dict(r) for r in conn.execute(
            "SELECT * FROM equipment_issues WHERE fixed_at IS NULL "
            "AND called_at IS NULL " + _EQUIPMENT_ORDER).fetchall()]
        waiting = [dict(r) for r in conn.execute(
            "SELECT * FROM equipment_issues WHERE fixed_at IS NULL "
            "AND called_at IS NOT NULL " + _EQUIPMENT_ORDER).fetchall()]
        fixed = [dict(r) for r in conn.execute(
            "SELECT * FROM equipment_issues WHERE fixed_at IS NOT NULL "
            "ORDER BY fixed_at DESC, id DESC LIMIT ?", (fixed_limit,)).fetchall()]
        conn.commit()
    today = date.fromisoformat(today_local())
    for r in to_call + waiting:
        r["days_down"] = _days_since(r["report_date"], today)
        r["stale"] = r["days_down"] >= 2
    for r in waiting:
        r["waiting_days"] = _days_since(business_date(r["called_at"]), today)
    for r in fixed:
        try:
            end = date.fromisoformat(business_date(r["fixed_at"]))
        except ValueError:
            end = today
        r["days_down"] = _days_since(r["report_date"], end)
    return to_call, waiting, fixed


def equipment_counts() -> tuple[int, int]:
    """(to call in, waiting on repair) for the Today tile and the ☰ badge —
    equipment_feed's buckets, so they never contradict the page."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(called_at IS NULL), 0), "
            "COALESCE(SUM(called_at IS NOT NULL), 0) "
            "FROM equipment_issues WHERE fixed_at IS NULL").fetchone()
    return row[0], row[1]


def get_equipment_issue(issue_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM equipment_issues WHERE id=?",
                           (issue_id,)).fetchone()
    return dict(row) if row else None


def set_equipment_called(issue_id: int, called: bool, note: str, by: str) -> bool:
    """Mark service called (or undo a mis-tap). Like set_recovery_contacted,
    only flips unfixed rows in the opposite state, so two leaders can't
    overwrite each other's stamp. False for an unknown id or a no-op."""
    guard = "IS NULL" if called else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE equipment_issues SET called_at=?, called_by=?, service_note=? "
            f"WHERE id=? AND fixed_at IS NULL AND called_at {guard}",
            (now_stamp() if called else None, by if called else None,
             ((note or "").strip() or None) if called else None, issue_id),
        )
        return cur.rowcount > 0


def set_equipment_fixed(issue_id: int, fixed: bool, note: str, by: str) -> bool:
    """Mark fixed, or reopen (clearing the fix stamp and note). Only flips
    rows in the opposite state. False for an unknown id or a no-op."""
    guard = "IS NULL" if fixed else "IS NOT NULL"
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE equipment_issues SET fixed_at=?, fixed_by=?, fix_note=? "
            f"WHERE id=? AND fixed_at {guard}",
            (now_stamp() if fixed else None, by if fixed else None,
             ((note or "").strip() or None) if fixed else None, issue_id),
        )
        return cur.rowcount > 0


def delete_equipment_issue(issue_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM equipment_issues WHERE id=?", (issue_id,))


def equipment_repeats(days: int = 90, limit: int = 8) -> list[dict]:
    """[{'equipment', 'count'}]: the `limit` most-reported pieces of
    equipment over the last `days` business dates (today included), most
    first — names matched ignoring case (accents too: Python's casefold,
    not SQLite's ASCII-only lower()), shown as last typed. The page's
    "what keeps breaking" radar."""
    since = (date.fromisoformat(today_local()) - timedelta(days=days - 1)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute("SELECT id, equipment FROM equipment_issues "
                            "WHERE report_date>=? ORDER BY id", (since,)).fetchall()
    tally: dict[str, dict] = {}
    for r in rows:                                  # oldest first: the last spelling wins
        t = tally.setdefault(r["equipment"].casefold(), {"count": 0})
        t.update(equipment=r["equipment"], last_id=r["id"], count=t["count"] + 1)
    ranked = sorted(tally.values(), key=lambda t: (-t["count"], -t["last_id"]))
    return [{"equipment": t["equipment"], "count": t["count"]} for t in ranked[:limit]]


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def roster(include_inactive: bool = False) -> list[dict]:
    q = "SELECT * FROM team_members "
    if not include_inactive:
        q += "WHERE active=1 "
    q += "ORDER BY name COLLATE NOCASE"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_member(name: str, role: str = "team", touch_existing: bool = True) -> bool:
    """Add (or, by default, reactivate) a roster name. touch_existing=False
    leaves an existing row completely alone — the shout-out path uses it so
    praising a departed team member can't silently resurrect them onto the
    active roster or clobber their role."""
    name = " ".join(name.split())
    if not name:
        return False
    with closing(connect()) as conn, conn:
        existing = conn.execute(
            "SELECT id FROM team_members WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            if touch_existing:
                conn.execute(
                    "UPDATE team_members SET active=1, role=? WHERE id=?",
                    (role, existing["id"]),
                )
        else:
            conn.execute(
                "INSERT INTO team_members (name, role, active, created_at) VALUES (?,?,1,?)",
                (name, role, now_stamp()),
            )
    return True


def _first_word(name: str) -> str:
    words = (name or "").split()
    return words[0].casefold() if words else ""


def _slack_keys(people: list[dict]) -> list[dict]:
    people = [dict(p) for p in people]
    for p in people:
        p["_exact"] = {p["name"].casefold(), (p.get("display") or "").casefold()} - {""}
        p["_first"] = {_first_word(p["name"]), _first_word(p.get("display") or "")} - {""}
    return people


def _match_leaders(leaders: list[dict], people: list[dict]) -> dict[str, tuple]:
    """Which Slack person is which leader login, recomputed on every pull:
    {slack_id: (leader, how)}. Each leader and each person at most once.
      1. exact — the login IS the person's Slack full or display name;
      2. remembered — the account an earlier pull or login matched, if
         that person is still here and unclaimed (an exact match always
         wins over it, so a wrong guess heals once the evidence improves);
      3. first — the login's first word is the first word of exactly one
         unclaimed person (a guess: the pull summary lists new ones).
    Inactive logins match too (so reactivation finds them), but only
    active ones take a person off the team roster."""
    assigned: dict[str, tuple] = {}
    taken: set[int] = set()

    def assign(p: dict, leader: dict, how: str) -> None:
        assigned[p["slack_id"]] = (leader, how)
        taken.add(leader["id"])

    for leader in leaders:
        lname = leader["name"].casefold()
        cands = [p for p in people if p["slack_id"] not in assigned and lname in p["_exact"]]
        if len(cands) == 1:
            assign(cands[0], leader, "exact")
    present = {p["slack_id"]: p for p in people}
    for leader in leaders:
        p = present.get(leader.get("slack_id") or "")
        if leader["id"] not in taken and p and p["slack_id"] not in assigned:
            assign(p, leader, "remembered")
    by_first: dict[str, list[dict]] = {}
    for leader in leaders:
        if leader["id"] not in taken:
            by_first.setdefault(_first_word(leader["name"]), []).append(leader)
    for first, group in by_first.items():
        if not first or len(group) != 1:
            continue
        cands = [p for p in people if p["slack_id"] not in assigned and first in p["_first"]]
        if len(cands) == 1:
            assign(cands[0], group[0], "first")
    return assigned


def _store_leader_links(conn, assigned: dict[str, tuple]) -> list[str]:
    """Write each match onto leaders.slack_id (unique: a Slack account
    belongs to one login). Returns "Login → Slack name" for new or changed
    links made by a first-name guess, for the summary."""
    guesses = []
    for slack_id, (leader, how) in assigned.items():
        if leader.get("slack_id") == slack_id:
            continue
        conn.execute("UPDATE leaders SET slack_id=NULL WHERE slack_id=? AND id<>?",
                     (slack_id, leader["id"]))
        conn.execute("UPDATE leaders SET slack_id=? WHERE id=?", (slack_id, leader["id"]))
        leader["slack_id"] = slack_id
        if how == "first":
            guesses.append(leader["name"])
    return guesses


def sync_roster_from_slack(people: list[dict], record_sync: bool = True) -> dict:
    """Merge Slack #general members into the roster. `people` is
    [{'slack_id', 'name', 'display'}] — real, active, full workspace members
    with cleaned names (shift_app.fetch_slack_roster does the filtering).

    Leaders (see _match_leaders): a person matched to an ACTIVE login is
    left off the team roster (they're under Leaders; a roster twin would
    list them twice), and any roster row already carrying their Slack name
    is linked so the picker hides it. Unmatched active logins are reported,
    and people who might be one of them are added but flagged ambiguous.

    Roster rows are linked rather than duplicated: by Slack id; by exact
    name (a removed row only on a multi-word name — a removed one-word
    "Sam" may be someone else); or a one-word ACTIVE row equal to a first
    name that only one member has. Never linked: a row named like an
    active login that isn't this person (that's the leader's own lineup
    entry). Never re-adds someone the Operator removed, never removes
    anyone, never renames anyone (lineup names stay as typed).

    One write transaction, so a manual pull and the daily refresh can't
    race each other into the name UNIQUE constraint."""
    out: dict[str, list[str]] = {k: [] for k in (
        "added", "linked", "already", "removed_kept", "leaders", "ambiguous",
        "duplicates", "leader_named", "leader_guesses", "unmatched_leaders")}
    stamp = now_stamp()
    people = _slack_keys(people)
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            leaders_all = [dict(r) for r in conn.execute(
                "SELECT id, name, active, slack_id FROM leaders "
                "ORDER BY active DESC, name COLLATE NOCASE")]
            assigned = _match_leaders(leaders_all, people)
            guesses = _store_leader_links(conn, assigned)
            matched_ids = {leader["id"] for leader, _ in assigned.values()}
            unmatched = [l for l in leaders_all if l["active"] and l["id"] not in matched_ids]
            out["unmatched_leaders"] = [l["name"] for l in unmatched]
            open_keys = ({l["name"].casefold() for l in unmatched}
                         | {_first_word(l["name"]) for l in unmatched})

            rows = [dict(r) for r in conn.execute(
                "SELECT id, name, active, slack_id FROM team_members")]
            by_slack = {r["slack_id"]: r for r in rows if r["slack_id"]}
            by_name = {r["name"].casefold(): r for r in rows}

            def set_row_slack(row: dict, slack_id: str) -> None:
                conn.execute("UPDATE team_members SET slack_id=? WHERE id=?",
                             (slack_id, row["id"]))
                row["slack_id"] = slack_id
                by_slack[slack_id] = row

            team = []
            for p in people:
                match = assigned.get(p["slack_id"])
                if match and match[0]["active"]:
                    leader = match[0]
                    label = p["name"]
                    if leader["name"] in guesses:
                        out["leader_guesses"].append(f"{leader['name']} → {p['name']}")
                    out["leaders"].append(label)
                    # A roster row under this leader's Slack name is the
                    # leader: link it so the picker hides it.
                    if p["slack_id"] not in by_slack:
                        for key in sorted(p["_exact"]):
                            row = by_name.get(key)
                            if row and not row["slack_id"]:
                                set_row_slack(row, p["slack_id"])
                                break
                    continue
                p["_ambiguous"] = bool((p["_exact"] | p["_first"]) & open_keys)
                if p["_ambiguous"]:
                    out["ambiguous"].append(p["name"])
                team.append(p)

            def owner_login(row: dict, p: dict) -> bool:
                """The row is named like an active login that isn't p."""
                key = row["name"].casefold()
                for leader in leaders_all:
                    if leader["active"] and leader["name"].casefold() == key:
                        match = assigned.get(p["slack_id"])
                        return not (match and match[0]["id"] == leader["id"])
                return False

            first_counts: dict[str, int] = {}
            for p in team:
                f = _first_word(p["name"])
                first_counts[f] = first_counts.get(f, 0) + 1

            def link(row: dict, p: dict) -> None:
                set_row_slack(row, p["slack_id"])
                if not row["active"]:
                    out["removed_kept"].append(row["name"])
                elif row["name"].casefold() == p["name"].casefold():
                    out["linked"].append(row["name"])
                else:
                    out["linked"].append(f"{row['name']} → {p['name']}")

            for p in team:
                row = by_slack.get(p["slack_id"])
                if row:
                    out["already" if row["active"] else "removed_kept"].append(row["name"])
                    continue
                row = by_name.get(p["name"].casefold())
                if row:
                    if row["slack_id"]:
                        out["duplicates"].append(p["name"])
                    elif owner_login(row, p):
                        out["leader_named"].append(p["name"])
                    elif not row["active"] and len(row["name"].split()) < 2:
                        out["removed_kept"].append(row["name"])   # maybe someone else: no link
                    else:
                        link(row, p)
                    continue
                first = _first_word(p["name"])
                row = by_name.get(first)
                if (row and row["active"] and not row["slack_id"]
                        and len(row["name"].split()) == 1
                        and first_counts.get(first) == 1 and not p["_ambiguous"]
                        and not owner_login(row, p)):
                    link(row, p)
                    continue
                cur = conn.execute(
                    "INSERT INTO team_members (name, role, active, created_at, slack_id) "
                    "VALUES (?, 'team', 1, ?, ?)", (p["name"], stamp, p["slack_id"]))
                row = {"id": cur.lastrowid, "name": p["name"], "active": 1,
                       "slack_id": p["slack_id"]}
                by_slack[p["slack_id"]] = by_name[p["name"].casefold()] = row
                out["added"].append(p["name"])
            if record_sync:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                             "VALUES ('slack_roster_synced_at', ?)", (stamp,))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return out


def relink_leaders_from_roster() -> list[str]:
    """Match leader logins that have no Slack account yet against the
    Slack-linked roster rows already in the database — no Slack call. Run
    when a login is added or reactivated, so promoting a team member who
    came in from Slack (or the boot seed) lists them once right away
    instead of waiting for a live pull. Display names aren't stored, so
    this uses the exact-name and unique-first-name rules only. Returns
    "Login → roster name" for each new link."""
    made = []
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            leaders = [dict(r) for r in conn.execute(
                "SELECT id, name, active, slack_id FROM leaders "
                "WHERE slack_id IS NULL ORDER BY active DESC, name COLLATE NOCASE")]
            held = {r[0] for r in conn.execute(
                "SELECT slack_id FROM leaders WHERE slack_id IS NOT NULL")}
            people = _slack_keys([
                {"slack_id": r["slack_id"], "name": r["name"], "display": ""}
                for r in conn.execute(
                    "SELECT name, slack_id FROM team_members "
                    "WHERE active=1 AND slack_id IS NOT NULL")
                if r["slack_id"] not in held])
            assigned = _match_leaders(leaders, people)
            for slack_id, (leader, _) in assigned.items():
                conn.execute("UPDATE leaders SET slack_id=? WHERE id=?",
                             (slack_id, leader["id"]))
                name = next(p["name"] for p in people if p["slack_id"] == slack_id)
                made.append(f"{leader['name']} → {name}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return made


def apply_roster_snapshot(people: list[dict]) -> dict | None:
    """Seed the roster ONCE from the pinned Slack #general snapshot
    (shift_roster_seed.py), so the 1:1 picker lists the whole team on the
    first boot after upgrade — before the bot can pull live. Same merge
    rules as a live pull. Skipped once applied, or once a live pull has
    run. Doesn't count as a live pull (no 'updated' stamp, no daily
    auto-refresh). Returns the summary, or None when skipped."""
    with closing(connect()) as conn:
        done = conn.execute(
            "SELECT 1 FROM meta WHERE key IN "
            "('roster_snapshot_applied', 'slack_roster_synced_at')"
        ).fetchone()
    if done:
        return None
    out = sync_roster_from_slack([dict(p) for p in people], record_sync=False)
    with closing(connect()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES "
                     "('roster_snapshot_applied', ?)", (now_stamp(),))
    return out


def slack_roster_synced_at() -> str | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='slack_roster_synced_at'"
        ).fetchone()
    return row["value"] if row else None


def claim_slack_roster_autosync(min_hours: float = 20) -> bool:
    """True (and the slot is taken) when the daily background refresh is
    due: a manual pull has succeeded at least once, and no refresh was
    attempted within `min_hours`. Recording the ATTEMPT, not the success,
    means a broken token retries once a day instead of on every page view."""
    now = datetime.strptime(now_stamp(), "%Y-%m-%d %H:%M")
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            vals = {r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM meta WHERE key IN "
                "('slack_roster_synced_at', 'slack_roster_attempted_at')")}
            if "slack_roster_synced_at" not in vals:
                conn.rollback()
                return False
            last = max(vals.values())
            try:
                due = now - datetime.strptime(last, "%Y-%m-%d %H:%M") \
                    >= timedelta(hours=min_hours)
            except ValueError:
                due = True
            if due:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES "
                             "('slack_roster_attempted_at', ?)", (now_stamp(),))
            conn.commit()
            return due
        except BaseException:
            conn.rollback()
            raise


def set_member_active(member_id: int, active: bool) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE team_members SET active=? WHERE id=?", (1 if active else 0, member_id)
        )


# ---------------------------------------------------------------------------
# Shift notes
# ---------------------------------------------------------------------------

def add_note(note_date: str, daypart: str, category: str, body: str, author: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO shift_notes (note_date, daypart, category, body, author, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (note_date, daypart or None, category, body, author, now_stamp()),
        )


def notes_feed(limit: int = 50, category: str | None = None) -> list[dict]:
    q = "SELECT * FROM shift_notes "
    params: list = []
    if category:
        q += "WHERE category=? "
        params.append(category)
    q += "ORDER BY note_date DESC, id DESC LIMIT ?"
    params.append(limit)
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def notes_for_date(note_date: str) -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM shift_notes WHERE note_date=? ORDER BY id DESC", (note_date,)
        ).fetchall()]


def get_note(note_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM shift_notes WHERE id=?", (note_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_note(note_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM shift_notes WHERE id=?", (note_id,))


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

def active_announcements(today: str | None = None, reader: str | None = None) -> list[dict]:
    """Active announcements; with reader set, each row gets acked/ack_count."""
    today = today or today_local()
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM announcements WHERE expires_on IS NULL OR expires_on >= ? "
            "ORDER BY pinned DESC, id DESC",
            (today,),
        ).fetchall()]
        for a in rows:
            a["ack_count"] = conn.execute(
                "SELECT COUNT(*) AS c FROM announcement_reads WHERE announcement_id=?",
                (a["id"],),
            ).fetchone()["c"]
            if reader is not None:
                a["acked"] = bool(conn.execute(
                    "SELECT 1 FROM announcement_reads WHERE announcement_id=? AND reader=?",
                    (a["id"], reader),
                ).fetchone())
    return rows


def ack_announcement(announcement_id: int, reader: str) -> bool:
    """Record a 'Got it'. Returns False if the announcement no longer exists
    (OR IGNORE does not suppress foreign-key violations, so check first)."""
    with closing(connect()) as conn, conn:
        if not conn.execute(
            "SELECT 1 FROM announcements WHERE id=?", (announcement_id,)
        ).fetchone():
            return False
        conn.execute(
            "INSERT OR IGNORE INTO announcement_reads (announcement_id, reader, read_at) "
            "VALUES (?,?,?)",
            (announcement_id, reader, now_stamp()),
        )
    return True


def announcement_readers(announcement_id: int) -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT reader, read_at FROM announcement_reads WHERE announcement_id=? "
            "ORDER BY read_at",
            (announcement_id,),
        ).fetchall()]


def unacked_count(reader: str, today: str | None = None) -> int:
    today = today or today_local()
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM announcements a "
            "WHERE (a.expires_on IS NULL OR a.expires_on >= ?) "
            "AND NOT EXISTS (SELECT 1 FROM announcement_reads r "
            "                WHERE r.announcement_id=a.id AND r.reader=?)",
            (today, reader),
        ).fetchone()["c"]


def add_announcement(body: str, author: str, pinned: bool, expires_on: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO announcements (body, author, pinned, expires_on, created_at) "
            "VALUES (?,?,?,?,?)",
            (body, author, 1 if pinned else 0, expires_on or None, now_stamp()),
        )


def delete_announcement(announcement_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (announcement_id,))


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def recent_dates(days: int = 7) -> list[str]:
    """Today and the previous days-1 store-local dates, newest first."""
    base = date.fromisoformat(today_local())
    return [(base - timedelta(days=i)).isoformat() for i in range(days)]


# ---------------------------------------------------------------------------
# Backup: JSON export / import
# ---------------------------------------------------------------------------

# Everything except leaders' pin_hash values (those never leave the DB) and
# meta (regenerated locally).
EXPORT_TABLES = [
    "team_members", "checklist_templates", "checklist_template_items",
    "checklist_schedule_log", "checklist_runs", "checklist_run_items", "goals", "goal_updates",
    "positions", "lineup_assignments", "shift_notes", "announcements",
    "announcement_reads", "course_lessons", "lesson_progress", "leader_tasks",
    "guest_recoveries", "oneonone_topics", "oneonone_member_topics", "shoutouts",
    "hub_categories", "hub_pages", "hub_page_versions", "hub_attachments", "hub_acks",
    "hub_reviews", "hub_audit", "daily_goals", "equipment_issues",
]
# A backup from before the Team Hub existed leaves the Hub as it is.
HUB_TABLES = [t for t in EXPORT_TABLES if t.startswith("hub_")]


def session_epoch() -> str:
    """Changes on every backup restore; leader sessions minted under an
    older epoch are invalidated (a restore can renumber leader ids, and a
    stale 30-day cookie must never re-attach to a different leader)."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='session_epoch'"
        ).fetchone()
    return row["value"] if row else ""


def export_json() -> str:
    payload: dict = {"exported_at": now_stamp(), "format": 1}
    with closing(connect()) as conn:
        since = conn.execute(
            "SELECT value FROM meta WHERE key='manager_check_since'").fetchone()
        seeded = [key for key, _ in shift_cleaning.BATCHES if conn.execute(
            "SELECT 1 FROM meta WHERE key=?", (key,)).fetchone()]
        drains = conn.execute(
            "SELECT value FROM meta WHERE key='closing_drains_added'").fetchone()
        payload["meta"] = {"manager_check_since": since["value"] if since else None,
                           "cleaning_batches": ",".join(seeded),
                           "closing_drains_added": drains["value"] if drains else None,
                           "list_additions": ",".join(
                               key for key, *_ in LIST_ADDITIONS if conn.execute(
                                   "SELECT 1 FROM meta WHERE key=?", (key,)).fetchone())}
        # SELECTs run in autocommit by default; an explicit transaction makes
        # the whole export one consistent snapshot (WAL gives repeatable
        # reads), so a write landing mid-export can't orphan child rows.
        conn.execute("BEGIN")
        for table in EXPORT_TABLES:
            payload[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        payload["leaders"] = [dict(r) for r in conn.execute(
            "SELECT id, name, role, active, created_at, slack_id, area, checklist_side "
            "FROM leaders")]
        conn.commit()
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _rekey_hub_acks(conn) -> None:
    """Read confirmations kept across a restore of an older backup point at
    roster/leader ids the backup may have reassigned: re-point each at the
    person of the same name, or retire its key so it can never attach to
    whoever gets that id next. Two passes (via temporary keys) so swapped
    ids can't collide with a row that hasn't moved yet."""
    people: dict[str, dict[str, list[int]]] = {}
    for kind, table in (("leader", "leaders"), ("member", "team_members")):
        for r in conn.execute(f"SELECT id, name FROM {table}"):
            people.setdefault(r["name"].casefold(), {}).setdefault(kind, []).append(r["id"])
    moves = []
    for r in conn.execute("SELECT id, reader_key, reader_name FROM hub_acks "
                          "WHERE reader_key LIKE 'member:%' OR reader_key LIKE 'leader:%'"
                          ).fetchall():
        kind, _, old_id = r["reader_key"].partition(":")
        ids = people.get(r["reader_name"].casefold(), {}).get(kind, [])
        if old_id.isdigit() and int(old_id) in ids:
            continue
        target = f"{kind}:{ids[0]}" if len(ids) == 1 else f"former:{r['reader_key']}"
        moves.append((r["id"], r["reader_key"], target))
    for ack_id, _, _ in moves:
        conn.execute("UPDATE hub_acks SET reader_key=? WHERE id=?", (f"tmp:{ack_id}", ack_id))
    for ack_id, old_key, target in moves:
        for key in (target, f"former:{old_key}", f"former:{old_key}:{ack_id}"):
            if conn.execute("UPDATE OR IGNORE hub_acks SET reader_key=? WHERE id=?",
                            (key, ack_id)).rowcount:
                break


class _RestoreError(Exception):
    """Raised inside the restore transaction so `with conn:` rolls back."""


def import_json(raw: str) -> str | None:
    """Restore an export_json() payload, REPLACING current content of the
    exported tables. Leader accounts are restored with LOCKED PINs (hashes
    never leave the database) — the Operator resets each PIN afterwards.
    Returns an error message or None.

    All-or-nothing: the payload is fully validated before anything is
    deleted, and the whole restore runs in one transaction that rolls back
    on any failure — a rejected backup never touches existing data.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        return "That file is not valid JSON."
    if not isinstance(payload, dict) or payload.get("format") != 1:
        return "That file is not a CFA Sidekick shift-app backup."

    # Validate shape completely before any destructive statement runs.
    meta = payload.get("meta", {})
    if not isinstance(meta, dict) or not all(
            isinstance(v, (str, type(None))) for v in meta.values()):
        return "Backup section 'meta' is malformed."
    for table in EXPORT_TABLES + ["leaders"]:
        rows = payload.get(table, [])
        if not isinstance(rows, list):
            return f"Backup section '{table}' is malformed."
        for row in rows:
            if not isinstance(row, dict) or not row:
                return f"Backup section '{table}' has a malformed row."
            if not all(isinstance(v, (str, int, float, type(None)))
                       for v in row.values()):
                return f"Backup section '{table}' has a malformed row."
    # Hub file rows name files on disk: only names the Hub itself makes.
    for row in payload.get("hub_attachments", []):
        if (not re.fullmatch(r"[0-9a-f]{32}", str(row.get("stored_name", "")))
                or row.get("kind") not in HUB_FILE_KINDS):
            return "Backup section 'hub_attachments' has a malformed row."
    for row in payload.get("hub_pages", []):
        if row.get("status", "draft") not in HUB_PAGE_STATUSES:
            return "Backup section 'hub_pages' has a malformed row."

    # Restored leader accounts get an unmatchable PIN hash: the Operator
    # resets PINs after a restore. Keeping the leaders' original ids is what
    # lets lesson_progress rows re-attach on a fresh database.
    locked_hash = generate_password_hash("locked-" + secrets.token_hex(16))

    try:
        with closing(connect()) as conn:
            try:
                with conn:  # one transaction; any exception rolls it back
                    # Hub sign-in grants never leave the database; deleting
                    # the roster cascades them away, so re-attach each by name.
                    access = conn.execute(
                        "SELECT m.name, a.code_hash, a.granted_at, a.granted_by "
                        "FROM hub_access a JOIN team_members m ON m.id = a.member_id"
                    ).fetchall()
                    hub_kept = "hub_acks" not in payload
                    conn.execute("DELETE FROM leaders")
                    for row in payload.get("leaders", []):
                        conn.execute(
                            "INSERT INTO leaders (id, name, pin_hash, role, active, "
                            "created_at, slack_id, area, checklist_side) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (row.get("id"), row.get("name", ""), locked_hash,
                             row.get("role", "lead"), row.get("active", 1),
                             row.get("created_at", now_stamp()),
                             row.get("slack_id"), row.get("area"),
                             row.get("checklist_side")
                             if row.get("checklist_side") in CHECKLIST_VIEWS else "all"),
                        )
                    for table in EXPORT_TABLES:
                        if table in HUB_TABLES and table not in payload:
                            continue
                        valid_cols = {r["name"] for r in
                                      conn.execute(f"PRAGMA table_info({table})")}
                        conn.execute(f"DELETE FROM {table}")
                        for row in payload.get(table, []):
                            cols = [c for c in row if c in valid_cols]
                            if not cols:
                                raise _RestoreError(
                                    f"Backup section '{table}' has a malformed row.")
                            conn.execute(
                                f"INSERT INTO {table} ({','.join(cols)}) "
                                f"VALUES ({','.join('?' * len(cols))})",
                                [row[c] for c in cols],
                            )
                    # Lists from a backup made before sides (or with a side
                    # this version doesn't know) get theirs from their area.
                    _backfill_sides(conn, [
                        row.get("id") for row in payload.get("checklist_templates", [])
                        if row.get("side") not in CHECKLIST_SIDES])
                    for row in access:
                        # A fresh nonce: everyone signs in again after a restore.
                        conn.execute(
                            "INSERT OR IGNORE INTO hub_access (member_id, code_hash, nonce, "
                            "granted_at, granted_by) SELECT id, ?, ?, ?, ? FROM team_members "
                            "WHERE name=? COLLATE NOCASE",
                            (row["code_hash"], secrets.token_hex(16), row["granted_at"],
                             row["granted_by"], row["name"]))
                    if hub_kept:
                        _rekey_hub_acks(conn)
                    # Keep the course-seed flag in sync with what the backup
                    # actually restored: a pre-course backup (no lessons)
                    # clears it so the next boot re-seeds from code, and a
                    # backup WITH lessons sets it so a later boot never
                    # seeds duplicates on top of the restored course.
                    if conn.execute(
                        "SELECT 1 FROM course_lessons LIMIT 1"
                    ).fetchone():
                        conn.execute(
                            "INSERT OR REPLACE INTO meta (key, value) "
                            "VALUES ('course_seeded', ?)", (now_stamp(),))
                    else:
                        conn.execute("DELETE FROM meta WHERE key='course_seeded'")
                    # A backup from before a batch of station cleaning
                    # sheets would wipe that batch for good: seed it back
                    # now, skipping any name the backup restored. Newer
                    # backups list their batches; older ones predate all but
                    # the first, which they hold if their templates carry a
                    # frequency. An empty backup stays empty.
                    templates = payload.get("checklist_templates", [])
                    listed = meta.get("cleaning_batches")
                    for key, _ in shift_cleaning.BATCHES:
                        if listed is not None:
                            held = key in listed.split(",")
                        elif not templates:
                            held = True
                        elif key == shift_cleaning.BATCHES[0][0]:
                            held = any("frequency" in row for row in templates)
                        else:
                            held = False
                        if held:
                            conn.execute("INSERT OR IGNORE INTO meta (key, value) "
                                         "VALUES (?, ?)", (key, now_stamp()))
                        else:
                            conn.execute("DELETE FROM meta WHERE key=?", (key,))
                    _seed_cleaning(conn)
                    # Same for the nightly drain items on the closing lists.
                    if ("closing_drains_added" in meta
                            and meta["closing_drains_added"]) or (
                            "closing_drains_added" not in meta and not templates):
                        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES "
                                     "('closing_drains_added', ?)", (now_stamp(),))
                    else:
                        conn.execute("DELETE FROM meta WHERE key='closing_drains_added'")
                        _seed_closing_drains(conn)
                    # And the later one-line additions (LIST_ADDITIONS).
                    listed = meta.get("list_additions")
                    for key, *_ in LIST_ADDITIONS:
                        held = (key in listed.split(",") if listed is not None
                                else not templates)
                        if held:
                            conn.execute("INSERT OR IGNORE INTO meta (key, value) "
                                         "VALUES (?, ?)", (key, now_stamp()))
                        else:
                            conn.execute("DELETE FROM meta WHERE key=?", (key,))
                    _seed_list_additions(conn)
                    # Lists restored without schedule history get a baseline,
                    # and runs from before the Manager check existed don't
                    # count against it: the backup says since when (older
                    # backups predate the button entirely).
                    _seed_schedule_log(conn)
                    runs = payload.get("checklist_runs", [])
                    if "manager_check_since" in meta:
                        since = meta["manager_check_since"]
                    elif runs and not any("verified_at" in row for row in runs):
                        since = today_local()
                    else:
                        since = None
                    conn.execute("DELETE FROM meta WHERE key='manager_check_since'")
                    if since:
                        conn.execute(
                            "INSERT INTO meta (key, value) "
                            "VALUES ('manager_check_since', ?)", (since,))
                    # A restore may renumber leader ids; bump the session
                    # epoch so every leader's 30-day cookie must re-login
                    # instead of silently attaching to a different leader.
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) "
                        "VALUES ('session_epoch', ?)", (secrets.token_hex(8),))
            except _RestoreError as e:
                return str(e)
    except sqlite3.Error as e:
        return f"Backup could not be restored (nothing was changed): {e}"
    return None
