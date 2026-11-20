"""SQLite storage for the People section (leader reviews and hiring).

This is a separate database file from the shift app's, on purpose. Records
about a named applicant or leader live here and nowhere else: nothing in
this file is part of shift_db.EXPORT_TABLES, the scheduled shift backup,
Slack, email or the logs. It has its own Operator-only backup
(export_json) and no foreign keys into the shift database, which is
another file. A person is always referred to by leader id AND sign-in name
together, so a Team Hub restore that renumbers leaders can never hand one
person's records or access to another.

Same style as shift_db.py / hub_db.py: stdlib sqlite3, one short-lived
connection per function, idempotent DDL at every boot. The feature stores
(people_reviews_db, people_hiring_db) add their own tables through
init_schema(conn).

The file lives at PEOPLE_DB_PATH, or people.db beside the shift database
(the Render persistent disk in production).
"""

import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing

import hub_text
import shift_db

# What the Operator can grant a leader. Access is never decided by role:
# an admin-role leader has neither until the Operator ticks it.
CAPABILITIES = ("hiring", "reviewer")
CAPABILITY_LABELS = {"hiring": "Hiring", "reviewer": "Leader reviews"}

FEATURE_STORES = ("people_reviews_db", "people_hiring_db")

SCHEMA = """
-- Local housekeeping: the pepper for private-link hashes. Never exported.
-- No personal content.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Switches and store details. Personal content: only the hiring contact's
-- name, work email and work phone, which are shown on public job postings.
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT);

-- Who the Operator has let into hiring or leader reviews. Personal content:
-- a leader's sign-in name and Team Hub leader id. A grant counts only while
-- both still match the person signing in.
CREATE TABLE IF NOT EXISTS access_grants (
  id INTEGER PRIMARY KEY, leader_id INTEGER NOT NULL, leader_name TEXT NOT NULL,
  capability TEXT NOT NULL, granted_at TEXT NOT NULL, granted_by TEXT NOT NULL,
  UNIQUE (leader_id, capability));

-- The activity log: who opened or changed what, and when. Personal content:
-- the sign-in name of whoever did it. `detail` holds ids and short fixed
-- labels only, never free text about a person.
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, action TEXT NOT NULL,
  object_type TEXT NOT NULL DEFAULT '', object_id INTEGER, detail TEXT NOT NULL DEFAULT '');

-- "Our standards": the store's values, standards and level descriptions,
-- loaded by the Operator. Every save is a new version. No personal content
-- beyond the sign-in name of whoever saved it.
CREATE TABLE IF NOT EXISTS standards_models (
  id INTEGER PRIMARY KEY, version INTEGER NOT NULL, name TEXT NOT NULL, levels_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

-- One row per standard in a model version. Wording about behaviour at work
-- only: no personal content.
CREATE TABLE IF NOT EXISTS standards_dimensions (
  id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL REFERENCES standards_models(id) ON DELETE CASCADE,
  sort INTEGER NOT NULL, group_name TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
  definition TEXT NOT NULL, anchors_json TEXT NOT NULL, evidence_examples TEXT NOT NULL DEFAULT '',
  hiring_question TEXT NOT NULL DEFAULT '', hiring_anchors_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (model_id, key));

-- Every AI request and reply, kept as the record of what the AI was shown
-- and what it said. Personal content: review notes and application answers
-- in the form they were sent (names, contact details and gendered pronouns
-- taken out first), the AI's reply, and the sign-in name of whoever asked.
-- system_text is the instruction text sent with the request.
CREATE TABLE IF NOT EXISTS ai_jobs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT, model TEXT, prompt_version TEXT,
  input_json TEXT, raw_output TEXT, output_json TEXT, error TEXT, system_text TEXT);

CREATE INDEX IF NOT EXISTS idx_people_ai_jobs_target ON ai_jobs (kind, target_id, id);

-- Local housekeeping for the PIN lock: which browsers have People pages
-- open right now. The browser holds a random id; only a hash of it is
-- here, with "op" or "l:<leader id>" and two clock stamps. Never exported.
-- No personal content.
CREATE TABLE IF NOT EXISTS unlocks (
  id_hash TEXT PRIMARY KEY, identity TEXT NOT NULL, created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL);
"""


