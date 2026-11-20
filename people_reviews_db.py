"""Storage for leader reviews (the People section).

Everything here is about named leaders, so it lives in the People database
(people_db.connect()), never in the shift database or its backups.
people_db.init_db() calls init_schema(conn) at every boot.

How a review moves:

    draft -> (finalise, every "before you finish" answer is No) -> final
          -> (share) -> shared -> (the leader acknowledges) -> acknowledged
    draft -> (finalise, any answer is "Yes or not sure") -> on_hold
          -> (the Operator releases it) -> draft
    final -> (the Operator reopens it) -> draft

Ratings, the summary, agreed actions and time away change only while a
review is a draft in an open cycle; the Operator may change a level in
calibration while it is final. After it is shared nothing changes except
the leader's acknowledgement and actions being marked done. Every state
change is one guarded UPDATE that says whether a row changed, and writes
its own line in the activity log (ids and fixed labels only).

A person is tied to a row by leader id AND sign-in name together
(people_db.same_person), so a renumbered or renamed sign-in fails closed.
"""

import json
import re
from contextlib import closing
from datetime import date, datetime

import hub_text
import people_db
import shift_db

OBS_KINDS = ("strength", "growth", "note")
OBS_KIND_LABELS = {"strength": "Did well", "growth": "To work on", "note": "Note"}

STATUSES = ("draft", "on_hold", "final", "shared", "acknowledged")
STATUS_LABELS = {"draft": "Draft", "on_hold": "On hold", "final": "Finalised",
                 "shared": "Shared", "acknowledged": "Acknowledged"}
# The reviewer has finished writing: the evidence and wording are settled.
DONE_STATUSES = ("final", "shared", "acknowledged")

TITLE_SUGGESTIONS = ("Director", "Team leader", "Supervisor", "Emerging leader")

# The protected-context questions asked before a review can be finalised.
# Each is answered "no" or "yes" (yes means "Yes or not sure").
GATE = (
    ("leave", "Was this leader away on any leave during the period (sick, family, pregnancy "
              "or parental, bereavement or any other leave)?"),
    ("accommodation", "Does this leader have an accommodation, or have they asked for one?"),
    ("health", "Do you know of, or suspect, a health condition or disability that could be "
               "affecting their work?"),
    ("pregnancy", "Is this leader pregnant, or have they recently returned from pregnancy or "
                  "parental leave?"),
    ("injury", "Has this leader had a workplace injury or a WSIB claim?"),
    ("concern", "Has this leader raised a concern or complaint about safety, harassment, pay "
                "or how they were treated?"),
)
GATE_KEYS = tuple(key for key, _ in GATE)

# What the Operator can record when releasing a hold. Fixed sentences on
# purpose: the reason a review was held is never written down here.
RELEASE_NOTES = {
    "carry_on": "Handled outside this tool. The review carries on as written.",
    "recheck": "Handled outside this tool. The reviewer will check the review again before "
               "finishing it.",
}

CAPS = {"name": 80, "title": 60, "area": 120, "observation": 2000, "comment": 2000,
        "summary": 4000, "action": 300, "reflection": 2000, "ack_comment": 4000,
        "cal_reason": 500, "cycle_name": 80}
MAX_TIME_AWAY = 20
MAX_ACTIONS = 12
OBS_MAX_AGE_DAYS = 730
ACTION_RULE = "Every action needs a measure, a target and a day it gets checked."

_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_KEY = re.compile(r"[a-z0-9-]{2,40}")

SCHEMA = """
-- People under review. display_name is how the review names them. Personal
-- content: a leader's name, sign-in link, title and area, and who reviews them.
CREATE TABLE IF NOT EXISTS rv_subjects (
  id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, leader_id INTEGER, leader_name TEXT,
  title TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '',
  reviewer_leader_id INTEGER, reviewer_name TEXT,            -- both NULL = the Operator reviews
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

-- Dated, behaviour-based notes. Work conduct only: never health, family,
-- leave, complaints. Personal content: what a reviewer saw a leader do.
CREATE TABLE IF NOT EXISTS rv_observations (
  id INTEGER PRIMARY KEY, subject_id INTEGER NOT NULL REFERENCES rv_subjects(id) ON DELETE CASCADE,
  obs_date TEXT NOT NULL, dimension_key TEXT, kind TEXT NOT NULL DEFAULT 'note',   -- strength | growth | note
  body TEXT NOT NULL, in_review INTEGER NOT NULL DEFAULT 1,
  author_name TEXT NOT NULL, author_leader_id INTEGER, created_at TEXT NOT NULL, removed_at TEXT, removed_by TEXT);

-- A review period. No personal content beyond who created it.
CREATE TABLE IF NOT EXISTS rv_cycles (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  due_date TEXT NOT NULL, ask_reflection INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'open',  -- open | closed
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

-- One leader's review in one cycle. Personal content: the reviewer's written
-- summary, the leader's own comments, and the answers to the "before you
-- finish" questions (gate_json: Operator eyes only, never shown to the leader).
CREATE TABLE IF NOT EXISTS rv_reviews (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL REFERENCES rv_cycles(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES rv_subjects(id) ON DELETE CASCADE, model_id INTEGER NOT NULL,
  reviewer_leader_id INTEGER, reviewer_name TEXT,
  status TEXT NOT NULL DEFAULT 'draft',        -- draft | on_hold | final | shared | acknowledged
  strengths TEXT NOT NULL DEFAULT '', growth TEXT NOT NULL DEFAULT '',
  time_away TEXT NOT NULL DEFAULT '',          -- JSON list of [start, end] date pairs the reviewer marked as away; no reason is ever stored
  gate_json TEXT, gate_at TEXT, gate_by TEXT, hold_note TEXT NOT NULL DEFAULT '', released_at TEXT, released_by TEXT,
  reflection_at TEXT, finalised_at TEXT, finalised_by TEXT, shared_at TEXT, shared_by TEXT,
  discussed_on TEXT, ack_at TEXT, ack_comment TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE (cycle_id, subject_id));

-- The level a person gave for one standard, with their comment. ai_level is
-- what the AI draft suggested when it was first shown (after the person had
-- rated); cal_* records a change the Operator made in calibration.
CREATE TABLE IF NOT EXISTS rv_ratings (
  id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES rv_reviews(id) ON DELETE CASCADE,
  dimension_key TEXT NOT NULL, level INTEGER, not_enough INTEGER NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
  rated_at TEXT, rated_by TEXT, ai_level INTEGER, ai_shown_at TEXT,
  cal_from INTEGER, cal_reason TEXT, cal_at TEXT, cal_by TEXT,
  UNIQUE (review_id, dimension_key));

-- Every agreed action needs a measure, a target and a day it gets checked.
CREATE TABLE IF NOT EXISTS rv_actions (
  id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES rv_reviews(id) ON DELETE CASCADE,
  action TEXT NOT NULL, measure TEXT NOT NULL, target TEXT NOT NULL, check_date TEXT NOT NULL,
  done_at TEXT, done_by TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

-- The leader's own reflection, written by the leader.
CREATE TABLE IF NOT EXISTS rv_reflections (
  id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL REFERENCES rv_reviews(id) ON DELETE CASCADE,
  dimension_key TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', updated_at TEXT, UNIQUE (review_id, dimension_key));

CREATE INDEX IF NOT EXISTS idx_rv_observations_subject ON rv_observations (subject_id, obs_date);
CREATE INDEX IF NOT EXISTS idx_rv_reviews_subject ON rv_reviews (subject_id);
CREATE INDEX IF NOT EXISTS idx_rv_reviews_reviewer ON rv_reviews (reviewer_leader_id);
CREATE INDEX IF NOT EXISTS idx_rv_actions_review ON rv_actions (review_id);
"""


