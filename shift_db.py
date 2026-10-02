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
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from werkzeug.security import check_password_hash, generate_password_hash

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
}

NOTE_CATEGORIES = ["general", "win", "issue", "equipment", "staffing", "guest", "food-safety"]

GOAL_PERIODS = ["daily", "weekly", "monthly", "one-time"]

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
-- entry, and lineups keep working.

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
    daypart TEXT NOT NULL,                      -- one of DAYPARTS
    area TEXT NOT NULL DEFAULT 'All',
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checklist_template_items (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    critical INTEGER NOT NULL DEFAULT 0,        -- food-safety/cash items, flagged red
    sort INTEGER NOT NULL DEFAULT 0
);

-- One run of a template on one date. Items are snapshotted from the template
-- at creation so later template edits never rewrite past days' history.
CREATE TABLE IF NOT EXISTS checklist_runs (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE CASCADE,
    run_date TEXT NOT NULL,                     -- YYYY-MM-DD store-local
    UNIQUE (template_id, run_date)
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
    period TEXT NOT NULL DEFAULT 'weekly',      -- one of GOAL_PERIODS
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
"""


# ---------------------------------------------------------------------------
# Seed content — Chick-fil-A Wharncliffe & Wonderland defaults. Inserted once
# (guarded by meta.seeded); everything is editable in the app afterwards.
# Items are (label, critical) — critical marks food-safety/cash tasks that the
# UI flags red until done.
# ---------------------------------------------------------------------------

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
        ("Doors locked, lights + signage off, alarm checklist", True),
    ]),
    ("BOH Closing", "dinner", "Kitchen", [
        ("All fryers filtered and boiled out per schedule", False),
        ("Breading table broken down, flour sifted and stored", False),
        ("Grills scraped, cleaned and re-seasoned", False),
        ("All product dated, wrapped, rotated (FIFO) into walk-in", True),
        ("Prep surfaces + boards sanitized", True),
        ("Floors swept, mopped, drains cleaned", False),
        ("Closing temp checks logged", True),
        ("Waste log completed and verified", True),
        ("Sanitizer buckets emptied, towels to laundry", False),
    ]),
]

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
            _seed_course(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


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


def _seed_base(conn) -> None:
    seeded = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
    if seeded:
        return
    stamp = now_stamp()
    for sort, (name, daypart, area, items) in enumerate(SEED_TEMPLATES):
        cur = conn.execute(
            "INSERT INTO checklist_templates (name, daypart, area, sort) VALUES (?,?,?,?)",
            (name, daypart, area, sort),
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


# ---------------------------------------------------------------------------
# Checklists
# ---------------------------------------------------------------------------

def ensure_runs_for_date(run_date: str) -> None:
    """Instantiate a run (with snapshotted items) for every active template
    that doesn't have one on run_date yet."""
    with closing(connect()) as conn, conn:
        templates = conn.execute(
            "SELECT id FROM checklist_templates WHERE active=1"
        ).fetchall()
        for t in templates:
            # INSERT OR IGNORE + the UNIQUE(template_id, run_date) constraint
            # makes concurrent first-loads race-safe.
            cur = conn.execute(
                "INSERT OR IGNORE INTO checklist_runs (template_id, run_date) VALUES (?,?)",
                (t["id"], run_date),
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


def runs_for_date(run_date: str) -> list[dict]:
    """Runs for a date with progress counts, ordered by daypart then sort."""
    daypart_order = {d: i for i, d in enumerate(DAYPARTS)}
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.run_date, t.name, t.daypart, t.area, t.sort,
                   COUNT(i.id) AS total,
                   COALESCE(SUM(i.done), 0) AS done,
                   COALESCE(SUM(CASE WHEN i.critical=1 AND i.done=0 THEN 1 ELSE 0 END), 0)
                       AS critical_remaining
            FROM checklist_runs r
            JOIN checklist_templates t ON t.id = r.template_id
            LEFT JOIN checklist_run_items i ON i.run_id = r.id
            WHERE r.run_date = ?
            GROUP BY r.id
            """,
            (run_date,),
        ).fetchall()
    runs = [dict(r) for r in rows]
    runs.sort(key=lambda r: (daypart_order.get(r["daypart"], 99), r["sort"], r["id"]))
    return runs


def run_with_items(run_id: int) -> tuple[dict | None, list[dict]]:
    with closing(connect()) as conn:
        run = conn.execute(
            """
            SELECT r.id, r.run_date, t.name, t.daypart, t.area
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
    return dict(run), [dict(i) for i in items]


def set_item_done(item_id: int, done: bool, by: str) -> bool:
    """Check or uncheck one run item. Returns False if the item is unknown."""
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE checklist_run_items SET done=?, done_by=?, done_at=? WHERE id=?",
            (1 if done else 0, by if done else None, now_stamp() if done else None, item_id),
        )
        return cur.rowcount > 0


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


def create_template(name: str, daypart: str, area: str,
                    items: list[tuple[str, bool]]) -> int:
    """items are (label, critical) pairs."""
    with closing(connect()) as conn, conn:
        sort = conn.execute(
            "SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM checklist_templates"
        ).fetchone()["s"]
        cur = conn.execute(
            "INSERT INTO checklist_templates (name, daypart, area, sort) VALUES (?,?,?,?)",
            (name, daypart, area, sort),
        )
        conn.executemany(
            "INSERT INTO checklist_template_items (template_id, label, critical, sort) "
            "VALUES (?,?,?,?)",
            [(cur.lastrowid, label, 1 if critical else 0, i)
             for i, (label, critical) in enumerate(items)],
        )
        return cur.lastrowid


def update_template(template_id: int, name: str, daypart: str, area: str,
                    items: list[tuple[str, bool]]) -> None:
    """Replace a template's fields and item list ((label, critical) pairs).
    Existing runs keep their snapshotted items; only future runs pick up the
    change."""
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE checklist_templates SET name=?, daypart=?, area=? WHERE id=?",
            (name, daypart, area, template_id),
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


def set_template_active(template_id: int, active: bool) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE checklist_templates SET active=? WHERE id=?",
            (1 if active else 0, template_id),
        )


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def goals_by_status(status: str = "active", leader_id: int | None = None,
                    store_only: bool = False) -> list[dict]:
    """Goals with latest value + progress. leader_id filters to one
    leader's personal goals (their 1:1 page); store_only keeps the Today
    dashboard to store-wide goals. Defaults return everything."""
    q = ("SELECT g.*, ld.name AS leader_name FROM goals g "
         "LEFT JOIN leaders ld ON ld.id = g.leader_id WHERE g.status=?")
    params: list = [status]
    if leader_id is not None:
        q += " AND g.leader_id=?"
        params.append(leader_id)
    elif store_only:
        q += " AND g.leader_id IS NULL"
    q += " ORDER BY g.created_at DESC, g.id DESC"
    with closing(connect()) as conn:
        goals = [dict(g) for g in conn.execute(q, params).fetchall()]
        for g in goals:
            latest = conn.execute(
                "SELECT value, recorded_at FROM goal_updates WHERE goal_id=? "
                "AND value IS NOT NULL ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (g["id"],),
            ).fetchone()
            g["latest_value"] = latest["value"] if latest else None
            g["latest_at"] = latest["recorded_at"] if latest else None
            g["progress_pct"] = goal_progress_pct(g)
    return goals