def db_path() -> str:
    """Resolved on every call: tests (and a moved disk) repoint the shift
    database, and the People file follows it unless PEOPLE_DB_PATH is set."""
    return (os.getenv("PEOPLE_DB_PATH", "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(shift_db.DB_PATH)), "people.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_ready: set[str] = set()


def init_db() -> None:
    """Create the core tables, then let each feature store add its own. A
    feature whose schema fails is skipped with a warning: it must not take
    the rest of People down with it."""
    path = db_path()
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        with conn:
            _pepper(conn)
        _ready.add(path)
        for name in FEATURE_STORES:
            try:
                importlib.import_module(name).init_schema(conn)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"WARNING: People store {name} failed to set up its tables: {e!r}")


def ensure() -> None:
    """Make sure the store at the current path has its tables. The app sets
    it up at boot; this covers a path that changed since (a test's fresh
    database, a moved disk), so a People page never meets a missing table."""
    path = db_path()
    if path not in _ready or not os.path.exists(path):
        init_db()


def _line(value, cap: int) -> str:
    """One clean line of text, capped."""
    return " ".join(hub_text.clean(str(value or "")).split())[:cap]


def report_fault(where: str, error: BaseException) -> None:
    """One fixed line in the server log for a page that failed: where (a
    label of ours) and the kind of error. Never the error's own message,
    the address that was asked for, or anything a person typed."""
    print(f"ERROR: People {_line(where, 40)} failed: {type(error).__name__}")


def same_person(leader_id, name, other_id, other_name) -> bool:
    """True only when the leader id AND the sign-in name both match. Every
    People access check that ties a row to a person uses this pair, so a
    renumbered or renamed login fails closed."""
    if not leader_id or not other_id or not name or not other_name:
        return False
    try:
        if int(leader_id) != int(other_id):
            return False
    except (TypeError, ValueError):
        return False
    return str(name).casefold() == str(other_name).casefold()


# ---------------------------------------------------------------------------
# The PIN lock, kept on the server
# ---------------------------------------------------------------------------

def _unlock_hash(unlock_id: str) -> str:
    return hashlib.sha256(("people-unlock:" + str(unlock_id or "")).encode()).hexdigest()


def unlock_open(identity: str, now: int, max_seconds: int) -> str:
    """Record that this sign-in entered its PIN again, and return the
    random id its browser keeps. Rows too old to count are cleared out."""
    unlock_id = secrets.token_hex(16)
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM unlocks WHERE created_at < ?", (int(now) - int(max_seconds),))
        conn.execute("INSERT INTO unlocks (id_hash, identity, created_at, last_seen) VALUES (?,?,?,?)",
                     (_unlock_hash(unlock_id), str(identity)[:40], int(now), int(now)))
    return unlock_id


def unlock_valid(unlock_id, identity: str, now: int, idle_seconds: int, max_seconds: int) -> bool:
    """Is this browser's unlock still good: it exists here (it was not
    locked or signed out, from this browser or any copy of its cookie), it
    belongs to this same sign-in, it was used recently enough, and the PIN
    was entered within the longest time allowed. A good one is stamped as
    just used. One that belongs to a different sign-in is removed."""
    if not unlock_id or not isinstance(unlock_id, str):
        return False
    key = _unlock_hash(unlock_id)
    with closing(connect()) as conn:
        row = conn.execute("SELECT identity, created_at, last_seen FROM unlocks WHERE id_hash=?",
                           (key,)).fetchone()
        if not row:
            return False
        good = (row["identity"] == identity
                and -60 <= now - row["last_seen"] <= idle_seconds
                and -60 <= now - row["created_at"] <= max_seconds)
        with conn:
            if not good:
                conn.execute("DELETE FROM unlocks WHERE id_hash=?", (key,))
            elif now - row["last_seen"] >= 30:      # not a write on every tap
                conn.execute("UPDATE unlocks SET last_seen=? WHERE id_hash=?", (int(now), key))
    return good


def unlock_close(unlock_id) -> None:
    """Lock now, or sign out: the unlock is gone for every copy of it."""
    if unlock_id and isinstance(unlock_id, str):
        with closing(connect()) as conn, conn:
            conn.execute("DELETE FROM unlocks WHERE id_hash=?", (_unlock_hash(unlock_id),))


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def _audit(conn, who: str, action: str, object_type: str = "",
           object_id: int | None = None, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit (at, who, action, object_type, object_id, detail) VALUES (?,?,?,?,?,?)",
        (shift_db.now_stamp(), _line(who, 80), _line(action, 60), _line(object_type, 40),
         object_id if isinstance(object_id, int) and not isinstance(object_id, bool) else None,
         _line(detail, 200)))


def audit(who: str, action: str, object_type: str = "", object_id: int | None = None,
          detail: str = "") -> None:
    """One row in the activity log. `detail` is for ids and short fixed
    labels, never free text about a person."""
    with closing(connect()) as conn, conn:
        _audit(conn, who, action, object_type, object_id, detail)


def audit_feed(limit: int = 200, offset: int = 0) -> list[dict]:
    """Newest first."""
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# key -> default. Values are always strings.
SETTINGS = {
    "ai_reviews": "0",
    "ai_hiring": "0",
    "lock_minutes": "30",
    "employer_name": "Chick-fil-A Wharncliffe & Wonderland",
    "hiring_contact_name": "",
    "hiring_contact_email": "",
    "hiring_contact_phone": "",
    "min_wage_general": "17.95",
    "min_wage_student": "16.90",
    "quick_link_days": "7",
    "retention_months": "24",
}
# A person's contact details: the activity log records that these changed,
# never the value.
_CONTACT_SETTINGS = {"hiring_contact_name", "hiring_contact_email", "hiring_contact_phone"}
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no", ""}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOCK_MINUTES_RANGE = (5, 120)
QUICK_LINK_DAYS_RANGE = (1, 30)
RETENTION_MONTHS_RANGE = (12, 120)


def _whole(value: str, low: int, high: int, clamp: bool = False) -> str | None:
    if not re.fullmatch(r"[0-9]{1,6}", value):
        return None
    number = int(value)
    if clamp:
        return str(max(low, min(high, number)))
    return str(number) if low <= number <= high else None


def _clean_setting(key: str, value) -> str | None:
    """The value as it will be stored, or None when it isn't acceptable."""
    value = _line(value, 200)
    if key in ("ai_reviews", "ai_hiring"):
        low = value.casefold()
        return "1" if low in _ON else "0" if low in _OFF else None
    if key == "lock_minutes":
        return _whole(value, *LOCK_MINUTES_RANGE, clamp=True)
    if key == "quick_link_days":
        return _whole(value, *QUICK_LINK_DAYS_RANGE)
    if key == "retention_months":
        return _whole(value, *RETENTION_MONTHS_RANGE)
    if key in ("min_wage_general", "min_wage_student"):
        if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,2})?", value):
            return None
        return f"{float(value):.2f}" if 0 < float(value) < 200 else None
    if key == "employer_name":
        return value[:120] or None
    if key == "hiring_contact_name":
        return value[:80]
    if key == "hiring_contact_email":
        return value[:120] if not value or (_EMAIL.fullmatch(value) and len(value) <= 120) else None
    if key == "hiring_contact_phone":
        digits = re.sub(r"\D", "", value)
        if not value:
            return ""
        return value[:30] if 10 <= len(digits) <= 15 and re.fullmatch(r"[0-9+().\s-]+", value) else None
    return None