def init_schema(conn) -> None:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _line(value, cap: int) -> str:
    """One clean line of text, capped."""
    return " ".join(hub_text.clean(str(value or "")).split())[:cap]


def _text(value, cap: int) -> str:
    """Clean text that may run over several lines, capped."""
    return hub_text.clean(str(value or "")).strip()[:cap]


def _day(value) -> str | None:
    """A real calendar date as YYYY-MM-DD, or None."""
    raw = str(value or "").strip()
    if not _DAY.fullmatch(raw):
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def _today() -> str:
    """The calendar date (not the 4 a.m. business day): due dates, check
    dates and "not in the future" are about the calendar."""
    return shift_db.now_local().date().isoformat()


def _id(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 2 ** 62 else None


def _log(conn, who: str, action: str, object_type: str = "", object_id: int | None = None,
         detail: str = "") -> None:
    """A line in the People activity log, inside the same transaction as
    the change it describes. Ids and short fixed labels only."""
    people_db._audit(conn, who, action, object_type, object_id, detail)


def _pair(leader_id, name) -> tuple[int | None, str | None]:
    """A sign-in link is an id AND a name, or nothing at all."""
    leader_id, name = _id(leader_id), _line(name, CAPS["name"])
    return (leader_id, name) if leader_id and name else (None, None)


def is_self(subject: dict, leader_id, name) -> bool:
    """Is this sign-in the person the row is about? Deliberately loose (the
    id OR either name is enough): it is only ever used to refuse, so that
    nobody reviews themselves."""
    if leader_id and subject.get("leader_id") and int(leader_id) == int(subject["leader_id"]):
        return True
    mine = str(name or "").casefold()
    return bool(mine) and mine in {str(subject.get("leader_name") or "").casefold(),
                                   str(subject.get("display_name") or "").casefold()}


def time_away(review: dict) -> list[tuple[str, str]]:
    """The date ranges the reviewer marked as away, oldest first. Dates
    only: no reason is ever stored."""
    try:
        raw = json.loads(review.get("time_away") or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            start, end = _day(item[0]), _day(item[1])
            if start and end and start <= end:
                out.append((start, end))
    return sorted(out)


def gate_answers(review: dict) -> dict:
    """What was answered before finishing: {"answers": {key: "no"|"yes"},
    "since_release": "no"|"yes"|None}. For the Operator's eyes only."""
    try:
        raw = json.loads(review.get("gate_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
    return {"answers": {key: answers.get(key) for key in GATE_KEYS if answers.get(key) in ("no", "yes")},
            "since_release": raw.get("since_release") if raw.get("since_release") in ("no", "yes") else None}


# ---------------------------------------------------------------------------
# Leaders under review
# ---------------------------------------------------------------------------

def subjects(include_inactive: bool = False) -> list[dict]:
    q = "SELECT * FROM rv_subjects "
    if not include_inactive:
        q += "WHERE active=1 "
    q += "ORDER BY active DESC, display_name COLLATE NOCASE, id"
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(q)]


def get_subject(subject_id: int) -> dict | None:
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT * FROM rv_subjects WHERE id=?", (subject_id,)).fetchone()
    return dict(row) if row else None


def _subject_problem(conn, subject_id, display_name, leader, reviewer) -> str | None:
    if not display_name:
        return "Give the leader a name."
    for row in conn.execute("SELECT * FROM rv_subjects WHERE id != ?", (subject_id or 0,)):
        same_link = leader[0] and people_db.same_person(leader[0], leader[1],
                                                        row["leader_id"], row["leader_name"])
        if same_link or row["display_name"].casefold() == display_name.casefold():
            return f"{row['display_name']} is already on the list."
    if reviewer[0] and is_self({"leader_id": leader[0], "leader_name": leader[1],
                                "display_name": display_name}, reviewer[0], reviewer[1]):
        return "A leader can't review themselves. Choose someone else."
    return None


def add_subject(display_name: str, by: str, leader_id=None, leader_name=None, title: str = "",
                area: str = "", reviewer_leader_id=None, reviewer_name=None
                ) -> tuple[int | None, str | None]:
    """Put a leader under review. (new id, None) or (None, what to fix)."""
    display_name = _line(display_name, CAPS["name"])
    leader = _pair(leader_id, leader_name)
    reviewer = _pair(reviewer_leader_id, reviewer_name)
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        problem = _subject_problem(conn, None, display_name, leader, reviewer)
        if problem:
            return None, problem
        subject_id = conn.execute(
            "INSERT INTO rv_subjects (display_name, leader_id, leader_name, title, area, "
            "reviewer_leader_id, reviewer_name, active, created_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)",
            (display_name, leader[0], leader[1], _line(title, CAPS["title"]),
             _line(area, CAPS["area"]), reviewer[0], reviewer[1], shift_db.now_stamp(),
             _line(by, CAPS["name"]))).lastrowid
        _log(conn, by, "leader added to reviews", "leader under review", subject_id)
    return subject_id, None


def update_subject(subject_id: int, by: str, *, display_name: str, leader_id=None,
                   leader_name=None, title: str = "", area: str = "", reviewer_leader_id=None,
                   reviewer_name=None, active: bool = True) -> str | None:
    """Change a leader's details. None when saved, or what to fix. A change
    of reviewer also moves this leader's unfinished reviews to the new
    reviewer; finished ones keep the reviewer who wrote them."""
    display_name = _line(display_name, CAPS["name"])
    leader = _pair(leader_id, leader_name)
    reviewer = _pair(reviewer_leader_id, reviewer_name)
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM rv_subjects WHERE id=?", (subject_id,)).fetchone():
            return "That leader isn't on the list any more."
        problem = _subject_problem(conn, subject_id, display_name, leader, reviewer)
        if problem:
            return problem
        conn.execute(
            "UPDATE rv_subjects SET display_name=?, leader_id=?, leader_name=?, title=?, area=?, "
            "reviewer_leader_id=?, reviewer_name=?, active=? WHERE id=?",
            (display_name, leader[0], leader[1], _line(title, CAPS["title"]),
             _line(area, CAPS["area"]), reviewer[0], reviewer[1], 1 if active else 0, subject_id))
        conn.execute(
            "UPDATE rv_reviews SET reviewer_leader_id=?, reviewer_name=? "
            "WHERE subject_id=? AND status IN ('draft','on_hold')",
            (reviewer[0], reviewer[1], subject_id))
        _log(conn, by, "leader under review changed", "leader under review", subject_id)
    return None


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

def _locked(conn, obs) -> bool:
    """Part of a finished review: the note was there, inside the period,
    when the review was finalised, so it can no longer be changed."""
    return conn.execute(
        "SELECT 1 FROM rv_reviews r JOIN rv_cycles c ON c.id = r.cycle_id "
        "WHERE r.subject_id=? AND r.status IN ('final','shared','acknowledged') "
        "AND ? BETWEEN c.period_start AND c.period_end AND r.finalised_at IS NOT NULL "
        "AND ? <= r.finalised_at LIMIT 1",
        (obs["subject_id"], obs["obs_date"], obs["created_at"])).fetchone() is not None


def add_observation(subject_id: int, obs_date: str, dimension_key: str | None, kind: str,
                    body: str, author_name: str, author_leader_id: int | None = None
                    ) -> tuple[int | None, str | None]:
    """One dated note about what a leader did. (new id, None) or (None,
    what to fix)."""
    day = _day(obs_date)
    today = _today()
    if not day:
        return None, "Choose the date you saw it."
    if day > today:
        return None, "That date hasn't happened yet."
    if (date.fromisoformat(today) - date.fromisoformat(day)).days > OBS_MAX_AGE_DAYS:
        return None, "That date is too far back. Write notes within a day or two."
    body = _text(body, CAPS["observation"])
    if not body:
        return None, "Write what you saw or heard first."
    key = dimension_key if isinstance(dimension_key, str) and _KEY.fullmatch(dimension_key) else None
    kind = kind if kind in OBS_KINDS else "note"
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM rv_subjects WHERE id=? AND active=1",
                            (subject_id,)).fetchone():
            return None, "That leader isn't under review right now."
        obs_id = conn.execute(
            "INSERT INTO rv_observations (subject_id, obs_date, dimension_key, kind, body, in_review, "
            "author_name, author_leader_id, created_at) VALUES (?,?,?,?,?,1,?,?,?)",
            (subject_id, day, key, kind, body, _line(author_name, CAPS["name"]),
             _id(author_leader_id), shift_db.now_stamp())).lastrowid
        _log(conn, author_name, "observation added", "leader under review", subject_id, f"note {obs_id}")
    return obs_id, None


def observations(subject_id: int, removed: bool = False) -> list[dict]:
    """A leader's notes, newest first, each with "locked" (part of a
    finished review). removed=True returns only the removed ones."""
    with closing(people_db.connect()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM rv_observations WHERE subject_id=? AND removed_at IS "
            + ("NOT NULL" if removed else "NULL") + " ORDER BY obs_date DESC, id DESC", (subject_id,))]
        for row in rows:
            row["locked"] = _locked(conn, row)
    return rows


def get_observation(obs_id: int) -> dict | None:
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT * FROM rv_observations WHERE id=?", (obs_id,)).fetchone()
        if not row:
            return None
        obs = dict(row)
        obs["locked"] = _locked(conn, obs)
    return obs


def toggle_observation(obs_id: int, by: str) -> bool | None:
    """Flip "leave out of the review". Returns the new in_review value, or
    None when nothing changed (gone, removed, or part of a finished review)."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM rv_observations WHERE id=? AND removed_at IS NULL",
                           (obs_id,)).fetchone()
        if not row or _locked(conn, row):
            return None
        new = 0 if row["in_review"] else 1
        conn.execute("UPDATE rv_observations SET in_review=? WHERE id=? AND removed_at IS NULL",
                     (new, obs_id))
        _log(conn, by, "observation kept in the review" if new else "observation left out of the review",
             "leader under review", row["subject_id"], f"note {obs_id}")
    return bool(new)


def remove_observation(obs_id: int, by: str) -> bool:
    """Take a note off the log. The row is kept (who removed it, and when)."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM rv_observations WHERE id=? AND removed_at IS NULL",
                           (obs_id,)).fetchone()
        if not row or _locked(conn, row):
            return False
        conn.execute("UPDATE rv_observations SET removed_at=?, removed_by=? WHERE id=? AND removed_at IS NULL",
                     (shift_db.now_stamp(), _line(by, CAPS["name"]), obs_id))
        _log(conn, by, "observation removed", "leader under review", row["subject_id"], f"note {obs_id}")
    return True


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

