"""People: one test (or a few) for each thing the three checks of the
finished section found after the review fixes (A = the attack on those
fixes, C = the compliance and documentation check, B = the run in a real
browser). Each is named for the finding it pins and failed before its fix.
What only a real browser shows is pinned by its cause: the header, the
attribute, the style rule or the markup. No real AI service is ever
called. Everyone here is invented."""

import json
import re
import threading
import time

import pytest

import app as cfa_app
import careers
import people
import people_ai
import people_db
import people_hiring
import people_hiring_db as hdb
import people_reviews
import people_reviews_db as rdb
import shift_app
import shift_db
from tests import people_testkit as kit
from tests import test_people_reviews_kit as rk
from tests.test_people_hiring_db import (ai_in_use, applied, clock, criterion, draft, live, live_with_ai,
                                         person, table)

page, post = rk.page, rk.post
UNLOCK = "/shift/people/unlock"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    made = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    careers.reset_throttles()
    return made


@pytest.fixture()
def public():
    """Someone who has never signed in to anything."""
    return cfa_app.app.test_client()


def text(resp) -> str:
    return resp.data.decode()


def flashed(client) -> list[str]:
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


# --- A-V1: the limit on wrong PINs holds when the guesses arrive together -------------------------

def slow_counting_verify(monkeypatch) -> list:
    """Stand in for the leader PIN check: as slow as the real hash, and it
    records every PIN that really reached it."""
    checked: list[str] = []
    real = shift_db.verify_leader

    def verify(name, pin):
        checked.append(pin)
        time.sleep(0.15)
        return real(name, pin)

    monkeypatch.setattr(shift_db, "verify_leader", verify)
    return checked


def burst(n: int, send) -> list:
    """Call send(i) from n threads released at the same moment."""
    gate, out = threading.Barrier(n), [None] * n

    def one(i):
        gate.wait()
        out[i] = send(i)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_va1_fifty_wrong_pins_at_the_lock_at_once_and_only_five_are_ever_checked(world, monkeypatch):
    signed_in = world["client"]
    kit.sign_in(signed_in, "rae")
    cookie = signed_in.get_cookie("session").value
    checked = slow_counting_verify(monkeypatch)

    def guess(i):
        client = cfa_app.app.test_client()
        client.set_cookie("session", cookie)
        client.post(UNLOCK, data={"unlock_code": f"{5000 + i}"})
        return flashed(client)[-1]

    said = burst(50, guess)
    assert len(checked) == people.UNLOCK_MAX_MISSES == 5
    assert said.count("Wrong PIN.") == 5
    assert said.count("Too many wrong PINs. Try again in 10 minutes.") == 45
    assert [r["action"] for r in people_db.audit_feed()].count("wrong PIN at the lock") == 5
    assert [r["action"] for r in people_db.audit_feed()].count("lock held after wrong PINs") == 1
    # held now: the right PIN is not even checked
    signed_in.post(UNLOCK, data={"unlock_code": world["pins"]["rae"]})
    assert len(checked) == 5 and signed_in.get("/shift/people").status_code == 302


def test_va1_the_right_pin_inside_a_burst_does_not_get_a_free_check(world, monkeypatch):
    signed_in = world["client"]
    kit.sign_in(signed_in, "rae")
    cookie = signed_in.get_cookie("session").value
    checked = slow_counting_verify(monkeypatch)
    pins = [f"{6000 + i}" for i in range(29)] + [world["pins"]["rae"]]

    def guess(i):
        client = cfa_app.app.test_client()
        client.set_cookie("session", cookie)
        return client.post(UNLOCK, data={"unlock_code": pins[i]}).headers["Location"]

    burst(30, guess)
    assert len(checked) <= 5                     # the right one is checked only if it was among the first five


def test_va1_the_count_is_in_the_people_store_not_in_this_process(world):
    client = world["client"]
    kit.sign_in(client, "lena")
    mine = f"l:{world['ids']['lena']}"
    for _ in range(5):
        client.post(UNLOCK, data={"unlock_code": "0000"})
    assert not hasattr(people, "_unlock_fails")                      # nothing a restart could clear
    with people_db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pin_tries")]
    assert len(rows) == 5 and {r["key"] for r in rows} == {mine}
    assert "0000" not in json.dumps(rows)                            # never the PIN itself
    assert "pin_tries" not in json.loads(people_db.export_json("Operator"))["tables"]
    # another server process (same store) refuses the sixth without checking it
    assert people_db.pin_try(mine, people._now(), people.UNLOCK_MAX_MISSES, people.UNLOCK_HOLD_SECONDS) == 0
    # ten minutes later the count has run out
    later = people._now() + people.UNLOCK_HOLD_SECONDS + 1
    assert people_db.pin_try(mine, later, people.UNLOCK_MAX_MISSES, people.UNLOCK_HOLD_SECONDS) == 1


def test_va1_a_request_that_read_the_clock_a_second_early_is_still_refused(world):
    now = people._now()
    for nth in range(1, 6):
        assert people_db.pin_try("op", now + 1, 5, 600) == nth
    assert people_db.pin_try("op", now, 5, 600) == 0      # it sees the five counted "after" it
    assert people_db.pin_try("l:7", now, 5, 600) == 1     # someone else's count is their own


def test_va1_fifty_wrong_pins_at_the_sign_in_form_at_once_and_only_five_are_checked(world, monkeypatch):
    checked = slow_counting_verify(monkeypatch)

    def guess(i):
        resp = cfa_app.app.test_client().post("/shift/login", data={"name": "Rae", "pin": f"{7000 + i}"})
        return ("held" if b"Too many wrong PINs" in resp.data else "wrong", resp.status_code)

    said = burst(50, guess)
    assert len(checked) == 5
    assert said.count(("wrong", 401)) == 5 and said.count(("held", 401)) == 45
    # and the Operator's PIN, which is a fast compare, the same way
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", "9999")
    tried = burst(40, lambda i: cfa_app.app.test_client().post(
        "/shift/login", data={"name": "Operator", "pin": f"{8000 + i}"}).data)
    assert sum(b"Wrong PIN." in page_ for page_ in tried) == 5
    assert sum(b"Too many wrong PINs" in page_ for page_ in tried) == 35


def test_va1_sign_in_still_clears_the_count_on_the_right_pin(world):
    client = cfa_app.app.test_client()
    for _ in range(4):
        assert client.post("/shift/login", data={"name": "Rae", "pin": "0000"}).status_code == 401
    assert len(shift_app._pin_fails["rae"]) == 4
    assert client.post("/shift/login", data={"name": "Rae", "pin": world["pins"]["rae"]}).status_code == 302
    assert "rae" not in shift_app._pin_fails
