"""Shared set-up for the leader-review tests (no tests of its own).

Builds on tests/people_testkit.py: Lena, Ada, Hari and Rae are invented
leaders, Rae holds "reviewer". Dates are worked out from today so the
tests read the same on any day."""

import json
from datetime import timedelta

import people_db
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit

KEYS = ("reliable", "guest-care", "teamwork")           # the neutral example standards
NO = {key: "no" for key in rdb.GATE_KEYS}


def day(offset: int = 0) -> str:
    """Today's calendar date plus `offset` days, as YYYY-MM-DD."""
    return (shift_db.now_local().date() + timedelta(days=offset)).isoformat()


def add_subject(world, who: str = "lena", reviewer: str | None = "rae", **extra) -> int:
    """Put a Team Hub leader under review, linked by id and name.
    reviewer=None means the Operator reviews them."""
    ids = world["ids"]
    args = dict(leader_id=ids[who], leader_name=kit.NAMES[who])
    if reviewer:
        args.update(reviewer_leader_id=ids[reviewer], reviewer_name=kit.NAMES[reviewer])
    args.update(extra)
    name = args.pop("display_name", kit.NAMES[who])
    subject_id, problem = rdb.add_subject(name, "Operator", **args)
    assert problem is None, problem
    return subject_id


def add_cycle(name: str = "Autumn check-in", start: int = -60, end: int = 0, due: int = 14,
              ask_reflection: bool = True) -> int:
    cycle_id, problem = rdb.create_cycle(name, day(start), day(end), day(due), ask_reflection,
                                         "Operator")
    assert problem is None, problem
    return cycle_id


def review_of(cycle_id: int, subject_id: int) -> int:
    return next(r["id"] for r in rdb.cycle_reviews(cycle_id) if r["subject_id"] == subject_id)


def observe(subject_id: int, body: str, offset: int = -3, key: str | None = "reliable",
            kind: str = "note", by: str = "Rae", by_id: int | None = None) -> int:
    obs_id, problem = rdb.add_observation(subject_id, day(offset), key, kind, body, by, by_id)
    assert problem is None, problem
    return obs_id


def rate_all(review_id: int, level: int = 3, by: str = "Rae") -> None:
    for key in KEYS:
        assert rdb.save_rating(review_id, key, level, False, "Seen on shift.", by)


def finalise(review_id: int, by: str = "Rae", **answers) -> str | None:
    """Rate nothing; just answer the gate (all No unless told otherwise)."""
    result, problem = rdb.finalise(review_id, {**NO, **answers}, by)
    assert problem is None, problem
    return result


def one_review(world, who: str = "lena", reviewer: str | None = "rae", **cycle) -> dict:
    """A leader under review with one draft review in a fresh open cycle.
    {"subject": id, "cycle": id, "review": id}"""
    subject_id = add_subject(world, who, reviewer)
    cycle_id = add_cycle(**cycle)
    return {"subject": subject_id, "cycle": cycle_id, "review": review_of(cycle_id, subject_id)}


def shared_review(world, who: str = "lena", reviewer: str | None = "rae") -> dict:
    made = one_review(world, who, reviewer)
    rate_all(made["review"])
    assert finalise(made["review"]) == "final"
    assert rdb.share(made["review"], "Rae")
    return made


def audit_actions() -> list[tuple]:
    """(who, action, object_type, object_id) newest first."""
    return [(r["who"], r["action"], r["object_type"], r["object_id"]) for r in people_db.audit_feed()]


def queue_job(review_id: int, status: str = "queued", output: dict | None = None,
              error: str | None = None) -> None:
    """Put an AI job row straight into the store (a draft "still running",
    or one that finished) without calling anything."""
    with people_db.connect() as conn:
        conn.execute(
            "INSERT INTO ai_jobs (kind, target_id, status, requested_by, requested_at, output_json, error) "
            "VALUES ('review_draft', ?, ?, 'Rae', ?, ?, ?)",
            (review_id, status, shift_db.now_stamp(), json.dumps(output) if output else None, error))
        conn.commit()