def _cycle_model_id(conn, cycle_id: int) -> int | None:
    """The standards version this cycle's reviews are written against: every
    leader in one cycle is rated against the same wording."""
    row = conn.execute("SELECT model_id FROM rv_reviews WHERE cycle_id=? ORDER BY id LIMIT 1",
                       (cycle_id,)).fetchone()
    return row["model_id"] if row else None


def create_cycle(name: str, period_start: str, period_end: str, due_date: str,
                 ask_reflection: bool, by: str) -> tuple[int | None, str | None]:
    """Start a review cycle: a draft review for every active leader under
    review, pinned to the active standards and to that leader's reviewer."""
    name = _line(name, CAPS["cycle_name"])
    start, end, due = _day(period_start), _day(period_end), _day(due_date)
    if not name:
        return None, "Give the cycle a name."
    if not start or not end or not due:
        return None, "A cycle needs a start date, an end date and a due date."
    if start > end:
        return None, "The period can't end before it starts."
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 731:
        return None, "A review period can't be longer than two years."
    if due < start:
        return None, "The due date can't be before the period starts."
    model = people_db.active_model()
    if not model:
        return None, "Load Our standards first. A review rates each standard, so it needs them."
    now = shift_db.now_stamp()
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        people = conn.execute("SELECT * FROM rv_subjects WHERE active=1 ORDER BY id").fetchall()
        if not people:
            return None, "Add the leaders you review first."
        cycle_id = conn.execute(
            "INSERT INTO rv_cycles (name, period_start, period_end, due_date, ask_reflection, status, "
            "created_at, created_by) VALUES (?,?,?,?,?,'open',?,?)",
            (name, start, end, due, 1 if ask_reflection else 0, now, _line(by, CAPS["name"]))).lastrowid
        for person in people:
            conn.execute(
                "INSERT INTO rv_reviews (cycle_id, subject_id, model_id, reviewer_leader_id, "
                "reviewer_name, status, created_at) VALUES (?,?,?,?,?,'draft',?)",
                (cycle_id, person["id"], model["id"], person["reviewer_leader_id"],
                 person["reviewer_name"], now))
        _log(conn, by, "review cycle started", "review cycle", cycle_id, f"{len(people)} reviews")
    return cycle_id, None