def get_setting(key: str) -> str:
    if key not in SETTINGS:
        raise KeyError(key)
    with closing(connect()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else SETTINGS[key]


def setting_on(key: str) -> bool:
    return get_setting(key) == "1"


def settings_all() -> dict[str, dict]:
    """Every setting with who changed it last: {key: {value, updated_at,
    updated_by}} (the stamps are None while a setting is still its default)."""
    with closing(connect()) as conn:
        rows = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM settings")}
    return {key: {"value": rows[key]["value"] if key in rows else default,
                  "updated_at": rows[key]["updated_at"] if key in rows else None,
                  "updated_by": rows[key]["updated_by"] if key in rows else None}
            for key, default in SETTINGS.items()}


WAGE_SETTINGS = ("min_wage_general", "min_wage_student")
# The day the two wage figures above were last true as shipped: Ontario's
# rates for 1 October 2026. A store that has never confirmed them is
# treated as having confirmed them on this day.
WAGES_AS_OF = "2026-10-01"


def confirm_setting(key: str, by: str) -> bool:
    """Stamp a wage figure as checked today without changing it ("Still
    correct today"). Only the two minimum wage figures can be confirmed."""
    if key not in WAGE_SETTINGS:
        return False
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        value = row["value"] if row else SETTINGS[key]
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, value, shift_db.now_stamp(), _line(by, 80)))
        _audit(conn, by, "setting confirmed", "setting", None, f"{key} = {value}")
    return True