def goal_progress_pct(g: dict) -> int | None:
    """0-100 progress for goals with a numeric target, else None."""
    target = g.get("target_value")
    value = g.get("latest_value")
    if target in (None, 0) or value is None:
        return None
    if g.get("direction") == "down":
        # Lower is better (e.g. DT times): hitting or beating target = 100.
        if value <= 0:
            return 100
        pct = target / value * 100
    else:
        pct = value / target * 100
    return max(0, min(100, round(pct)))


def goal_with_updates(goal_id: int) -> tuple[dict | None, list[dict]]:
    with closing(connect()) as conn:
        g = conn.execute(
            "SELECT g.*, ld.name AS leader_name FROM goals g "
            "LEFT JOIN leaders ld ON ld.id = g.leader_id WHERE g.id=?",
            (goal_id,)).fetchone()
        if not g:
            return None, []
        updates = conn.execute(
            "SELECT * FROM goal_updates WHERE goal_id=? ORDER BY recorded_at DESC, id DESC",
            (goal_id,),
        ).fetchall()
    g = dict(g)
    latest = next((u for u in updates if u["value"] is not None), None)
    g["latest_value"] = latest["value"] if latest else None
    g["progress_pct"] = goal_progress_pct(g)
    return g, [dict(u) for u in updates]


def create_goal(title: str, why: str, metric: str, unit: str, target_value: float | None,
                direction: str, period: str, due_date: str, created_by: str,
                leader_id: int | None = None) -> int:
    with closing(connect()) as conn, conn:
        # Re-validate the optional leader tag so a stale form select can't
        # violate the FK — unknown leader means a store-wide goal.
        if leader_id is not None and not conn.execute(
                "SELECT 1 FROM leaders WHERE id=?", (leader_id,)).fetchone():
            leader_id = None
        cur = conn.execute(
            "INSERT INTO goals (title, why, metric, unit, target_value, direction, "
            "period, due_date, status, created_by, created_at, leader_id) "
            "VALUES (?,?,?,?,?,?,?,?,'active',?,?,?)",
            (title, why, metric, unit, target_value, direction, period,
             due_date or None, created_by, now_stamp(), leader_id),
        )
        return cur.lastrowid


def add_goal_update(goal_id: int, value: float | None, note: str, by: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO goal_updates (goal_id, value, note, recorded_by, recorded_at) "
            "VALUES (?,?,?,?,?)",
            (goal_id, value, note, by, now_stamp()),
        )


def set_goal_status(goal_id: int, status: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE goals SET status=? WHERE id=?", (status, goal_id))


# ---------------------------------------------------------------------------
# Lineup / setup sheet
# ---------------------------------------------------------------------------

def active_positions() -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE active=1 ORDER BY sort, id"
        ).fetchall()]