def add_review(cycle_id: int, subject_id: int, by: str) -> tuple[int | None, str | None]:
    """Add a leader who joined the list after an open cycle was started."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        cycle = conn.execute("SELECT * FROM rv_cycles WHERE id=? AND status='open'", (cycle_id,)).fetchone()
        person = conn.execute("SELECT * FROM rv_subjects WHERE id=? AND active=1", (subject_id,)).fetchone()
        if not cycle or not person:
            return None, "That cycle is closed, or that leader isn't under review."
        if conn.execute("SELECT 1 FROM rv_reviews WHERE cycle_id=? AND subject_id=?",
                        (cycle_id, subject_id)).fetchone():
            return None, f"{person['display_name']} already has a review in this cycle."
        model_id = _cycle_model_id(conn, cycle_id)
        if not model_id:
            model = people_db.active_model()
            if not model:
                return None, "Load Our standards first."
            model_id = model["id"]
        review_id = conn.execute(
            "INSERT INTO rv_reviews (cycle_id, subject_id, model_id, reviewer_leader_id, reviewer_name, "
            "status, created_at) VALUES (?,?,?,?,?,'draft',?)",
            (cycle_id, subject_id, model_id, person["reviewer_leader_id"], person["reviewer_name"],
             shift_db.now_stamp())).lastrowid
        _log(conn, by, "review added to a cycle", "review", review_id, f"cycle {cycle_id}")
    return review_id, None


def get_cycle(cycle_id: int) -> dict | None:
    with closing(people_db.connect()) as conn:
        row = conn.execute("SELECT * FROM rv_cycles WHERE id=?", (cycle_id,)).fetchone()
        if not row:
            return None
        cycle = dict(row)
        cycle["model_id"] = _cycle_model_id(conn, cycle_id)
    return cycle


def cycles() -> list[dict]:
    """Every cycle, open ones first then newest first, each with "counts"
    (reviews by status) and "total"."""
    with closing(people_db.connect()) as conn:
        out = [dict(r) for r in conn.execute(
            "SELECT * FROM rv_cycles ORDER BY (status='open') DESC, period_end DESC, id DESC")]
        tallies: dict[int, dict] = {}
        for r in conn.execute("SELECT cycle_id, status, COUNT(*) AS n FROM rv_reviews "
                              "GROUP BY cycle_id, status"):
            tallies.setdefault(r["cycle_id"], {})[r["status"]] = r["n"]
    for cycle in out:
        counts = tallies.get(cycle["id"], {})
        cycle["counts"] = {status: counts.get(status, 0) for status in STATUSES}
        cycle["total"] = sum(cycle["counts"].values())
    return out


def close_cycle(cycle_id: int, by: str) -> bool:
    """Finish a cycle. Reviews still in draft can no longer be written;
    finished ones can still be shared and acknowledged."""
    with closing(people_db.connect()) as conn, conn:
        changed = conn.execute("UPDATE rv_cycles SET status='closed' WHERE id=? AND status='open'",
                               (cycle_id,)).rowcount > 0
        if changed:
            _log(conn, by, "review cycle closed", "review cycle", cycle_id)
    return changed


# ---------------------------------------------------------------------------
# Reviews: reading
# ---------------------------------------------------------------------------

_REVIEW = (
    "SELECT r.*, c.name AS cycle_name, c.period_start, c.period_end, c.due_date, "
    "c.ask_reflection, c.status AS cycle_status, s.display_name, s.leader_id, s.leader_name, "
    "s.title, s.area, s.active AS subject_active "
    "FROM rv_reviews r JOIN rv_cycles c ON c.id = r.cycle_id JOIN rv_subjects s ON s.id = r.subject_id ")


def _shape(row) -> dict:
    review = dict(row)
    review["editable"] = review["status"] == "draft" and review["cycle_status"] == "open"
    review["done"] = review["status"] in DONE_STATUSES
    return review


def get_review(review_id: int) -> dict | None:
    """One review with its cycle and its leader: every rv_reviews column,
    cycle_name, period_start, period_end, due_date, ask_reflection,
    cycle_status, display_name, leader_id, leader_name, title, area, plus
    "editable" (a draft in an open cycle) and "done" (finalised or later)."""
    with closing(people_db.connect()) as conn:
        row = conn.execute(_REVIEW + "WHERE r.id=?", (review_id,)).fetchone()
    return _shape(row) if row else None


def cycle_reviews(cycle_id: int) -> list[dict]:
    """Alphabetical by leader. Never ordered by level."""
    with closing(people_db.connect()) as conn:
        return [_shape(r) for r in conn.execute(
            _REVIEW + "WHERE r.cycle_id=? ORDER BY s.display_name COLLATE NOCASE, r.id", (cycle_id,))]


def all_reviews() -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [_shape(r) for r in conn.execute(
            _REVIEW + "ORDER BY (c.status='open') DESC, c.due_date DESC, s.display_name COLLATE NOCASE")]


def subject_reviews(subject_id: int) -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [_shape(r) for r in conn.execute(
            _REVIEW + "WHERE r.subject_id=? ORDER BY c.period_end DESC, r.id DESC", (subject_id,))]


def ratings(review_id: int) -> dict[str, dict]:
    with closing(people_db.connect()) as conn:
        return {r["dimension_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM rv_ratings WHERE review_id=?", (review_id,))}


def actions(review_id: int) -> list[dict]:
    with closing(people_db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM rv_actions WHERE review_id=? ORDER BY check_date, id", (review_id,))]


def reflections(review_id: int) -> dict[str, str]:
    with closing(people_db.connect()) as conn:
        return {r["dimension_key"]: r["body"] for r in conn.execute(
            "SELECT dimension_key, body FROM rv_reflections WHERE review_id=? AND body != ''",
            (review_id,))}


def evidence(review: dict) -> list[dict]:
    """The observations that count for this review, oldest first: dated
    inside the cycle's period, kept in the review, not removed, and not
    dated inside a time-away range. Once a review is finalised the list is
    settled: only notes that were already written then."""
    sql = ("SELECT * FROM rv_observations WHERE subject_id=? AND removed_at IS NULL AND in_review=1 "
           "AND obs_date BETWEEN ? AND ?")
    args = [review["subject_id"], review["period_start"], review["period_end"]]
    if review["status"] in DONE_STATUSES and review.get("finalised_at"):
        sql += " AND created_at <= ?"
        args.append(review["finalised_at"])
    away = time_away(review)
    with closing(people_db.connect()) as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY obs_date, id", args)]
    return [r for r in rows if not any(start <= r["obs_date"] <= end for start, end in away)]


# ---------------------------------------------------------------------------
# Reviews: writing (a draft in an open cycle)
# ---------------------------------------------------------------------------

def _editable(conn, review_id: int):
    return conn.execute(
        "SELECT r.* FROM rv_reviews r JOIN rv_cycles c ON c.id = r.cycle_id "
        "WHERE r.id=? AND r.status='draft' AND c.status='open'", (review_id,)).fetchone()


def _model_keys(conn, model_id: int) -> list[str]:
    return [r["key"] for r in conn.execute(
        "SELECT key FROM standards_dimensions WHERE model_id=? ORDER BY sort, id", (model_id,))]


def _level(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= people_db.LEVEL_COUNT else None


def save_rating(review_id: int, dimension_key: str, level, not_enough: bool, comment: str,
                by: str) -> bool:
    """A person's own rating for one standard: a level 1 to 4, or "not
    enough to rate yet", with a comment. Only while the review is a draft."""
    level = None if not_enough else _level(level)
    if level is None and not not_enough:
        return False
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        review = _editable(conn, review_id)
        if not review or dimension_key not in _model_keys(conn, review["model_id"]):
            return False
        conn.execute(
            "INSERT INTO rv_ratings (review_id, dimension_key, level, not_enough, comment, rated_at, rated_by) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(review_id, dimension_key) DO UPDATE SET "
            "level=excluded.level, not_enough=excluded.not_enough, comment=excluded.comment, "
            "rated_at=excluded.rated_at, rated_by=excluded.rated_by",
            (review_id, dimension_key, level, 1 if not_enough else 0, _text(comment, CAPS["comment"]),
             shift_db.now_stamp(), _line(by, CAPS["name"])))
        _log(conn, by, "rating saved", "review", review_id, dimension_key)
    return True


