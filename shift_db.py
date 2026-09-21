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
    created_at TEXT NOT NULL
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
    created_at TEXT NOT NULL
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
    created_at TEXT NOT NULL
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
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
        _seed_base(conn)
        _seed_course(conn)


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

def goals_by_status(status: str = "active") -> list[dict]:
    with closing(connect()) as conn:
        goals = [dict(g) for g in conn.execute(
            "SELECT * FROM goals WHERE status=? ORDER BY created_at DESC, id DESC",
            (status,),
        ).fetchall()]
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
        g = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
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
                direction: str, period: str, due_date: str, created_by: str) -> int:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO goals (title, why, metric, unit, target_value, direction, "
            "period, due_date, status, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,'active',?,?)",
            (title, why, metric, unit, target_value, direction, period,
             due_date or None, created_by, now_stamp()),
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
    q = "SELECT id, name, role, active, created_at FROM leaders "
    if not include_inactive:
        q += "WHERE active=1 "
    q += "ORDER BY name COLLATE NOCASE"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_leader(name: str, pin: str, role: str = "lead") -> str | None:
    """Create (or reactivate) a leader login. Returns an error message or None."""
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
                   COUNT(p.lesson_id) AS done,
                   MAX(p.completed_at) AS last_completed
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
            "SELECT 1 FROM course_lessons WHERE id=?", (lesson_id,)
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
# Roster
# ---------------------------------------------------------------------------

def roster(include_inactive: bool = False) -> list[dict]:
    q = "SELECT * FROM team_members "
    if not include_inactive:
        q += "WHERE active=1 "
    q += "ORDER BY name COLLATE NOCASE"
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_member(name: str, role: str = "team") -> bool:
    """Add (or reactivate) a roster name. Returns False on empty name."""
    name = " ".join(name.split())
    if not name:
        return False
    with closing(connect()) as conn, conn:
        existing = conn.execute(
            "SELECT id FROM team_members WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
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
    "announcement_reads", "course_lessons", "lesson_progress",
]


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
            "SELECT id, name, role, active, created_at FROM leaders"
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
                            "created_at) VALUES (?,?,?,?,?,?)",
                            (row.get("id"), row.get("name", ""), locked_hash,
                             row.get("role", "lead"), row.get("active", 1),
                             row.get("created_at", now_stamp())),
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
                    # A pre-course backup carries no lessons: clear the seed
                    # flag so the next boot re-seeds the course from code.
                    if not conn.execute(
                        "SELECT 1 FROM course_lessons LIMIT 1"
                    ).fetchone():
                        conn.execute("DELETE FROM meta WHERE key='course_seeded'")
            except _RestoreError as e:
                return str(e)
    except sqlite3.Error as e:
        return f"Backup could not be restored (nothing was changed): {e}"
    return None