def lineup_for(lineup_date: str, daypart: str) -> dict[int, list[dict]]:
    """position_id -> assignment rows for one date+daypart."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM lineup_assignments WHERE lineup_date=? AND daypart=? "
            "ORDER BY id",
            (lineup_date, daypart),
        ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["position_id"], []).append(dict(r))
    return out


def assign_position(lineup_date: str, daypart: str, position_id: int,
                    member_name: str) -> bool:
    """Assign a name to a position. Returns False for an unknown position
    (stale form, tampered id) instead of raising. Duplicate (position, name)
    pairs are absorbed by the uq_lineup_slot unique index."""
    with closing(connect()) as conn, conn:
        if not conn.execute(
            "SELECT 1 FROM positions WHERE id=?", (position_id,)
        ).fetchone():
            return False
        conn.execute(
            "INSERT OR IGNORE INTO lineup_assignments "
            "(lineup_date, daypart, position_id, member_name) VALUES (?,?,?,?)",
            (lineup_date, daypart, position_id, member_name),
        )
    return True


def unassign(assignment_id: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM lineup_assignments WHERE id=?", (assignment_id,))


def copy_lineup(from_date: str, from_daypart: str, to_date: str, to_daypart: str) -> int:
    """Copy a lineup between (date, daypart) slots — 'copy from yesterday' and
    'copy from the previous daypart' both come through here. Duplicates are
    absorbed by the uq_lineup_slot unique index. Returns copied count."""
    copied = 0
    with closing(connect()) as conn, conn:
        src = conn.execute(
            "SELECT position_id, member_name FROM lineup_assignments "
            "WHERE lineup_date=? AND daypart=? ORDER BY id",
            (from_date, from_daypart),
        ).fetchall()
        for row in src:
            cur = conn.execute(
                "INSERT OR IGNORE INTO lineup_assignments "
                "(lineup_date, daypart, position_id, member_name) VALUES (?,?,?,?)",
                (to_date, to_daypart, row["position_id"], row["member_name"]),
            )
            copied += cur.rowcount
    return copied


def previous_lineup_date(before_date: str, daypart: str) -> str | None:
    """Most recent date before before_date that has assignments for daypart."""
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT lineup_date FROM lineup_assignments WHERE daypart=? AND lineup_date<? "
            "ORDER BY lineup_date DESC LIMIT 1",
            (daypart, before_date),
        ).fetchone()
    return row["lineup_date"] if row else None


def recent_lineup_names(days: int = 14) -> list[str]:
    """Distinct names used in recent lineups, for autosuggest."""
    since = (date.fromisoformat(today_local()) - timedelta(days=days)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT member_name FROM lineup_assignments WHERE lineup_date>=? "
            "ORDER BY member_name COLLATE NOCASE",
            (since,),
        ).fetchall()
    return [r["member_name"] for r in rows]


# ---------------------------------------------------------------------------
# Leaders (app logins)
# ---------------------------------------------------------------------------

def leaders(include_inactive: bool = False) -> list[dict]:
    q = "SELECT id, name, role, active, created_at, slack_id FROM leaders "
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
            "SELECT id, name, role, active FROM leaders WHERE id=?", (leader_id,)
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

def _business_date(stamp: str) -> str:
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
        t["carried"] = bool(last_met and _business_date(t["created_at"]) < last_met)
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
    role='leader' there, and lineup_assign resets it to 'team'."""
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
                _business_date(r["contacted_at"]))).days)
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
    "checklist_runs", "checklist_run_items", "goals", "goal_updates",
    "positions", "lineup_assignments", "shift_notes", "announcements",
    "announcement_reads", "course_lessons", "lesson_progress", "leader_tasks",
    "guest_recoveries", "oneonone_topics", "oneonone_member_topics", "shoutouts",
]


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
        # SELECTs run in autocommit by default; an explicit transaction makes
        # the whole export one consistent snapshot (WAL gives repeatable
        # reads), so a write landing mid-export can't orphan child rows.
        conn.execute("BEGIN")
        for table in EXPORT_TABLES:
            payload[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        payload["leaders"] = [dict(r) for r in conn.execute(
            "SELECT id, name, role, active, created_at, slack_id FROM leaders"
        )]
        conn.commit()
    return json.dumps(payload, ensure_ascii=False, indent=1)


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

    # Restored leader accounts get an unmatchable PIN hash: the Operator
    # resets PINs after a restore. Keeping the leaders' original ids is what
    # lets lesson_progress rows re-attach on a fresh database.
    locked_hash = generate_password_hash("locked-" + secrets.token_hex(16))

    try:
        with closing(connect()) as conn:
            try:
                with conn:  # one transaction; any exception rolls it back
                    conn.execute("DELETE FROM leaders")
                    for row in payload.get("leaders", []):
                        conn.execute(
                            "INSERT INTO leaders (id, name, pin_hash, role, active, "
                            "created_at, slack_id) VALUES (?,?,?,?,?,?,?)",
                            (row.get("id"), row.get("name", ""), locked_hash,
                             row.get("role", "lead"), row.get("active", 1),
                             row.get("created_at", now_stamp()),
                             row.get("slack_id")),
                        )
                    for table in EXPORT_TABLES:
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