def mark_ai_shown(review_id: int, shown: dict) -> None:
    """Record what the AI draft suggested for each standard the first time
    its panel is shown, which is only ever after a person's own rating
    exists. shown: {dimension key: suggested level or None}."""
    now = shift_db.now_stamp()
    with closing(people_db.connect()) as conn, conn:
        for key, level in shown.items():
            conn.execute(
                "UPDATE rv_ratings SET ai_level=?, ai_shown_at=? WHERE review_id=? AND dimension_key=? "
                "AND ai_shown_at IS NULL AND rated_at IS NOT NULL",
                (_level(level), now, review_id, key))


def save_summary(review_id: int, strengths: str, growth: str, by: str) -> bool:
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _editable(conn, review_id):
            return False
        conn.execute("UPDATE rv_reviews SET strengths=?, growth=? WHERE id=? AND status='draft'",
                     (_text(strengths, CAPS["summary"]), _text(growth, CAPS["summary"]), review_id))
        _log(conn, by, "summary saved", "review", review_id)
    return True


def add_action(review_id: int, action: str, measure: str, target: str, check_date: str,
               by: str) -> tuple[int | None, str | None]:
    """An agreed action. Refused unless it has all four parts."""
    action, measure, target = (_line(v, CAPS["action"]) for v in (action, measure, target))
    day = _day(check_date)
    if not action or not measure or not target or not day:
        return None, ACTION_RULE
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _editable(conn, review_id):
            return None, "This review can't be changed right now."
        if conn.execute("SELECT COUNT(*) FROM rv_actions WHERE review_id=?",
                        (review_id,)).fetchone()[0] >= MAX_ACTIONS:
            return None, f"A review holds {MAX_ACTIONS} actions at most. A few clear ones work best."
        action_id = conn.execute(
            "INSERT INTO rv_actions (review_id, action, measure, target, check_date, created_at, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (review_id, action, measure, target, day, shift_db.now_stamp(),
             _line(by, CAPS["name"]))).lastrowid
        _log(conn, by, "action added", "review", review_id, f"action {action_id}")
    return action_id, None


def remove_action(review_id: int, action_id: int, by: str) -> bool:
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _editable(conn, review_id):
            return False
        gone = conn.execute("DELETE FROM rv_actions WHERE id=? AND review_id=?",
                            (action_id, review_id)).rowcount > 0
        if gone:
            _log(conn, by, "action removed", "review", review_id, f"action {action_id}")
    return gone


def mark_action_done(review_id: int, action_id: int, by: str) -> bool:
    """Once a review has been shared, its actions can be marked done."""
    with closing(people_db.connect()) as conn, conn:
        done = conn.execute(
            "UPDATE rv_actions SET done_at=?, done_by=? WHERE id=? AND review_id=? AND done_at IS NULL "
            "AND EXISTS (SELECT 1 FROM rv_reviews r WHERE r.id = rv_actions.review_id "
            "AND r.status IN ('shared','acknowledged'))",
            (shift_db.now_stamp(), _line(by, CAPS["name"]), action_id, review_id)).rowcount > 0
        if done:
            _log(conn, by, "action marked done", "review", review_id, f"action {action_id}")
    return done


def add_time_away(review_id: int, start: str, end: str, by: str) -> str | None:
    """Mark a date range as time away. Dates only. None when saved."""
    first, last = _day(start), _day(end or start)
    if not first or not last:
        return "Choose the first and last day away."
    if first > last:
        return "The last day away can't be before the first."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        review = _editable(conn, review_id)
        if not review:
            return "This review can't be changed right now."
        ranges = time_away(dict(review))
        if (first, last) in ranges:
            return None
        if len(ranges) >= MAX_TIME_AWAY:
            return f"A review holds {MAX_TIME_AWAY} date ranges at most."
        ranges = sorted(ranges + [(first, last)])
        conn.execute("UPDATE rv_reviews SET time_away=? WHERE id=? AND status='draft'",
                     (json.dumps(ranges), review_id))
        _log(conn, by, "time away dates changed", "review", review_id)
    return None