def wages_to_check() -> list[str]:
    """The wage figures last confirmed before the most recent 1 October,
    the day Ontario's minimum wage usually changes. Empty when both have
    been looked at since."""
    today = shift_db.now_local().date()
    october = f"{today.year if (today.month, today.day) >= (10, 1) else today.year - 1}-10-01"
    saved = settings_all()
    return [key for key in WAGE_SETTINGS
            if ((saved[key]["updated_at"] or "")[:10] or WAGES_AS_OF) < october]


def set_setting(key: str, value: str, by: str) -> bool:
    """Save one setting. False when the key is unknown or the value isn't
    acceptable for it; nothing is written then."""
    if key not in SETTINGS:
        return False
    clean = _clean_setting(key, value)
    if clean is None:
        return False
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if (row["value"] if row else SETTINGS[key]) == clean:
            return True                              # nothing changed: no stamp, no log
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, clean, shift_db.now_stamp(), _line(by, 80)))
        _audit(conn, by, "setting changed", "setting", None,
               key if key in _CONTACT_SETTINGS else f"{key} = {clean}")
    return True


# ---------------------------------------------------------------------------
# Who has access
# ---------------------------------------------------------------------------

def grants() -> list[dict]:
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM access_grants ORDER BY leader_name COLLATE NOCASE, capability")]


def grant(leader_id: int, leader_name: str, capability: str, by: str) -> bool:
    """Give one leader one capability. A stale grant on the same leader id
    (left behind when a restore renumbered leaders) is replaced, never
    inherited. True when something changed."""
    name = _line(leader_name, 80)
    if capability not in CAPABILITIES or not name or not isinstance(leader_id, int) \
            or isinstance(leader_id, bool) or leader_id <= 0:
        return False
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, leader_name FROM access_grants "
                           "WHERE leader_id=? AND capability=?", (leader_id, capability)).fetchone()
        if row and row["leader_name"].casefold() == name.casefold():
            return False
        if row:
            conn.execute("DELETE FROM access_grants WHERE id=?", (row["id"],))
        conn.execute(
            "INSERT INTO access_grants (leader_id, leader_name, capability, granted_at, granted_by) "
            "VALUES (?,?,?,?,?)", (leader_id, name, capability, shift_db.now_stamp(), _line(by, 80)))
        _audit(conn, by, "access given", "leader", leader_id, capability)
    return True


def revoke(grant_id: int, by: str) -> bool:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT leader_id, capability FROM access_grants WHERE id=?",
                           (grant_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM access_grants WHERE id=?", (grant_id,))
        _audit(conn, by, "access removed", "leader", row["leader_id"], row["capability"])
    return True


def capabilities(leader_id: int | None, name: str) -> frozenset[str]:
    """What this leader has been granted. A grant counts only if the leader
    id matches AND the name matches (casefold): anything else fails closed."""
    if not leader_id or not name:
        return frozenset()
    with closing(connect()) as conn:
        rows = conn.execute("SELECT leader_id, leader_name, capability FROM access_grants "
                            "WHERE leader_id=?", (leader_id,)).fetchall()
    return frozenset(r["capability"] for r in rows
                     if r["capability"] in CAPABILITIES
                     and same_person(leader_id, name, r["leader_id"], r["leader_name"]))


# ---------------------------------------------------------------------------
# Private links
# ---------------------------------------------------------------------------

def _pepper(conn) -> bytes:
    """Call inside a write transaction: the first caller creates it."""
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('token_pepper', ?)",
                 (secrets.token_hex(32),))
    return conn.execute("SELECT value FROM meta WHERE key='token_pepper'").fetchone()["value"].encode()


def new_token() -> str:
    return secrets.token_hex(16)


