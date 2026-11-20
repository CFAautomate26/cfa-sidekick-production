"""Shared set-up for the People tests (core, reviews, hiring).

A plain module, not a conftest: import it and call make_world() from a
fixture. Everyone in it is invented.

    @pytest.fixture()
    def world(monkeypatch, tmp_path):
        return kit.make_world(monkeypatch, tmp_path)
"""

import json

import app as cfa_app
import hub
import hub_db
import people_db
import shift_app
import shift_db

PINS = {"operator": "9999", "lena": "1111", "ada": "2222", "hari": "3333", "rae": "4444"}
NAMES = {"operator": "Operator", "lena": "Lena", "ada": "Ada", "hari": "Hari", "rae": "Rae"}
MEMBER = ("Sam Patel", "1042")


def make_world(monkeypatch, tmp_path) -> dict:
    """A fresh store: a new shift database and a new People database beside
    it, the sign-in throttles cleared, and one of each kind of person.

    The Operator (PIN 9999); leaders Lena (lead, 1111), Ada (admin role,
    2222), Hari (lead, 3333, granted "hiring") and Rae (lead, 4444, granted
    "reviewer"); roster member Sam Patel with Hub code 1042; and the neutral
    example standards saved as the active model.

    Returns {"client": a test client, "ids": {lena, ada, hari, rae, member,
    model}, "pins": {...}}."""
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "shift.db"))
    monkeypatch.setenv("SHIFT_HUB_FILES_DIR", str(tmp_path / "hub_files"))
    monkeypatch.delenv("PEOPLE_DB_PATH", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)      # no real AI calls: see fake_ai
    monkeypatch.delenv("PEOPLE_AI_MODEL", raising=False)
    shift_db.init_db()
    people_db.init_db()
    shift_app._pin_fails.clear()
    hub._fails.clear()
    monkeypatch.setattr(shift_app, "SHIFT_ADMIN_PIN", PINS["operator"])
    cfa_app.app.config["TESTING"] = True
    for who, role in (("lena", "lead"), ("ada", "admin"), ("hari", "lead"), ("rae", "lead")):
        assert shift_db.add_leader(NAMES[who], PINS[who], role) is None
    by_name = {l["name"]: l["id"] for l in shift_db.leaders()}
    ids = {who: by_name[NAMES[who]] for who in ("lena", "ada", "hari", "rae")}
    assert people_db.grant(ids["hari"], NAMES["hari"], "hiring", "Operator")
    assert people_db.grant(ids["rae"], NAMES["rae"], "reviewer", "Operator")
    shift_db.add_member(MEMBER[0])
    member = next(m for m in shift_db.roster() if m["name"] == MEMBER[0])
    assert hub_db.grant(member["id"], MEMBER[1], "Operator") is None
    ids["member"] = member["id"]
    ids["model"], problems = people_db.save_model(people_db.PEOPLE_EXAMPLE_MODEL, "Operator")
    assert not problems
    return {"client": cfa_app.app.test_client(), "ids": ids, "pins": dict(PINS)}


def sign_in(client, who: str) -> None:
    """who: "operator", "lena", "ada", "hari", "rae" or "member"."""
    if who == "member":
        resp = client.post("/hub/login", data={"name": MEMBER[0].split()[0], "code": MEMBER[1]})
    else:
        resp = client.post("/shift/login", data={"name": NAMES[who], "pin": PINS[who]})
    assert resp.status_code == 302, (who, resp.status_code)


def unlock(client, who: str) -> None:
    """Enter this person's PIN on the People lock screen."""
    resp = client.post("/shift/people/unlock", data={"pin": PINS[who]})
    assert resp.status_code == 302 and "/unlock" not in resp.headers["Location"], who


def open_as(world, who: str):
    """A fresh client, signed in and (for anyone but the team member, who
    has no PIN) past the People lock."""
    client = cfa_app.app.test_client()
    sign_in(client, who)
    if who != "member":
        unlock(client, who)
    return client


def fake_ai(monkeypatch, responder) -> list:
    """Replace the one function that talks to the AI service. `responder`
    is called with (system, user) and returns the reply (a dict is sent
    back as JSON, a string as it is; it may raise) or is itself that reply.
    Also makes the AI look configured (the switches in Settings still have
    to be on). Returns the list of (system, user) calls, filled as they
    happen."""
    import people_ai

    calls: list = []

    def complete(system, user):
        calls.append((system, user))
        reply = responder(system, user) if callable(responder) else responder
        return reply if isinstance(reply, str) else json.dumps(reply)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(people_ai, "_complete", complete)
    return calls