def remove_time_away(review_id: int, start: str, end: str, by: str) -> bool:
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        review = _editable(conn, review_id)
        if not review:
            return False
        ranges = time_away(dict(review))
        if (start, end) not in ranges:
            return False
        ranges.remove((start, end))
        conn.execute("UPDATE rv_reviews SET time_away=? WHERE id=? AND status='draft'",
                     (json.dumps(ranges), review_id))
        _log(conn, by, "time away dates changed", "review", review_id)
    return True


# ---------------------------------------------------------------------------
# The leader's own reflection
# ---------------------------------------------------------------------------

def reflection_open(review: dict) -> bool:
    """The leader can still write: the cycle asks for a reflection, it is
    open, and the reviewer has not finished. (On hold counts as not
    finished, so a hold never shows on the leader's side.)"""
    return bool(review["ask_reflection"]) and review["cycle_status"] == "open" \
        and review["status"] in ("draft", "on_hold")


def save_reflection(review_id: int, bodies: dict, send: bool, by: str) -> str | None:
    """The leader's own words, one box per standard. send=True hands it to
    the reviewer (reflection_at); until then only the leader sees it. None
    when saved, or what to fix."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(_REVIEW + "WHERE r.id=?", (review_id,)).fetchone()
        if not row or not reflection_open(dict(row)):
            return "Your reflection can't be changed any more."
        now = shift_db.now_stamp()
        clean = {key: _text(bodies.get(key), CAPS["reflection"])
                 for key in _model_keys(conn, row["model_id"])}
        if send and not any(clean.values()):
            return "Write something in at least one box first."
        for key, body in clean.items():
            conn.execute(
                "INSERT INTO rv_reflections (review_id, dimension_key, body, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(review_id, dimension_key) DO UPDATE SET body=excluded.body, "
                "updated_at=excluded.updated_at", (review_id, key, body, now))
        if send or row["reflection_at"]:
            conn.execute("UPDATE rv_reviews SET reflection_at=? WHERE id=?", (now, review_id))
        _log(conn, by, "reflection sent" if send else "reflection saved", "review", review_id)
    return None


# ---------------------------------------------------------------------------
# Finishing: the checks, the hold, sharing and the acknowledgement
# ---------------------------------------------------------------------------

def unrated(review: dict, model: dict, rated: dict | None = None) -> list[dict]:
    """The standards that still need a level or "not enough to rate yet"."""
    rated = ratings(review["id"]) if rated is None else rated
    return [dim for dim in model["dimensions"]
            if not (rated.get(dim["key"]) and (rated[dim["key"]]["level"]
                                               or rated[dim["key"]]["not_enough"]))]


def finalise(review_id: int, answers: dict | None, by: str, changed: str | None = None
             ) -> tuple[str | None, str | None]:
    """Finish a draft. Before it can be final, the "before you finish"
    questions are answered: all No -> ("final", None); any "Yes or not sure"
    -> ("on_hold", None) and the review stops for the Operator. After the
    Operator has released a hold the six questions are not asked again (the
    honest answers would hold it forever); the reviewer says whether
    anything has `changed` since. (None, "unrated" | "gate" | "gone") when
    nothing was done."""
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        review = _editable(conn, review_id)
        if not review:
            return None, "gone"
        keys = _model_keys(conn, review["model_id"])
        done = {r["dimension_key"] for r in conn.execute(
            "SELECT dimension_key FROM rv_ratings WHERE review_id=? "
            "AND (level IS NOT NULL OR not_enough=1)", (review_id,))}
        if not keys or any(key not in done for key in keys):
            return None, "unrated"
        if review["released_at"]:
            if changed not in ("no", "yes"):
                return None, "gate"
            gate = gate_answers(dict(review))
            gate["since_release"] = changed
            hold = changed == "yes"
        else:
            answers = answers if isinstance(answers, dict) else {}
            if any(answers.get(key) not in ("no", "yes") for key in GATE_KEYS):
                return None, "gate"
            gate = {"answers": {key: answers[key] for key in GATE_KEYS}, "since_release": None}
            hold = any(value == "yes" for value in gate["answers"].values())
        now, who = shift_db.now_stamp(), _line(by, CAPS["name"])
        if hold:
            moved = conn.execute(
                "UPDATE rv_reviews SET status='on_hold', gate_json=?, gate_at=?, gate_by=? "
                "WHERE id=? AND status='draft'", (json.dumps(gate), now, who, review_id)).rowcount
        else:
            moved = conn.execute(
                "UPDATE rv_reviews SET status='final', gate_json=?, gate_at=?, gate_by=?, "
                "finalised_at=?, finalised_by=? WHERE id=? AND status='draft'",
                (json.dumps(gate), now, who, now, who, review_id)).rowcount
        if not moved:
            return None, "gone"
        # The log says only that it was held, never which answer held it.
        _log(conn, by, "review put on hold" if hold else "review finalised", "review", review_id)
    return ("on_hold" if hold else "final"), None


def release(review_id: int, note_key: str, by: str) -> bool:
    """The Operator releases a hold, back to draft. The note is one of the
    fixed RELEASE_NOTES: what was looked at is never written here."""
    if note_key not in RELEASE_NOTES:
        return False
    with closing(people_db.connect()) as conn, conn:
        moved = conn.execute(
            "UPDATE rv_reviews SET status='draft', hold_note=?, released_at=?, released_by=? "
            "WHERE id=? AND status='on_hold'",
            (RELEASE_NOTES[note_key], shift_db.now_stamp(), _line(by, CAPS["name"]), review_id)).rowcount > 0
        if moved:
            _log(conn, by, "hold released", "review", review_id, note_key)
    return moved


def reopen(review_id: int, by: str) -> bool:
    """The Operator sends a finalised review back to draft (open cycle only)."""
    with closing(people_db.connect()) as conn, conn:
        moved = conn.execute(
            "UPDATE rv_reviews SET status='draft', finalised_at=NULL, finalised_by=NULL "
            "WHERE id=? AND status='final' AND EXISTS (SELECT 1 FROM rv_cycles c "
            "WHERE c.id = rv_reviews.cycle_id AND c.status='open')", (review_id,)).rowcount > 0
        if moved:
            _log(conn, by, "review reopened", "review", review_id)
    return moved


def share(review_id: int, by: str) -> bool:
    with closing(people_db.connect()) as conn, conn:
        moved = conn.execute(
            "UPDATE rv_reviews SET status='shared', shared_at=?, shared_by=? WHERE id=? AND status='final'",
            (shift_db.now_stamp(), _line(by, CAPS["name"]), review_id)).rowcount > 0
        if moved:
            _log(conn, by, "review shared with the leader", "review", review_id)
    return moved


def acknowledge(review_id: int, discussed_on: str, comment: str, by: str) -> str | None:
    """The leader confirms they received the review and when it was
    discussed. Receipt and discussion only: never agreement. None when
    saved, or what to fix."""
    day = _day(discussed_on)
    if not day:
        return "Enter the date the review was discussed with you."
    if day > _today():
        return "That date hasn't happened yet."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(_REVIEW + "WHERE r.id=?", (review_id,)).fetchone()
        if not row or row["status"] != "shared":
            return "This review isn't waiting for you to confirm it."
        if day < row["period_start"]:
            return "That date is before the review period started. Check the date."
        conn.execute(
            "UPDATE rv_reviews SET status='acknowledged', discussed_on=?, ack_at=?, ack_comment=? "
            "WHERE id=? AND status='shared'",
            (day, shift_db.now_stamp(), _text(comment, CAPS["ack_comment"]), review_id))
        _log(conn, by, "review acknowledged", "review", review_id)
    return None


def calibrate(review_id: int, dimension_key: str, level, not_enough: bool, reason: str,
              by: str) -> str | None:
    """The Operator changes one level in calibration. Only on a finalised
    review, and only with a reason. cal_from keeps the level the reviewer
    gave (NULL when that was "not enough to rate yet"). None when saved."""
    reason = _text(reason, CAPS["cal_reason"])
    level = None if not_enough else _level(level)
    if level is None and not not_enough:
        return "Choose the new level."
    if not reason:
        return "Give a reason for the change. It is kept with the review."
    with closing(people_db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute("SELECT * FROM rv_reviews WHERE id=? AND status='final'",
                              (review_id,)).fetchone()
        if not review:
            return "Levels can be changed in calibration only while a review is finalised and not yet shared."
        row = conn.execute("SELECT * FROM rv_ratings WHERE review_id=? AND dimension_key=?",
                           (review_id, dimension_key)).fetchone()
        if not row:
            return "That standard isn't rated on this review."
        if row["level"] == level and bool(row["not_enough"]) == bool(not_enough):
            return "That is already the level. Nothing was changed."
        now, who = shift_db.now_stamp(), _line(by, CAPS["name"])
        if row["cal_at"]:                       # changed before: keep the reviewer's own level
            conn.execute(
                "UPDATE rv_ratings SET level=?, not_enough=?, cal_reason=?, cal_at=?, cal_by=? WHERE id=?",
                (level, 1 if not_enough else 0, reason, now, who, row["id"]))
        else:
            conn.execute(
                "UPDATE rv_ratings SET level=?, not_enough=?, cal_from=?, cal_reason=?, cal_at=?, cal_by=? "
                "WHERE id=?", (level, 1 if not_enough else 0, row["level"], reason, now, who, row["id"]))
        _log(conn, by, "level changed in calibration", "review", review_id,
             f"{dimension_key}: {row['level'] or 'not enough'} to {level or 'not enough'}")
    return None


# ---------------------------------------------------------------------------
# What each person has waiting
# ---------------------------------------------------------------------------

def reviews_for_reviewer(leader_id, name) -> list[dict]:
    """Reviews assigned to this reviewer (id AND name), never their own."""
    if not leader_id or not name:
        return []
    with closing(people_db.connect()) as conn:
        rows = [_shape(r) for r in conn.execute(
            _REVIEW + "WHERE r.reviewer_leader_id=? "
            "ORDER BY (c.status='open') DESC, c.due_date DESC, s.display_name COLLATE NOCASE",
            (leader_id,))]
    return [r for r in rows
            if people_db.same_person(leader_id, name, r["reviewer_leader_id"], r["reviewer_name"])
            and not is_self(r, leader_id, name)]


def subjects_for_reviewer(leader_id, name) -> list[dict]:
    if not leader_id or not name:
        return []
    return [s for s in subjects()
            if people_db.same_person(leader_id, name, s["reviewer_leader_id"], s["reviewer_name"])
            and not is_self(s, leader_id, name)]


def visible_to_leader(review: dict) -> bool:
    """Is there anything for the leader to open: a shared review, or a
    reflection the cycle asked for."""
    if review["status"] in ("shared", "acknowledged"):
        return True
    return bool(review["ask_reflection"]) and (reflection_open(review) or bool(review["reflection_at"]))


def reviews_for_leader(leader_id, name) -> list[dict]:
    """A leader's own reviews (the row's leader id AND name both match)
    that have something for them to open."""
    if not leader_id or not name:
        return []
    with closing(people_db.connect()) as conn:
        rows = [_shape(r) for r in conn.execute(
            _REVIEW + "WHERE s.leader_id=? ORDER BY c.period_end DESC, r.id DESC", (leader_id,))]
    return [r for r in rows
            if people_db.same_person(leader_id, name, r["leader_id"], r["leader_name"])
            and visible_to_leader(r)]


def waiting_for_leader(review: dict) -> bool:
    """Shared and not yet acknowledged, or a reflection not yet sent."""
    return review["status"] == "shared" or (reflection_open(review) and not review["reflection_at"])


def nav_counts(kind: str, leader_id, name) -> dict:
    """Counts for the Team Hub menu and the People home page. The Operator:
    on_hold, past_due, drafts (their own to write). A leader: drafts (the
    reviews they are assigned) and mine (their own review waiting on them)."""
    today = _today()
    open_draft = ("FROM rv_reviews r JOIN rv_cycles c ON c.id = r.cycle_id "
                  "WHERE c.status='open' AND r.status='draft' ")
    with closing(people_db.connect()) as conn:
        if kind == "operator":
            return {
                "on_hold": conn.execute(
                    "SELECT COUNT(*) FROM rv_reviews r JOIN rv_cycles c ON c.id = r.cycle_id "
                    "WHERE c.status='open' AND r.status='on_hold'").fetchone()[0],
                "past_due": conn.execute("SELECT COUNT(*) " + open_draft + "AND c.due_date < ?",
                                         (today,)).fetchone()[0],
                "drafts": conn.execute("SELECT COUNT(*) " + open_draft
                                       + "AND r.reviewer_leader_id IS NULL").fetchone()[0],
            }
    drafts = sum(1 for r in reviews_for_reviewer(leader_id, name) if r["editable"])
    mine = sum(1 for r in reviews_for_leader(leader_id, name) if waiting_for_leader(r))
    return {"drafts": drafts, "mine": mine}


# ---------------------------------------------------------------------------
# Calibration and process health (the Operator)
# ---------------------------------------------------------------------------

def _evidence_counts(review: dict, keys: list[str]) -> dict[str, int]:
    counts = {key: 0 for key in keys}
    for item in evidence(review):
        if item["dimension_key"] in counts:
            counts[item["dimension_key"]] += 1
    return counts


def calibration(cycle_id: int) -> dict | None:
    """One cycle laid out for calibration: leaders alphabetically (never by
    level) against the standards, how each reviewer spread their levels,
    and how many leaders sit at each level of each standard. Only finalised
    reviews show ratings. No totals or averages per person, no ranking."""
    cycle = get_cycle(cycle_id)
    if not cycle:
        return None
    model = people_db.get_model(cycle["model_id"]) if cycle["model_id"] else None
    keys = [d["key"] for d in model["dimensions"]] if model else []
    rows = []
    by_reviewer: dict[str, dict] = {}
    team = {key: {"levels": [0] * people_db.LEVEL_COUNT, "not_enough": 0} for key in keys}
    for review in cycle_reviews(cycle_id):
        row = {"review": review, "reviewer": review["reviewer_name"] or "Operator", "cells": {}}
        if review["done"] and review["model_id"] == cycle["model_id"]:
            rated = ratings(review["id"])
            counts = _evidence_counts(review, keys)
            levels = []
            for key in keys:
                r = rated.get(key)
                if not r:
                    continue
                row["cells"][key] = {"level": r["level"], "not_enough": bool(r["not_enough"]),
                                     "evidence": counts[key], "changed": bool(r["cal_at"])}
                if r["level"]:
                    levels.append(r["level"])
                    team[key]["levels"][r["level"] - 1] += 1
                elif r["not_enough"]:
                    team[key]["not_enough"] += 1
            spread = by_reviewer.setdefault(row["reviewer"], {
                "name": row["reviewer"], "levels": [0] * people_db.LEVEL_COUNT, "not_enough": 0,
                "reviews": 0, "flat_reviews": 0, "flat_of": 0})
            spread["reviews"] += 1
            spread["not_enough"] += sum(1 for c in row["cells"].values() if c["not_enough"])
            for level in levels:
                spread["levels"][level - 1] += 1
            if len(levels) >= 3:                 # too few standards says nothing about sameness
                spread["flat_of"] += 1
                most = max(levels.count(n) for n in set(levels))
                if most / len(levels) >= 0.8:
                    spread["flat_reviews"] += 1
        rows.append(row)
    reviewers = []
    for spread in sorted(by_reviewer.values(), key=lambda s: s["name"].casefold()):
        given = sum(spread["levels"])
        top_two = spread["levels"][-1] + spread["levels"][-2]
        spread["given"] = given
        spread["top_two"] = top_two
        spread["mostly_top"] = given >= 4 and top_two / given >= 0.8
        spread["mostly_flat"] = spread["flat_of"] >= 1 and spread["flat_reviews"] / spread["flat_of"] >= 0.8
        reviewers.append(spread)
    return {"cycle": cycle, "model": model, "rows": rows, "reviewers": reviewers,
            "team": [{"dim": dim, **team[dim["key"]],
                      "total": sum(team[dim["key"]]["levels"]) + team[dim["key"]]["not_enough"]}
                     for dim in (model["dimensions"] if model else [])]}


def _minutes_between(earlier: str | None, later: str | None) -> float | None:
    try:
        a = datetime.strptime(earlier or "", "%Y-%m-%d %H:%M")
        b = datetime.strptime(later or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return (b - a).total_seconds() / 60


QUICK_MINUTES = 10


def health(cycle_id: int) -> dict | None:
    """Counts that show how well the review process itself is being run for
    one cycle. Every figure is a count; the page puts the count beside any
    percentage. Ratings are counted on finalised reviews."""
    cycle = get_cycle(cycle_id)
    if not cycle:
        return None
    reviews = cycle_reviews(cycle_id)
    done = [r for r in reviews if r["done"]]
    out = {
        "cycle": cycle, "total": len(reviews), "finalised": len(done),
        "on_time": sum(1 for r in done if (r["finalised_at"] or "")[:10] <= cycle["due_date"]),
        "levels": [0] * people_db.LEVEL_COUNT, "not_enough": 0, "ratings": 0,
        "rated": 0, "evidence_items": 0, "no_evidence": 0,
        "ai_shown": 0, "ai_same": 0, "ai_not_enough": 0, "quick": 0,
        "cal_up": 0, "cal_down": 0, "cal_other": 0,
        "actions_due": 0, "actions_on_time": 0, "actions": 0,
    }
    today = _today()
    for review in done:
        rated = ratings(review["id"])
        counts = _evidence_counts(review, list(rated))
        for key, r in rated.items():
            if not (r["level"] or r["not_enough"]):
                continue
            out["ratings"] += 1
            if r["level"]:
                out["levels"][r["level"] - 1] += 1
                out["rated"] += 1
                out["evidence_items"] += counts.get(key, 0)
                out["no_evidence"] += 0 if counts.get(key, 0) else 1
            else:
                out["not_enough"] += 1
            if r["ai_shown_at"]:
                out["ai_shown"] += 1
                if r["ai_level"] is None:
                    out["ai_not_enough"] += 1
                # The reviewer's own level, before any calibration change.
                own = r["cal_from"] if r["cal_at"] else r["level"]
                if own == r["ai_level"]:
                    out["ai_same"] += 1
            gap = _minutes_between(r["rated_at"], review["finalised_at"])
            if gap is not None and 0 <= gap <= QUICK_MINUTES:
                out["quick"] += 1
            if r["cal_at"]:
                if r["cal_from"] and r["level"] and r["level"] > r["cal_from"]:
                    out["cal_up"] += 1
                elif r["cal_from"] and r["level"] and r["level"] < r["cal_from"]:
                    out["cal_down"] += 1
                elif r["cal_from"] != r["level"]:
                    out["cal_other"] += 1
        for action in actions(review["id"]):
            out["actions"] += 1
            if action["check_date"] <= today:
                out["actions_due"] += 1
                if action["done_at"] and action["done_at"][:10] <= action["check_date"]:
                    out["actions_on_time"] += 1
    return out