def token_hash(token: str) -> str:
    """What is stored instead of a private link's token: a keyed hash under
    a pepper that never leaves this database (it is not in the backup)."""
    with closing(connect()) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='token_pepper'").fetchone()
        if row:
            pepper = row["value"].encode()
        else:
            with conn:
                pepper = _pepper(conn)
    return hmac.new(pepper, (token or "").encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def export_json(by: str) -> str:
    """Every People table except the two that are local housekeeping (meta
    and unlocks: the link pepper and who has the lock open right now), as
    one JSON document. It holds private information about people:
    Operator-only, and logged."""
    with closing(connect()) as conn:
        with conn:
            _audit(conn, by, "backup downloaded")
        conn.execute("BEGIN")                        # one consistent snapshot
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT IN ('meta', 'unlocks') ORDER BY name")]
        tables = {name: [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
                  for name in names}
        conn.commit()
    return json.dumps({"format": 1, "kind": "people-backup", "exported_at": shift_db.now_stamp(),
                       "tables": tables}, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# "Our standards": the model reviews and hiring scorecards rate against
# ---------------------------------------------------------------------------

MODEL_FORMAT = 1
MODEL_MAX_BYTES = 256 * 1024
MAX_GROUPS = 8
MAX_DIMENSIONS = 30
LEVEL_COUNT = 4
CAPS = {"model_name": 80, "level": 40, "group": 80, "name": 80, "definition": 1200,
        "anchor": 700, "evidence_examples": 700, "question": 400}
_KEY = re.compile(r"[a-z0-9-]{2,40}")

# A small neutral example (invented, generic wording) for tests and for the
# "see an example file" download. It is never loaded automatically: the
# store's own standards are loaded by the Operator and are not in this
# repository.
PEOPLE_EXAMPLE_MODEL = {
    "format": 1,
    "name": "Example standards",
    "levels": ["Learning", "Developing", "Consistent", "Role model"],
    "groups": [
        {"name": "How we work", "dimensions": [
            {"key": "reliable",
             "name": "Reliable",
             "definition": "Arrives on time and ready, and finishes what they said they would.",
             "anchors": [
                 "Often late or not ready when the shift starts. Tasks are left unfinished without telling anyone.",
                 "On time most days. Finishes most tasks but needs reminders.",
                 "On time and ready. Finishes tasks when promised without being reminded.",
                 "Others plan around them. Warns early when something will be late and brings a plan."],
             "evidence_examples": "Start times, tasks finished by the day they were due, dated notes.",
             "hiring": {
                 "question": "Tell us about a time other people were counting on you. What did you do?",
                 "anchors": [
                     "No example, or the example shows others being let down.",
                     "A general answer with no clear example.",
                     "A clear example of following through.",
                     "A clear example, including what they did when something got in the way."]}},
            {"key": "guest-care",
             "name": "Guest care",
             "definition": "Notices what a guest needs and looks after it politely and quickly.",
             "anchors": [
                 "Waits to be asked. Guests are sometimes left waiting without a word.",
                 "Helps when asked and is polite. Sometimes misses a guest who needs something.",
                 "Greets guests, notices needs and fixes small problems on the spot.",
                 "Spots problems before the guest does and shows others how to do the same."],
             "evidence_examples": "What was seen or heard on shift, with the date.",
             "hiring": {
                 "question": "Describe a time you helped someone who was upset or confused. What happened?",
                 "anchors": [
                     "No example, or blames the other person.",
                     "A general answer about being nice.",
                     "A clear example of listening and helping.",
                     "A clear example, including checking afterwards that the person was looked after."]}},
        ]},
        {"name": "How we work together", "dimensions": [
            {"key": "teamwork",
             "name": "Teamwork",
             "definition": "Helps teammates, shares what they know and speaks to others with respect.",
             "anchors": [
                 "Keeps to their own tasks while others fall behind.",
                 "Helps when asked.",
                 "Offers help without being asked and shares what they know.",
                 "Makes the people around them better and steps in before a teammate is stuck."],
             "evidence_examples": "",
             "hiring": {
                 "question": "Tell us about a time you helped a teammate or classmate finish something.",
                 "anchors": [
                     "No example.",
                     "Helped because they were told to.",
                     "A clear example of choosing to help.",
                     "A clear example of noticing someone needed help before they asked."]}},
        ]},
    ],
}


def _field(value, cap: int, multiline: bool = False) -> tuple[str, bool]:
    """(clean text, acceptable?) where acceptable means a string within its cap."""
    if not isinstance(value, str):
        return "", value is None
    text = hub_text.clean(value)
    text = text.strip() if multiline else " ".join(text.split())
    return text, len(text) <= cap


def _four(values, where: str, what: str, problems: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) != LEVEL_COUNT:
        problems.append(f"{where}: {what} needs exactly four level descriptions, lowest first.")
        return []
    out = []
    for n, raw in enumerate(values, 1):
        text, ok = _field(raw, CAPS["anchor"], multiline=True)
        if not text or not ok:
            problems.append(f"{where}: {what}, level {n}, needs a description "
                            f"({CAPS['anchor']} characters at most).")
        out.append(text)
    return out


def _read_model(data: object) -> tuple[dict, list[str]]:
    """(the model as clean import-format data, plain-language problems)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return {}, ["This isn't a standards file. It should be one JSON object "
                    "with a name, four levels and groups of standards."]
    if data.get("format") != MODEL_FORMAT or isinstance(data.get("format"), bool):
        problems.append('The file needs "format": 1 near the top, so we know how to read it.')
    name, ok = _field(data.get("name"), CAPS["model_name"])
    if not name or not ok:
        problems.append(f"Give the model a name ({CAPS['model_name']} characters at most).")
    levels: list[str] = []
    raw_levels = data.get("levels")
    if not isinstance(raw_levels, list) or len(raw_levels) != LEVEL_COUNT:
        problems.append("List exactly four level names, lowest first.")
    else:
        for n, raw in enumerate(raw_levels, 1):
            text, ok = _field(raw, CAPS["level"])
            if not text or not ok:
                problems.append(f"Level {n} needs a name ({CAPS['level']} characters at most).")
            levels.append(text)
        named = [l.casefold() for l in levels if l]
        if len(set(named)) != len(named):
            problems.append("Two levels have the same name. Each level needs its own.")
    groups: list[dict] = []
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= MAX_GROUPS:
        problems.append(f"The file needs between 1 and {MAX_GROUPS} groups of standards.")
        raw_groups = raw_groups[:MAX_GROUPS] if isinstance(raw_groups, list) else []
    seen: set[str] = set()
    total = 0
    for g, raw_group in enumerate(raw_groups, 1):
        if not isinstance(raw_group, dict):
            problems.append(f"Group {g} isn't written as a group (it needs a name and its standards).")
            continue
        group_name, ok = _field(raw_group.get("name"), CAPS["group"])
        if not group_name or not ok:
            problems.append(f"Group {g} needs a name ({CAPS['group']} characters at most).")
        label = f"“{group_name}”" if group_name else f"group {g}"
        raw_dims = raw_group.get("dimensions")
        if not isinstance(raw_dims, list) or not raw_dims:
            problems.append(f"Group {label} has no standards in it.")
            raw_dims = []
        total += len(raw_dims)
        dims = []
        for d, raw in enumerate(raw_dims[:MAX_DIMENSIONS + 1], 1):
            if not isinstance(raw, dict):
                problems.append(f"Standard {d} in {label} isn't written as a standard.")
                continue
            dim_name, name_ok = _field(raw.get("name"), CAPS["name"])
            where = (f"“{dim_name}”" if dim_name else f"Standard {d}") + f" in {label}"
            key = raw.get("key") if isinstance(raw.get("key"), str) else ""
            if not _KEY.fullmatch(key):
                problems.append(f"{where}: the key must be 2 to 40 small letters, numbers or dashes "
                                "(for example guest-care).")
            elif key in seen:
                problems.append(f"The key “{key}” is used more than once. Every standard needs its own.")
            seen.add(key)
            if not dim_name or not name_ok:
                problems.append(f"{where}: needs a name ({CAPS['name']} characters at most).")
            definition, ok = _field(raw.get("definition"), CAPS["definition"], multiline=True)
            if not definition or not ok:
                problems.append(f"{where}: needs a definition ({CAPS['definition']:,} characters at most).")
            anchors = _four(raw.get("anchors"), where, "the standard", problems)
            examples, ok = _field(raw.get("evidence_examples"), CAPS["evidence_examples"], multiline=True)
            if not ok:
                problems.append(f"{where}: the evidence examples must be text, "
                                f"{CAPS['evidence_examples']} characters at most.")
            clean = {"key": key, "name": dim_name, "definition": definition, "anchors": anchors,
                     "evidence_examples": examples}
            raw_hiring = raw.get("hiring")
            if raw_hiring is not None:
                if not isinstance(raw_hiring, dict):
                    problems.append(f"{where}: the hiring part needs a question and four level descriptions.")
                else:
                    question, ok = _field(raw_hiring.get("question"), CAPS["question"], multiline=True)
                    if not question or not ok:
                        problems.append(f"{where}: the hiring question is missing or longer than "
                                        f"{CAPS['question']} characters.")
                    clean["hiring"] = {"question": question, "anchors": _four(
                        raw_hiring.get("anchors"), where, "the hiring question", problems)}
            dims.append(clean)
        groups.append({"name": group_name, "dimensions": dims})
    if total > MAX_DIMENSIONS:
        problems.append(f"A model can hold {MAX_DIMENSIONS} standards at most. This file has {total}.")
    return {"format": MODEL_FORMAT, "name": name, "levels": levels, "groups": groups}, problems


def validate_model(data: object) -> list[str]:
    """Plain-language problems with a standards file; empty means valid."""
    return _read_model(data)[1]


def save_model(data: dict, by: str) -> tuple[int | None, list[str]]:
    """Every save is a new version, and the new one becomes the active
    model. Nothing is saved while any problem remains."""
    model, problems = _read_model(data)
    if problems:
        return None, problems
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM standards_models").fetchone()[0]
        conn.execute("UPDATE standards_models SET active=0 WHERE active=1")
        model_id = conn.execute(
            "INSERT INTO standards_models (version, name, levels_json, active, created_at, created_by) "
            "VALUES (?,?,?,1,?,?)",
            (version, model["name"], json.dumps(model["levels"], ensure_ascii=False),
             shift_db.now_stamp(), _line(by, 80))).lastrowid
        sort = 0
        for group in model["groups"]:
            for dim in group["dimensions"]:
                sort += 1
                hiring = dim.get("hiring")
                conn.execute(
                    "INSERT INTO standards_dimensions (model_id, sort, group_name, key, name, definition, "
                    "anchors_json, evidence_examples, hiring_question, hiring_anchors_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (model_id, sort, group["name"], dim["key"], dim["name"], dim["definition"],
                     json.dumps(dim["anchors"], ensure_ascii=False), dim["evidence_examples"],
                     hiring["question"] if hiring else "",
                     json.dumps(hiring["anchors"] if hiring else [], ensure_ascii=False)))
        _audit(conn, by, "standards saved", "standards", model_id, f"version {version}")
    return model_id, []


def _model_from(conn, row) -> dict:
    dims = []
    groups: list[dict] = []
    for r in conn.execute("SELECT * FROM standards_dimensions WHERE model_id=? ORDER BY sort, id",
                          (row["id"],)):
        hiring_anchors = json.loads(r["hiring_anchors_json"] or "[]")
        dim = {"key": r["key"], "group": r["group_name"], "name": r["name"],
               "definition": r["definition"], "anchors": json.loads(r["anchors_json"]),
               "evidence_examples": r["evidence_examples"],
               "hiring": ({"question": r["hiring_question"], "anchors": hiring_anchors}
                          if r["hiring_question"] else None)}
        dims.append(dim)
        if not groups or groups[-1]["name"] != r["group_name"]:
            groups.append({"name": r["group_name"], "dimensions": []})
        groups[-1]["dimensions"].append(dim)
    return {"id": row["id"], "version": row["version"], "name": row["name"],
            "levels": json.loads(row["levels_json"]), "groups": groups, "dimensions": dims,
            "active": bool(row["active"]), "created_at": row["created_at"],
            "created_by": row["created_by"]}


def active_model() -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM standards_models WHERE active=1 "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        return _model_from(conn, row) if row else None


def get_model(model_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM standards_models WHERE id=?", (model_id,)).fetchone()
        return _model_from(conn, row) if row else None


def model_versions() -> list[dict]:
    """Every saved version, newest first (no wording, just the stamps)."""
    with closing(connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, version, name, active, created_at, created_by FROM standards_models "
            "ORDER BY id DESC")]


def model_export(model_id: int) -> str:
    """One model version in the JSON import format ('' if there is no such
    version)."""
    model = get_model(model_id)
    if not model:
        return ""
    groups = []
    for group in model["groups"]:
        dims = []
        for dim in group["dimensions"]:
            out = {"key": dim["key"], "name": dim["name"], "definition": dim["definition"],
                   "anchors": dim["anchors"], "evidence_examples": dim["evidence_examples"]}
            if dim["hiring"]:
                out["hiring"] = dim["hiring"]
            dims.append(out)
        groups.append({"name": group["name"], "dimensions": dims})
    return json.dumps({"format": MODEL_FORMAT, "name": model["name"], "levels": model["levels"],
                       "groups": groups}, ensure_ascii=False, indent=1)
