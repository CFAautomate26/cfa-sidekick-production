"""The People section's platform: its separate store, who is let in, the
PIN re-entry lock, settings, "Our standards", the activity log, the
backup, and how it sits in the Team Hub menu without ever being able to
break it. Everyone here is invented."""

import copy
import io
import json
import os
import re

import pytest

import app as cfa_app
import people_db
import shift_app
import shift_db
from tests import people_testkit as kit


@pytest.fixture()
def world(monkeypatch, tmp_path):
    return kit.make_world(monkeypatch, tmp_path)


def example():
    return copy.deepcopy(people_db.PEOPLE_EXAMPLE_MODEL)


# --- the store ----------------------------------------------------------------

def test_people_live_in_their_own_file(world, tmp_path, monkeypatch):
    assert people_db.db_path() == str(tmp_path / "people.db")
    assert os.path.abspath(people_db.db_path()) != os.path.abspath(shift_db.DB_PATH)
    assert os.path.exists(people_db.db_path())
    # the path follows the shift database on every call, or its own setting
    monkeypatch.setattr(shift_db, "DB_PATH", str(tmp_path / "elsewhere" / "shift.db"))
    assert people_db.db_path() == str(tmp_path / "elsewhere" / "people.db")
    monkeypatch.setenv("PEOPLE_DB_PATH", str(tmp_path / "own.db"))
    assert people_db.db_path() == str(tmp_path / "own.db")


def test_nothing_of_it_rides_in_the_shift_backup(world):
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    backup = shift_db.export_json()
    with people_db.connect() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    payload = json.loads(backup)
    assert "access_grants" in tables and "standards_models" in tables
    for table in tables:
        if table != "meta":                      # the shift database has its own meta
            assert table not in payload, table
            assert table not in shift_db.EXPORT_TABLES
    assert "Morgan Example" not in backup and "Hari" in backup    # leaders are; grants are not


def test_set_up_is_repeatable_and_one_broken_feature_store_is_skipped(world, monkeypatch, capsys):
    import people_hiring_db
    import people_reviews_db

    def broken(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS rv_half_made (id INTEGER PRIMARY KEY)")
        raise RuntimeError("bad schema")

    def fine(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS hr_probe (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(people_reviews_db, "init_schema", broken)
    monkeypatch.setattr(people_hiring_db, "init_schema", fine)
    people_db.init_db()
    people_db.init_db()
    out = capsys.readouterr().out
    assert "WARNING: People store people_reviews_db failed" in out
    with people_db.connect() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hr_probe" in tables and "access_grants" in tables
    assert people_db.active_model()["version"] == 1                # nothing was lost


def test_a_store_that_was_never_set_up_is_made_on_the_first_people_page(world, tmp_path, monkeypatch):
    """The app sets the store up at boot. A path that changed afterwards
    (another test's fresh database) must never be a missing-table error."""
    fresh = tmp_path / "fresh" / "people.db"
    fresh.parent.mkdir()
    monkeypatch.setenv("PEOPLE_DB_PATH", str(fresh))
    assert not fresh.exists()
    lena = world["client"]
    kit.sign_in(lena, "lena")
    assert lena.get("/shift/").status_code == 200                  # the menu shrugs it off
    resp = lena.get("/shift/people")
    assert resp.status_code == 302 and "/shift/people/unlock" in resp.headers["Location"]
    assert fresh.exists() and people_db.capabilities(world["ids"]["hari"], "Hari") == frozenset()
    kit.unlock(lena, "lena")
    assert lena.get("/shift/people").status_code == 200
    fresh.unlink()                                                 # even a file that vanished
    for extra in (str(fresh) + "-wal", str(fresh) + "-shm"):
        if os.path.exists(extra):
            os.unlink(extra)
    # the store comes back empty, so the lock (which is kept in it) asks for the PIN again: never an error
    resp = lena.get("/shift/people")
    assert resp.status_code == 302 and "/shift/people/unlock" in resp.headers["Location"]
    kit.unlock(lena, "lena")
    assert lena.get("/shift/people").status_code == 200


# --- settings -----------------------------------------------------------------

def test_settings_have_defaults_and_reject_unknown_keys(world):
    assert people_db.get_setting("lock_minutes") == "30"
    assert people_db.get_setting("employer_name") == "Chick-fil-A Wharncliffe & Wonderland"
    assert people_db.get_setting("min_wage_general") == "17.95"
    assert people_db.get_setting("min_wage_student") == "16.90"
    assert people_db.get_setting("quick_link_days") == "7"
    assert people_db.get_setting("retention_months") == "24"
    assert not people_db.setting_on("ai_reviews") and not people_db.setting_on("ai_hiring")
    with pytest.raises(KeyError):
        people_db.get_setting("made_up")
    assert people_db.set_setting("made_up", "1", "Operator") is False
    assert set(people_db.settings_all()) == set(people_db.SETTINGS)


@pytest.mark.parametrize("key,value,stored", [
    ("ai_reviews", "1", "1"), ("ai_reviews", "on", "1"), ("ai_hiring", "0", "0"),
    ("lock_minutes", "45", "45"), ("lock_minutes", "1", "5"), ("lock_minutes", "999", "120"),
    ("employer_name", "  Example   Restaurant ", "Example Restaurant"),
    ("hiring_contact_email", "hiring@example.com", "hiring@example.com"),
    ("hiring_contact_phone", "519-555-0100", "519-555-0100"),
    ("hiring_contact_phone", "", ""),
    ("min_wage_general", "18", "18.00"), ("min_wage_student", "17.5", "17.50"),
    ("quick_link_days", "10", "10"), ("retention_months", "36", "36"),
])
def test_settings_accept_and_tidy_good_values(world, key, value, stored):
    assert people_db.set_setting(key, value, "Operator") is True
    assert people_db.get_setting(key) == stored


@pytest.mark.parametrize("key,value", [
    ("ai_reviews", "maybe"), ("lock_minutes", "soon"), ("lock_minutes", "-5"),
    ("employer_name", "   "), ("hiring_contact_email", "not-an-email"),
    ("hiring_contact_phone", "12345"), ("hiring_contact_phone", "call me maybe"),
    ("min_wage_general", "lots"), ("min_wage_general", "0"), ("min_wage_student", "-3"),
    ("quick_link_days", "0"), ("quick_link_days", "31"),
    ("retention_months", "6"), ("retention_months", "abc"),
])
def test_settings_refuse_bad_values_and_keep_the_old_one(world, key, value):
    before = people_db.get_setting(key)
    assert people_db.set_setting(key, value, "Operator") is False
    assert people_db.get_setting(key) == before


def test_setting_changes_are_logged_without_contact_details(world):
    people_db.set_setting("ai_reviews", "1", "Operator")
    people_db.set_setting("ai_reviews", "1", "Operator")          # no change: no second row
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "morgan@example.com", "Operator")
    rows = [r for r in people_db.audit_feed() if r["action"] == "setting changed"]
    assert [r["detail"] for r in rows] == ["hiring_contact_email", "hiring_contact_name",
                                           "ai_reviews = 1"]
    assert "Morgan" not in json.dumps(rows) and "example.com" not in json.dumps(rows)
    info = people_db.settings_all()
    assert info["ai_reviews"]["updated_by"] == "Operator" and info["ai_reviews"]["updated_at"]
    assert info["lock_minutes"]["updated_by"] is None


# --- who has access -----------------------------------------------------------

def test_grants_need_the_id_and_the_name_to_agree(world):
    hari = world["ids"]["hari"]
    assert people_db.capabilities(hari, "Hari") == frozenset({"hiring"})
    assert people_db.capabilities(hari, "hari") == frozenset({"hiring"})      # casefold
    # the right id under another name, or the right name on another id: nothing
    assert people_db.capabilities(hari, "Rae") == frozenset()
    assert people_db.capabilities(world["ids"]["rae"], "Hari") == frozenset()
    assert people_db.capabilities(hari + 100, "Hari") == frozenset()
    assert people_db.capabilities(None, "Hari") == frozenset()
    assert people_db.capabilities(hari, "") == frozenset()
    # the role alone gives nothing
    assert people_db.capabilities(world["ids"]["ada"], "Ada") == frozenset()


def test_grant_and_revoke(world):
    lena = world["ids"]["lena"]
    assert people_db.grant(lena, "Lena", "owner", "Operator") is False        # not a capability
    assert people_db.grant(0, "Lena", "hiring", "Operator") is False
    assert people_db.grant(lena, "  ", "hiring", "Operator") is False
    assert people_db.grant(lena, "Lena", "reviewer", "Operator") is True
    assert people_db.grant(lena, "Lena", "reviewer", "Operator") is False     # already there
    assert people_db.grant(lena, "Lena", "hiring", "Operator") is True
    assert people_db.capabilities(lena, "Lena") == frozenset({"hiring", "reviewer"})
    mine = [g for g in people_db.grants() if g["leader_id"] == lena]
    assert {g["capability"] for g in mine} == {"hiring", "reviewer"}
    assert all(g["granted_by"] == "Operator" and g["granted_at"] for g in mine)
    assert people_db.revoke(mine[0]["id"], "Operator") is True
    assert people_db.revoke(mine[0]["id"], "Operator") is False
    assert len(people_db.capabilities(lena, "Lena")) == 1
    actions = [(r["action"], r["object_type"], r["object_id"], r["detail"])
               for r in people_db.audit_feed() if r["object_id"] == lena]
    assert ("access given", "leader", lena, "reviewer") in actions
    assert ("access removed", "leader", lena, mine[0]["capability"]) in actions


def test_a_renumbered_login_never_inherits_someone_elses_grant(world):
    """After a Team Hub restore, Hari's old id could belong to someone else."""
    hari = world["ids"]["hari"]
    assert people_db.capabilities(hari, "Jordan") == frozenset()
    # the Operator grants the person now holding that id: the stale row goes
    assert people_db.grant(hari, "Jordan", "hiring", "Operator") is True
    assert people_db.capabilities(hari, "Jordan") == frozenset({"hiring"})
    assert people_db.capabilities(hari, "Hari") == frozenset()
    assert people_db.same_person(3, "Lena", 3, "LENA")
    assert not people_db.same_person(3, "Lena", 4, "Lena")
    assert not people_db.same_person(3, "Lena", 3, "Ada")
    assert not people_db.same_person(None, "Lena", None, "Lena")


# --- private links ------------------------------------------------------------

def test_tokens_are_random_and_stored_only_as_a_keyed_hash(world):
    a, b = people_db.new_token(), people_db.new_token()
    assert re.fullmatch(r"[0-9a-f]{32}", a) and a != b
    assert people_db.token_hash(a) == people_db.token_hash(a) != people_db.token_hash(b)
    assert re.fullmatch(r"[0-9a-f]{64}", people_db.token_hash(a)) and a not in people_db.token_hash(a)
    with people_db.connect() as conn:
        pepper = conn.execute("SELECT value FROM meta WHERE key='token_pepper'").fetchone()["value"]
        conn.execute("DELETE FROM meta")
        conn.commit()
    assert people_db.token_hash(a)                              # a new pepper is made and kept
    assert people_db.token_hash(a) == people_db.token_hash(a)
    assert pepper not in people_db.export_json("Operator")


# --- the backup ---------------------------------------------------------------

def test_backup_holds_every_table_but_meta_and_is_logged(world):
    with people_db.connect() as conn:
        conn.execute("CREATE TABLE rv_probe (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO rv_probe (note) VALUES ('kept')")
        conn.commit()
    payload = json.loads(people_db.export_json("Operator"))
    assert payload["format"] == 1 and payload["exported_at"]
    tables = payload["tables"]
    assert "meta" not in tables
    for name in ("settings", "access_grants", "audit", "standards_models",
                 "standards_dimensions", "ai_jobs", "rv_probe"):
        assert name in tables, name
    assert tables["rv_probe"] == [{"id": 1, "note": "kept"}]
    assert {g["leader_name"] for g in tables["access_grants"]} == {"Hari", "Rae"}
    assert len(tables["standards_dimensions"]) == 3
    assert tables["audit"][-1]["action"] == "backup downloaded"   # the copy records itself
    assert people_db.audit_feed(limit=1)[0]["who"] == "Operator"


# --- the activity log ---------------------------------------------------------

def test_activity_log_is_newest_first_and_keeps_details_short(world):
    for n in range(5):
        people_db.audit("Rae", "review opened", "review", n, "x" * 500)
    feed = people_db.audit_feed(limit=3)
    assert [r["object_id"] for r in feed] == [4, 3, 2]
    assert [r["object_id"] for r in people_db.audit_feed(limit=2, offset=3)] == [1, 0]
    assert len(feed[0]["detail"]) == 200 and feed[0]["at"] == shift_db.now_stamp()
    people_db.audit("Rae", "odd id", "review", "not-a-number")
    assert people_db.audit_feed(limit=1)[0]["object_id"] is None


# --- Our standards: the model ---------------------------------------------------

def test_the_example_model_is_valid_and_reads_back_in_the_agreed_shape(world):
    assert people_db.validate_model(people_db.PEOPLE_EXAMPLE_MODEL) == []
    model = people_db.active_model()
    assert model["id"] == world["ids"]["model"] and model["version"] == 1
    assert model["name"] == "Example standards" and len(model["levels"]) == 4
    assert [g["name"] for g in model["groups"]] == ["How we work", "How we work together"]
    assert [d["key"] for d in model["dimensions"]] == ["reliable", "guest-care", "teamwork"]
    first = model["dimensions"][0]
    assert set(first) >= {"key", "group", "name", "definition", "anchors", "evidence_examples", "hiring"}
    assert first["group"] == "How we work" and len(first["anchors"]) == 4
    assert first["hiring"]["question"] and len(first["hiring"]["anchors"]) == 4
    assert model["groups"][0]["dimensions"][0] == first
    assert json.loads(people_db.model_export(model["id"])) == people_db.PEOPLE_EXAMPLE_MODEL
    assert people_db.get_model(9999) is None and people_db.model_export(9999) == ""


def test_every_save_is_a_new_version_and_old_ones_keep_their_wording(world):
    first = world["ids"]["model"]
    data = example()
    data["groups"][0]["dimensions"][0]["definition"] = "Shows up ready and follows through."
    del data["groups"][1]["dimensions"][0]["hiring"]
    second, problems = people_db.save_model(data, "Operator")
    assert problems == [] and second != first
    assert people_db.active_model()["id"] == second and people_db.active_model()["version"] == 2
    old = people_db.get_model(first)
    assert old["version"] == 1 and not old["active"]
    assert old["dimensions"][0]["definition"].startswith("Arrives on time")
    assert people_db.active_model()["dimensions"][2]["hiring"] is None
    versions = people_db.model_versions()
    assert [(v["version"], v["active"]) for v in versions] == [(2, 1), (1, 0)]
    saved = [r for r in people_db.audit_feed() if r["action"] == "standards saved"]
    assert saved[0]["object_id"] == second and saved[0]["detail"] == "version 2"


def _broken(change):
    data = example()
    change(data)
    return people_db.validate_model(data)


def test_model_problems_are_said_in_plain_words(world):
    dim = lambda d: d["groups"][0]["dimensions"][0]          # noqa: E731
    assert "one JSON object" in people_db.validate_model(["not", "a", "model"])[0]
    assert any('"format": 1' in p for p in _broken(lambda d: d.pop("format")))
    assert any("name" in p for p in _broken(lambda d: d.update(name="")))
    assert any("exactly four level names" in p for p in _broken(lambda d: d.update(levels=["a", "b"])))
    assert any("same name" in p for p in _broken(lambda d: d.update(levels=["a", "A", "b", "c"])))
    assert any("between 1 and 8 groups" in p for p in _broken(lambda d: d.update(groups=[])))
    assert any("between 1 and 8 groups" in p
               for p in _broken(lambda d: d.update(groups=d["groups"] * 5)))
    assert any("no standards" in p for p in _broken(lambda d: d["groups"][0].update(dimensions=[])))
    assert any("small letters" in p for p in _broken(lambda d: dim(d).update(key="Guest Care")))
    assert any("small letters" in p for p in _broken(lambda d: dim(d).update(key="x")))
    assert any("more than once" in p for p in _broken(lambda d: dim(d).update(key="teamwork")))
    assert any("definition" in p for p in _broken(lambda d: dim(d).update(definition="")))
    assert any("definition" in p for p in _broken(lambda d: dim(d).update(definition="x" * 1201)))
    assert any("needs a name" in p for p in _broken(lambda d: dim(d).update(name="x" * 81)))
    assert any("exactly four" in p for p in _broken(lambda d: dim(d).update(anchors=["a", "b", "c"])))
    assert any("level 2" in p for p in _broken(lambda d: dim(d)["anchors"].__setitem__(1, "x" * 701)))
    assert any("evidence examples" in p
               for p in _broken(lambda d: dim(d).update(evidence_examples="x" * 701)))
    assert any("hiring question" in p
               for p in _broken(lambda d: dim(d)["hiring"].update(question="")))
    assert any("hiring question" in p and "exactly four" in p
               for p in _broken(lambda d: dim(d)["hiring"].update(anchors=["only one"])))


def test_a_model_holds_thirty_standards_at_most(world):
    def many(count):
        data = example()
        base = data["groups"][0]["dimensions"][0]
        data["groups"] = [{"name": "Everything", "dimensions": [
            dict(base, key=f"standard-{n}", name=f"Standard {n}") for n in range(count)]}]
        return data
    assert people_db.validate_model(many(30)) == []
    assert any("30 standards at most" in p for p in people_db.validate_model(many(31)))


def test_a_file_with_problems_changes_nothing(world):
    data = example()
    data["levels"] = ["only", "three", "levels"]
    model_id, problems = people_db.save_model(data, "Operator")
    assert model_id is None and problems
    assert people_db.active_model()["id"] == world["ids"]["model"]
    assert len(people_db.model_versions()) == 1


def test_model_text_is_cleaned_on_the_way_in(world):
    data = example()
    data["name"] = "  Our‮   standards\x00 "
    data["groups"][0]["dimensions"][0]["definition"] = "Line one\r\nline two\x07"
    model_id, problems = people_db.save_model(data, "Operator")
    assert problems == []
    model = people_db.get_model(model_id)
    assert model["name"] == "Our standards"
    assert model["dimensions"][0]["definition"] == "Line one\nline two"


# =============================================================================
# The pages: the gate, the lock, who gets what
# =============================================================================

import people  # noqa: E402

CORE_PAGES = ("/shift/people", "/shift/people/access", "/shift/people/settings",
              "/shift/people/standards", "/shift/people/audit", "/shift/people/backup")
OPERATOR_ONLY = ("/shift/people/access", "/shift/people/settings", "/shift/people/audit",
                 "/shift/people/backup")
UNLOCK = "/shift/people/unlock"


def page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.status_code, resp.headers.get("Location"))
    return resp.data.decode()


def flashed(client):
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


def lock_tries(identity: str) -> int:
    """PINs counted at the lock for this sign-in ("op" or "l:<leader id>"):
    kept in the People store, so they survive a restart and are shared by
    every server process."""
    return people_db.pin_tries(identity, people._now(), people.UNLOCK_HOLD_SECONDS)


def test_people_is_registered_on_a_fresh_boot():
    assert "people" in cfa_app.app.blueprints and "careers" in cfa_app.app.blueprints
    assert people.FEATURES == {"reviews", "hiring"}
    rules = {r.rule for r in cfa_app.app.url_map.iter_rules() if r.endpoint.startswith("people.")}
    assert set(CORE_PAGES) <= rules and UNLOCK in rules and "/shift/people/lock" in rules


def test_a_feature_imported_ahead_of_people_is_asked_again_not_written_off(monkeypatch, capsys):
    """A test (or a script) may import people_reviews before the app; that
    module then imports people while it is itself only half-built."""
    import sys
    import types
    calls = []
    half = types.ModuleType("people_probe")
    half.__spec__ = types.SimpleNamespace(_initializing=True)
    monkeypatch.setitem(sys.modules, "people_probe", half)
    monkeypatch.setattr(people, "_FEATURE_NAMES", people._FEATURE_NAMES + (("probe", "people_probe"),))
    monkeypatch.setattr(people, "FEATURES", set(people.FEATURES))
    monkeypatch.setattr(people, "_settled", set(people._settled))
    monkeypatch.setattr(people, "_FEATURE_MODULES", dict(people._FEATURE_MODULES))
    people.load_features()
    assert "probe" not in people.FEATURES and capsys.readouterr().out == ""    # waiting, not failed
    half.register = lambda bp: calls.append(bp)
    half.__spec__._initializing = False
    people.load_features()
    people.load_features()                                   # once only
    assert "probe" in people.FEATURES and calls == [people.people_bp]
    # one that really is broken is skipped with a warning, and never retried
    broken = types.ModuleType("people_broken")
    broken.register = lambda bp: 1 / 0
    monkeypatch.setitem(sys.modules, "people_broken", broken)
    monkeypatch.setattr(people, "_FEATURE_NAMES", people._FEATURE_NAMES + (("broken", "people_broken"),
                                                                           ("gone", "people_gone")))
    people.load_features()
    people.load_features()
    out = capsys.readouterr().out
    assert out.count("WARNING: People feature broken failed to load") == 1
    assert out.count("WARNING: People feature gone failed to load") == 1
    assert not {"broken", "gone"} & people.FEATURES and {"reviews", "hiring"} <= people.FEATURES


# --- the gate, in order ---------------------------------------------------------

def test_signed_out_lands_on_the_front_door(world):
    client = world["client"]
    for path in CORE_PAGES + (UNLOCK,):
        resp = client.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path
    resp = client.get("/shift/people/settings")
    assert "next=/shift/people/settings" in resp.headers["Location"]      # back here after sign-in
    for path in (UNLOCK, "/shift/people/lock", "/shift/people/settings", "/shift/people/backup"):
        resp = client.post(path, data={"pin": "9999"})
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path


def test_a_team_member_is_sent_back_to_the_hub(world):
    client = world["client"]
    kit.sign_in(client, "member")
    for path in CORE_PAGES + (UNLOCK,):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/"), path
    assert "That part of Team Hub is for leaders." in flashed(client)
    resp = client.post(UNLOCK, data={"pin": "1042"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/")
    with client.session_transaction() as sess:
        assert "people_unlock_at" not in sess


def test_an_idle_shared_device_and_a_removed_login_go_to_the_front_door(world):
    client = world["client"]
    client.post("/shift/login", data={"name": "Operator", "pin": "9999", "shared": "1"})
    kit.unlock(client, "operator")
    with client.session_transaction() as sess:
        sess["seen_at"] -= 31 * 60
    resp = client.get("/shift/people")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    hari = kit.open_as(world, "hari")
    assert hari.get("/shift/people").status_code == 200
    shift_db.set_leader_active(world["ids"]["hari"], False)
    resp = hari.get("/shift/people")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


@pytest.mark.parametrize("who", ["operator", "lena", "hari"])
def test_pages_stay_locked_until_the_pin_is_entered_again(world, who):
    client = world["client"]
    kit.sign_in(client, who)
    for path in CORE_PAGES:
        resp = client.get(path)
        assert resp.status_code == 302, path
        assert resp.headers["Location"] == f"{UNLOCK}?next={path}", path
    # a form post while locked is never carried out
    resp = client.post("/shift/people/settings", data={"lock_minutes": "99"})
    assert resp.status_code == 302 and resp.headers["Location"] == UNLOCK
    # (rewritten after the browser run, finding B5: the message now says the form was not saved)
    assert ("People pages locked themselves before that was saved, so it was not saved. "
            "Enter your PIN and try again.") in flashed(client)
    assert people_db.get_setting("lock_minutes") == "30"
    # ... and after the PIN they land back on the page the form was on
    resp = client.post("/shift/people/settings", data={"lock_minutes": "99"},
                       headers={"Referer": "http://localhost/shift/people/settings"})
    assert resp.headers["Location"] == UNLOCK + "?next=/shift/people/settings"
    resp = client.post("/shift/people/settings", data={"lock_minutes": "99"},
                       headers={"Referer": "https://evil.example/shift/people/settings"})
    assert resp.headers["Location"] == UNLOCK
    lock_page = page(client, UNLOCK + "?next=/shift/people/standards")
    assert "Enter your PIN to open People pages" in lock_page
    assert "These pages hold private information about people, so they lock after 30 minutes." in lock_page
    # one box for the PIN: not a password box and not called "pin", so a browser that saved the
    # sign-in PIN does not fill it in; the digits are hidden by the page's own style instead
    assert lock_page.count('type="password"') == 0 and 'name="pin"' not in lock_page
    assert lock_page.count('name="unlock_code"') == 1 and 'autocomplete="one-time-code"' in lock_page
    assert "-webkit-text-security: disc" in lock_page and 'class="pp-pin"' in lock_page
    assert 'name="next" value="/shift/people/standards"' in lock_page
    resp = client.post(UNLOCK, data={"pin": world["pins"][who], "next": "/shift/people/standards"})
    assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people/standards"
    assert client.get("/shift/people").status_code == 200
    assert client.get("/shift/people/").status_code == 200          # with or without the slash
    # once open, the lock page just sends you on
    assert client.get(UNLOCK).headers["Location"] == "/shift/people"
    assert [r["who"] for r in people_db.audit_feed() if r["action"] == "unlocked"] == [kit.NAMES[who]]


def test_a_wrong_pin_stays_locked_and_five_of_them_hold_the_lock(world, capsys):
    client = world["client"]
    kit.sign_in(client, "lena")
    mine = f"l:{world['ids']['lena']}"
    for attempt in range(5):
        resp = client.post(UNLOCK, data={"unlock_code": "0000", "next": "/shift/people/standards"})
        assert resp.status_code == 302
        assert resp.headers["Location"] == UNLOCK + "?next=/shift/people/standards"
        assert flashed(client)[-1] == "Wrong PIN."
        assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    assert lock_tries(mine) == 5
    # held now: even the right PIN is refused at the lock, under either field name
    for field in ("unlock_code", "pin"):
        client.post(UNLOCK, data={field: "1111"})
        assert flashed(client)[-1] == "Too many wrong PINs. Try again in 10 minutes."
        assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    # the lock keeps its own count: it is not the sign-in lockout, in either direction
    assert "lena" not in shift_app._pin_fails
    other = cfa_app.app.test_client()
    assert other.post("/shift/login", data={"name": "Lena", "pin": "1111"}).status_code == 302
    assert [r["who"] for r in people_db.audit_feed() if r["action"] == "lock held after wrong PINs"] == ["Lena"]
    wrong = [r for r in people_db.audit_feed() if r["action"] == "wrong PIN at the lock"]
    assert len(wrong) == 5 and {r["who"] for r in wrong} == {"Lena"}
    assert "0000" not in json.dumps(people_db.audit_feed())           # never the PIN itself
    out = capsys.readouterr().out
    assert "0000" not in out and "1111" not in out


def test_the_right_pin_clears_earlier_misses_and_an_empty_one_is_not_a_miss(world):
    client = world["client"]
    kit.sign_in(client, "rae")
    mine = f"l:{world['ids']['rae']}"
    client.post(UNLOCK, data={"pin": ""})
    assert flashed(client)[-1] == "Enter your PIN."
    assert lock_tries(mine) == 0
    client.post(UNLOCK, data={"pin": "1"})
    client.post(UNLOCK, data={"pin": "22"})
    assert lock_tries(mine) == 2
    kit.unlock(client, "rae")
    assert lock_tries(mine) == 0 and "rae" not in shift_app._pin_fails


def test_only_your_own_pin_opens_the_lock(world, monkeypatch):
    client = world["client"]
    kit.sign_in(client, "lena")
    for someone_elses in (world["pins"]["ada"], world["pins"]["operator"], "PÏN"):
        client.post(UNLOCK, data={"pin": someone_elses})
        assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    # a leader whose id no longer matches their name's login never unlocks
    with client.session_transaction() as sess:
        sess["shift_leader_id"] = world["ids"]["ada"]             # Lena's name on Ada's id
    client.post(UNLOCK, data={"pin": "1111"})
    assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    # the Operator's lock opens with the master PIN only, never a leader's
    operator = cfa_app.app.test_client()
    kit.sign_in(operator, "operator")
    operator.post(UNLOCK, data={"pin": "1111"})
    assert operator.get("/shift/people").status_code == 302
    kit.unlock(operator, "operator")
    assert operator.get("/shift/people").status_code == 200


def test_an_unlock_belongs_to_one_sign_in(world):
    client = kit.open_as(world, "operator")
    with client.session_transaction() as sess:
        assert sess["people_unlock_for"] == "op" and isinstance(sess["people_unlock_at"], int)
        stamp = sess["people_unlock_at"]
    lena = kit.open_as(world, "lena")
    with lena.session_transaction() as sess:
        assert sess["people_unlock_for"] == f"l:{world['ids']['lena']}"
        sess["people_unlock_for"] = "op"                          # someone else's unlock
        sess["people_unlock_at"] = stamp
    assert lena.get("/shift/people").headers["Location"].startswith(UNLOCK)


@pytest.mark.parametrize("target,lands", [
    ("/shift/people/settings", "/shift/people/settings"),
    ("/shift/reviews/review/3?tab=2", "/shift/reviews/review/3?tab=2"),
    ("/shift/hiring", "/shift/hiring"),
    ("https://evil.example/shift/people", "/shift/people"),
    ("//evil.example/shift/people", "/shift/people"),
    ("/shift/people//evil.example", "/shift/people"),
    ("/shift/people\\evil", "/shift/people"),
    ("/shift/people/\x00x", "/shift/people"),
    ("/shift/people\nSet-Cookie: x", "/shift/people"),
    ("/shift/peoplex", "/shift/people"),
    ("/shift/admin", "/shift/people"),
    ("/hub/people", "/shift/people"),
    ("/shift/people/unlock?next=/shift/people/unlock", "/shift/people"),
    ("", "/shift/people"),
])
def test_next_only_ever_leads_back_into_people_pages(world, target, lands):
    client = world["client"]
    kit.sign_in(client, "operator")
    resp = client.post(UNLOCK, data={"pin": "9999", "next": target})
    assert resp.status_code == 302 and resp.headers["Location"] == lands
    assert 'name="next"' not in cfa_app.app.test_client().get(UNLOCK).data.decode()


def test_the_lock_comes_back_after_the_idle_time(world):
    client = kit.open_as(world, "operator")
    with client.session_transaction() as sess:
        sess["people_unlock_at"] -= 20 * 60
        earlier = sess["people_unlock_at"]
    assert client.get("/shift/people").status_code == 200          # inside 30 minutes
    with client.session_transaction() as sess:
        assert sess["people_unlock_at"] > earlier                  # every page refreshes it
        sess["people_unlock_at"] -= 31 * 60
    resp = client.get("/shift/people/settings")
    assert resp.headers["Location"] == UNLOCK + "?next=/shift/people/settings"
    # the Operator can set a longer or shorter time
    kit.unlock(client, "operator")
    people_db.set_setting("lock_minutes", "45", "Operator")
    with client.session_transaction() as sess:
        sess["people_unlock_at"] -= 31 * 60
    assert client.get("/shift/people").status_code == 200
    people_db.set_setting("lock_minutes", "5", "Operator")
    with client.session_transaction() as sess:
        sess["people_unlock_at"] -= 6 * 60
    assert client.get("/shift/people").status_code == 302
    assert "lock after 5 minutes" in page(client, UNLOCK)


def test_lock_now(world):
    client = kit.open_as(world, "hari")
    assert "Lock now" in page(client, "/shift/people")
    resp = client.post("/shift/people/lock")
    assert resp.status_code == 302 and resp.headers["Location"] == "/shift/"
    with client.session_transaction() as sess:
        assert "people_unlock_at" not in sess and "people_unlock_for" not in sess
        assert sess["shift_name"] == "Hari"                        # still signed in to Team Hub
    assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    assert client.get("/shift/").status_code == 200


@pytest.mark.parametrize("headers,accepted", [
    ({}, True),
    ({"Origin": "http://localhost"}, True),
    ({"Sec-Fetch-Site": "same-origin"}, True),
    ({"Sec-Fetch-Site": "none"}, True),
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "http://localhost.evil.example"}, False),
    ({"Origin": "null"}, False),
    ({"Sec-Fetch-Site": "cross-site"}, False),
    ({"Sec-Fetch-Site": "same-site"}, False),
    ({"Origin": "http://localhost", "Sec-Fetch-Site": "cross-site"}, False),
])
def test_a_form_post_from_another_site_is_refused(world, headers, accepted):
    client = kit.open_as(world, "operator")
    resp = client.post("/shift/people/settings", data={"lock_minutes": "60"}, headers=headers)
    assert resp.status_code == 302
    if accepted:
        assert people_db.get_setting("lock_minutes") == "60"
    else:
        assert resp.headers["Location"] == "/shift/people"
        assert "That didn't come from Team Hub. Please try again." in flashed(client)
        assert people_db.get_setting("lock_minutes") == "30"


def test_another_site_cannot_open_the_lock_either(world):
    client = world["client"]
    kit.sign_in(client, "operator")
    client.post(UNLOCK, data={"pin": "9999"}, headers={"Origin": "https://evil.example"})
    assert client.get("/shift/people").headers["Location"].startswith(UNLOCK)
    assert "operator" not in shift_app._pin_fails and lock_tries("op") == 0


# --- who gets what ------------------------------------------------------------

@pytest.mark.parametrize("who,allowed", [
    ("operator", set(CORE_PAGES)),
    ("hari", {"/shift/people", "/shift/people/standards"}),
    ("rae", {"/shift/people", "/shift/people/standards"}),
    ("lena", {"/shift/people"}),
    ("ada", {"/shift/people"}),              # the admin role alone opens nothing
])
def test_core_pages_for_each_person(world, who, allowed):
    client = kit.open_as(world, who)
    for path in CORE_PAGES:
        resp = client.get(path)
        if path in allowed:
            assert resp.status_code == 200, (who, path)
        else:
            assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", (who, path)
            assert flashed(client)[-1] == "That page isn't available on your sign-in."


def test_an_admin_leader_without_a_grant_gets_nothing(world):
    client = kit.open_as(world, "ada")
    home = page(client, "/shift/people")
    assert "Nothing here for your sign-in right now." in home
    for link in OPERATOR_ONLY + ("/shift/people/standards",):
        assert f'href="{link}"' not in home
    for path, data in (("/shift/people/access/grant",
                        {"leader_id": world["ids"]["ada"], "capability": "hiring"}),
                       ("/shift/people/settings", {"ai_reviews": "1"}),
                       ("/shift/people/standards/export", {}),
                       ("/shift/people/standards/example", {}),
                       ("/shift/people/backup", {})):
        resp = client.post(path, data=data)
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", path
    assert people_db.capabilities(world["ids"]["ada"], "Ada") == frozenset()
    assert not people_db.setting_on("ai_reviews")
    assert not [r for r in people_db.audit_feed() if r["action"] == "backup downloaded"]


def test_home_lists_only_what_each_person_can_open(world, monkeypatch):
    import people_hiring
    import people_reviews
    monkeypatch.setattr(people, "_page_url", lambda path: path + "/")    # as if the features had pages
    operator = page(kit.open_as(world, "operator"), "/shift/people")
    for label in ("Leader reviews", "Hiring", "Who has access", "Our standards", "Settings",
                  "Activity log", "Backup", "Lock now"):
        assert label in operator, label
    assert "My review" not in operator
    hari = page(kit.open_as(world, "hari"), "/shift/people")
    assert 'href="/shift/hiring/"' in hari and "/shift/reviews" not in hari
    assert "Our standards" in hari and "Who has access" not in hari and "Backup" not in hari
    rae = page(kit.open_as(world, "rae"), "/shift/people")
    assert 'href="/shift/reviews/"' in rae and "/shift/hiring" not in rae
    lena = kit.open_as(world, "lena")
    assert "/shift/reviews" not in page(lena, "/shift/people")
    # "My review" appears only when the reviews feature says something is waiting
    monkeypatch.setattr(people_reviews, "nav_counts",
                        lambda kind, leader_id, name: {"mine": 1, "drafts": 4, "on_hold": 2})
    monkeypatch.setattr(people_hiring, "nav_counts",
                        lambda kind, leader_id, name: {"new": 3, "notices_overdue": 1})
    home = page(lena, "/shift/people")
    assert 'href="/shift/reviews/mine/"' in home and "My review" in home
    assert "to finish" not in home and "on hold" not in home and "3 new" not in home
    operator = page(kit.open_as(world, "operator"), "/shift/people")
    assert "2 on hold" in operator and "3 new" in operator and "1 notice overdue" in operator


def test_home_without_feature_pages_links_nowhere_that_is_not_there(world, monkeypatch):
    """A link must never point at a page that isn't there: when a feature
    has no page (it did not load), People home offers nothing for it."""
    with cfa_app.app.test_request_context("/shift/people"):
        assert people._page_url("/shift/nothing-here") is None       # no such page, so no link
        for path in ("/shift/reviews", "/shift/hiring", "/shift/reviews/mine"):
            assert (people._page_url(path) or "").rstrip("/") == path, path   # the real pages
    monkeypatch.setattr(people, "_page_url", lambda path: None)      # as if neither feature loaded
    for who in ("operator", "hari", "rae", "lena"):
        home = page(kit.open_as(world, who), "/shift/people")
        assert "/shift/reviews" not in home and "/shift/hiring" not in home, who


def test_view_helpers_for_the_feature_pages(world):
    @people.requires("hiring")
    def hiring_only():
        return "the page"

    def as_viewer(caps, kind="leader"):
        from flask import g
        g.people_viewer = people.Viewer(kind, None if kind == "operator" else 7, "Hari", "lead",
                                        frozenset(caps))

    with cfa_app.app.test_request_context("/shift/hiring/"):
        as_viewer({"hiring"})
        assert people.viewer().name == "Hari" and people.actor() == "Hari"
        assert people.can("hiring") and not people.can("reviewer") and not people.can("operator")
        assert not people.viewer().is_operator
        assert hiring_only() == "the page"
        as_viewer({"reviewer"})
        resp = hiring_only()
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people"
        resp = people.deny()
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people"
        as_viewer(people.OPERATOR_CAPS, "operator")
        assert people.viewer().is_operator and people.can("hiring") and people.can("reviewer")
        resp = people._id_out_of_range(OverflowError())
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people"


# --- who has access (the Operator's page) ---------------------------------------

def test_the_operator_grants_and_removes_access(world):
    client = kit.open_as(world, "operator")
    lena = world["ids"]["lena"]
    before = page(client, "/shift/people/access")
    for name in ("Lena", "Ada", "Hari", "Rae"):
        assert name in before
    assert before.count("Can open") == 2                          # Hari: hiring, Rae: reviews
    assert "Being an admin leader opens neither one" in before
    resp = client.post("/shift/people/access/grant",
                       data={"leader_id": lena, "capability": "reviewer", "leader_name": "Ada"})
    assert resp.status_code == 302 and resp.headers["Location"] == f"/shift/people/access#l{lena}"
    assert people_db.capabilities(lena, "Lena") == frozenset({"reviewer"})    # her own row's name
    assert people_db.capabilities(lena, "Ada") == frozenset()
    assert page(client, "/shift/people/access").count("Can open") == 3
    grant = next(g for g in people_db.grants() if g["leader_id"] == lena)
    assert grant["granted_by"] == "Operator"
    assert client.post("/shift/people/access/revoke", data={"grant_id": grant["id"]}).status_code == 302
    assert people_db.capabilities(lena, "Lena") == frozenset()
    for bad in ({"leader_id": 9999, "capability": "hiring"}, {"leader_id": lena, "capability": "owner"},
                {"leader_id": "x", "capability": "hiring"}, {}):
        assert client.post("/shift/people/access/grant", data=bad).status_code == 302
    assert client.post("/shift/people/access/revoke", data={"grant_id": "nope"}).status_code == 302
    assert people_db.capabilities(lena, "Lena") == frozenset()
    # access taken away stops on the leader's next tap
    hari = kit.open_as(world, "hari")
    assert hari.get("/shift/people/standards").status_code == 200
    held = next(g for g in people_db.grants() if g["leader_name"] == "Hari")
    client.post("/shift/people/access/revoke", data={"grant_id": held["id"]})
    assert hari.get("/shift/people/standards").status_code == 302


def test_a_grant_that_no_longer_matches_a_sign_in_opens_nothing(world):
    """A Team Hub restore can hand a leader id to someone else."""
    hari = world["ids"]["hari"]
    with shift_db.connect() as conn:
        conn.execute("UPDATE leaders SET name='Jordan' WHERE id=?", (hari,))
        conn.commit()
    client = cfa_app.app.test_client()
    assert client.post("/shift/login", data={"name": "Jordan", "pin": "3333"}).status_code == 302
    client.post(UNLOCK, data={"pin": "3333"})
    home = page(client, "/shift/people")
    assert "Nothing here for your sign-in right now." in home
    assert client.get("/shift/people/standards").status_code == 302
    access = page(kit.open_as(world, "operator"), "/shift/people/access")
    assert "No longer matching a sign-in" in access
    assert access.count("Can open") == 1                          # only Rae's still counts


# --- settings (the Operator's page) ---------------------------------------------

def test_settings_page_saves_checks_and_explains(world):
    client = kit.open_as(world, "operator")
    html = page(client, "/shift/people/settings")
    for key in ("employer_name", "hiring_contact_name", "hiring_contact_email",
                "hiring_contact_phone", "min_wage_general", "min_wage_student",
                "quick_link_days", "retention_months", "lock_minutes"):
        assert f'name="{key}"' in html, key
    assert 'value="17.95"' in html and 'value="16.90"' in html
    assert "check the current rate on ontario.ca" in html
    resp = client.post("/shift/people/settings", data={
        "employer_name": "Example Restaurant", "hiring_contact_name": "Morgan Example",
        "hiring_contact_email": "hiring@example.com", "hiring_contact_phone": ""})
    assert resp.status_code == 302 and flashed(client)[-1] == "Settings saved."
    assert people_db.get_setting("hiring_contact_name") == "Morgan Example"
    html = page(client, "/shift/people/settings")
    assert 'value="Morgan Example"' in html and 'value="hiring@example.com"' in html
    # one bad value is named and left alone; the good ones still save
    client.post("/shift/people/settings", data={"min_wage_general": "lots", "min_wage_student": "17",
                                                "quick_link_days": "5", "retention_months": "3"})
    said = flashed(client)
    assert "The general minimum wage needs a dollar amount like 17.95." in said
    assert "Months to keep applications must be a whole number from 12 to 120." in said
    assert "Everything else was saved." in said
    assert people_db.get_setting("min_wage_general") == "17.95"
    assert people_db.get_setting("min_wage_student") == "17.00"
    assert people_db.get_setting("quick_link_days") == "5"
    assert people_db.get_setting("retention_months") == "24"


def test_each_ai_switch_sits_under_its_checklist_and_shows_who_switched_it(world, monkeypatch):
    client = kit.open_as(world, "operator")
    html = page(client, "/shift/people/settings")
    assert "No AI key is set up on the server" in html
    assert html.count("Before you switch it on, these should be true") == 2
    for line in ("Leaders have been told in writing that an AI tool helps prepare a draft",
                 "every standard is about behaviour at work",
                 "A first review cycle has been run without AI.",
                 "Nothing from your Team Hub activity is used.",
                 "Every posting that uses AI says so in the posting itself",
                 "a person makes every decision"):
        assert line in html, line
    assert html.index("A first review cycle has been run without AI.") \
        < html.index("Switch on AI for leader reviews")
    assert "Switch on AI for hiring" in html and "Last switched by" not in html
    client.post("/shift/people/settings", data={"ai_reviews": "1"})
    assert people_db.setting_on("ai_reviews") and not people_db.setting_on("ai_hiring")
    html = page(client, "/shift/people/settings")
    assert "Switch off AI for leader reviews" in html and "Last switched by Operator" in html
    client.post("/shift/people/settings", data={"ai_reviews": "0"})
    assert not people_db.setting_on("ai_reviews")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert "No AI key is set up" not in page(client, "/shift/people/settings")


# --- Our standards (the page) ---------------------------------------------------

def upload(client, data, name="standards.json"):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return client.post("/shift/people/standards/import",
                       data={"file": (io.BytesIO(raw), name)}, content_type="multipart/form-data")


def test_standards_page_shows_the_model_with_levels_folded_away(world):
    html = page(kit.open_as(world, "operator"), "/shift/people/standards")
    assert "Example standards" in html and "version 1" in html
    for text in ("How we work", "How we work together", "Reliable", "Guest care", "Teamwork",
                 "Learning", "Role model", "What each level looks like", "Hiring question",
                 "Others plan around them."):
        assert text in html, text
    assert html.count("<details") >= 3 and "Change this wording" in html
    assert "Load a standards file" in html and "See an example file" in html


def test_with_no_model_the_page_says_what_one_is(world, tmp_path, monkeypatch):
    monkeypatch.setenv("PEOPLE_DB_PATH", str(tmp_path / "empty-people.db"))
    people_db.init_db()
    client = kit.open_as(world, "operator")
    html = page(client, "/shift/people/standards")
    assert "No standards are loaded yet." in html
    assert "Reviews and hiring scorecards need a model before they can start." in html
    assert "Start with Our standards." in page(client, "/shift/people")
    assert client.post("/shift/people/standards/export").status_code == 302


def test_loading_a_file_makes_a_new_version_and_bad_files_change_nothing(world):
    client = kit.open_as(world, "operator")
    data = example()
    data["name"] = "Second go"
    resp = upload(client, data)
    assert resp.status_code == 302 and flashed(client)[-1] == "Standards loaded as version 2."
    assert people_db.active_model()["name"] == "Second go"
    # every problem is listed, in plain words, and nothing is saved
    data["levels"] = ["one", "two"]
    data["groups"][0]["dimensions"][0]["key"] = "Not A Key"
    data["groups"][0]["dimensions"][1]["anchors"] = ["just one"]
    resp = upload(client, data)
    assert resp.status_code == 400
    html = resp.data.decode()
    assert "Nothing was changed." in html
    for problem in ("List exactly four level names, lowest first.", "small letters, numbers or dashes",
                    "exactly four level descriptions"):
        assert problem in html, problem
    for raw in (b"this is not json", b"\xff\xfe\x00bad", b"[1, 2, 3]", b"[" * 5000):
        resp = upload(client, raw)
        assert resp.status_code == 400 and "Nothing was changed." in resp.data.decode()
    resp = upload(client, b" " * (people_db.MODEL_MAX_BYTES + 1))
    assert resp.status_code == 400 and "256 KB at most" in resp.data.decode()
    resp = upload(client, b" " * (people_db.MODEL_MAX_BYTES + 200_000))     # past the request limit
    assert resp.status_code == 302 and "256 KB at most" in flashed(client)[-1]
    assert client.post("/shift/people/standards/import", data={}).status_code == 302
    assert people_db.active_model()["version"] == 2 and len(people_db.model_versions()) == 2


def test_downloads_are_attachments_in_the_import_format(world):
    client = kit.open_as(world, "operator")
    resp = client.post("/shift/people/standards/export")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="our-standards-v1.json"'
    assert json.loads(resp.data) == people_db.PEOPLE_EXAMPLE_MODEL
    assert upload(client, resp.data).status_code == 302            # what comes out goes back in
    resp = client.post("/shift/people/standards/example")
    assert "example-standards.json" in resp.headers["Content-Disposition"]
    assert people_db.validate_model(json.loads(resp.data)) == []


def test_changing_one_standard_saves_a_new_version(world):
    client = kit.open_as(world, "operator")
    model = people_db.active_model()
    form = {"model_id": model["id"], "key": "teamwork", "name": "Working together",
            "definition": "Helps teammates and shares what they know.",
            "anchor1": "One", "anchor2": "Two", "anchor3": "Three", "anchor4": "Four",
            "evidence_examples": "Dated notes.", "hiring_question": "",
            "hiring_anchor1": "", "hiring_anchor2": "", "hiring_anchor3": "", "hiring_anchor4": ""}
    resp = client.post("/shift/people/standards/edit", data=form)
    assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people/standards#s-teamwork"
    assert flashed(client)[-1] == "Saved as version 2."
    new = people_db.active_model()
    dim = next(d for d in new["dimensions"] if d["key"] == "teamwork")
    assert new["version"] == 2 and dim["name"] == "Working together"
    assert dim["anchors"] == ["One", "Two", "Three", "Four"] and dim["hiring"] is None
    assert [d["key"] for d in new["dimensions"]] == ["reliable", "guest-care", "teamwork"]
    assert new["dimensions"][0] == dict(model["dimensions"][0])    # the others are untouched
    assert people_db.get_model(model["id"])["dimensions"][2]["name"] == "Teamwork"
    # a form opened before the change is refused, not applied to the new version
    resp = client.post("/shift/people/standards/edit", data=form)
    assert resp.status_code == 302 and "changed while that form was open" in flashed(client)[-1]
    # problems come back with what was typed still in the boxes
    form.update(model_id=new["id"], anchor3="", definition="Kept while fixing.")
    resp = client.post("/shift/people/standards/edit", data=form)
    assert resp.status_code == 400
    html = resp.data.decode()
    assert "Nothing was changed." in html and "level 3" in html and "Kept while fixing." in html
    assert people_db.active_model()["version"] == 2
    form.update(key="no-such-standard")
    assert client.post("/shift/people/standards/edit", data=form).status_code == 302


def test_a_leader_with_a_grant_reads_the_standards_but_cannot_change_them(world):
    client = kit.open_as(world, "rae")
    html = page(client, "/shift/people/standards")
    assert "Reliable" in html and "read only" in html
    for tool in ("Change this wording", "Load a standards file", "See an example file", "<form method=\"post\" action=\"/shift/people/standards"):
        assert tool not in html, tool
    model = people_db.active_model()
    for path, data in (("/shift/people/standards/edit", {"model_id": model["id"], "key": "teamwork"}),
                       ("/shift/people/standards/export", {}),
                       ("/shift/people/standards/example", {})):
        resp = client.post(path, data=data)
        assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people", path
    resp = upload(client, example())
    assert resp.status_code == 302 and people_db.active_model()["version"] == 1


# --- activity log and backup (the pages) ----------------------------------------

def test_activity_log_page_is_newest_first_two_hundred_a_page(world):
    for n in range(205):
        people_db.audit("Rae", "review opened", "review", n)
    client = kit.open_as(world, "operator")
    first = page(client, "/shift/people/audit")
    assert first.count("<tr>") == 201 and "review 204" in first and "Older" in first
    assert "Newer" not in first
    second = page(client, "/shift/people/audit?page=2")
    assert "review 0<" in second and "Newer" in second and "standards saved" in second
    assert "access given" in second and "leader" in second
    assert "Nothing further back." in page(client, "/shift/people/audit?page=9")
    for odd in ("0", "-3", "abc", "99999999999999999999", "²"):
        assert "review 204" in page(client, f"/shift/people/audit?page={odd}")


def test_backup_page_and_download(world):
    client = kit.open_as(world, "operator")
    html = page(client, "/shift/people/backup")
    for text in ("This file holds private information about people.", "No copy has been downloaded yet.",
                 "Restoring from this file is not built in this version.", "Keep only the newest copy"):
        assert text in html, text
    resp = client.post("/shift/people/backup")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="people-backup-')
    assert resp.headers["Cache-Control"] == "private, no-store"
    payload = json.loads(resp.data)
    assert "meta" not in payload["tables"] and payload["tables"]["access_grants"]
    assert "Last downloaded" in page(client, "/shift/people/backup")
    assert [r["who"] for r in people_db.audit_feed() if r["action"] == "backup downloaded"] == ["Operator"]


# --- headers, and no scripts anywhere ---------------------------------------------

def test_every_page_has_the_strict_headers_and_runs_no_scripts(world):
    hub_csp = cfa_app.app.test_client().get("/hub/login").headers["Content-Security-Policy"]
    client = world["client"]
    kit.sign_in(client, "operator")
    pages = [(UNLOCK, client.get(UNLOCK))]
    kit.unlock(client, "operator")
    people_db.audit("Operator", "review opened", "review", 1)
    pages += [(path, client.get(path)) for path in CORE_PAGES]
    bad_upload = upload(client, b"nope")
    pages.append(("standards with problems", bad_upload))
    for name, resp in pages:
        assert resp.status_code in (200, 400), name
        assert resp.headers["Cache-Control"] == "private, no-store", name
        assert resp.headers["X-Content-Type-Options"] == "nosniff", name
        assert resp.headers["Referrer-Policy"] == "same-origin", name
        assert resp.headers["X-Frame-Options"] == "DENY", name
        assert resp.headers["Content-Security-Policy"] == hub_csp, name
        html = resp.data.decode()
        assert "<script" not in html.lower(), name
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", html), name
        assert "javascript:" not in html.lower(), name
        assert not re.search(r'(?:src|href)="https?://', html), name          # no outside assets
        assert re.search(r'<section class="hero compact">\s*<p class="eyebrow">[^<]+</p>\s*'
                         r'<h1 class="headline">[^<]+</h1>\s*<p class="triage">', html), name
        assert '<nav class="tabbar"' in html and "Open the Team Hub menu" in html, name
    # redirects from the gate carry the no-store header too
    resp = cfa_app.app.test_client().get("/shift/people")
    assert resp.status_code == 302 and resp.headers["Cache-Control"] == "private, no-store"


# --- the Team Hub menu ----------------------------------------------------------

def menu(client, path="/shift/more"):
    html = page(client, path)
    return html, re.findall(r'<span class="grow">([^<]+)</span>', html)


def test_people_row_is_in_the_menu_for_the_right_people(world):
    for who, expected in (("operator", True), ("hari", True), ("rae", True),
                          ("lena", False), ("ada", False)):
        client = world["client"] if who == "operator" else cfa_app.app.test_client()
        kit.sign_in(client, who)                       # the menu needs no unlock
        html, rows = menu(client)
        assert ("People" in rows) is expected, who
        assert ('href="/shift/people"' in html) is expected, who
        assert "My review" not in rows
        assert "Team roster" in rows and "History" in rows          # the rest is untouched
        if expected and who != "operator":
            assert rows.index("People") > rows.index("History")
        hub_html, hub_rows = menu(client, "/hub/menu")               # the Hub's copy of the menu too
        assert ("People" in hub_rows) is expected, who
    # the leader tabs and the big words are exactly as they were
    tabs = re.findall(r"<span>([^<]+)</span>", html[html.index("menu-big"):html.index("menu-hr")])
    assert "People" not in tabs


def test_a_team_member_never_sees_a_people_link(world):
    client = world["client"]
    kit.sign_in(client, "member")
    html, rows = menu(client, "/hub/menu")
    assert "People" not in rows and "My review" not in rows
    assert "/shift/people" not in html and "/shift/reviews" not in html and "/shift/hiring" not in html
    assert "/shift/people" not in page(client, "/hub/")


def test_menu_badges_and_my_review_come_from_the_features(world, monkeypatch):
    import people_hiring
    import people_reviews
    monkeypatch.setattr(people, "_page_url", lambda path: path)
    monkeypatch.setattr(people_reviews, "nav_counts",
                        lambda kind, leader_id, name: ({"on_hold": 1, "past_due": 2}
                                                       if kind == "operator" else
                                                       {"mine": 2, "drafts": 4}))
    monkeypatch.setattr(people_hiring, "nav_counts",
                        lambda kind, leader_id, name: {"new": 3, "notices_due": 1})

    def rows_for(who):
        client = cfa_app.app.test_client()
        kit.sign_in(client, who)
        html, rows = menu(client)
        return html, rows

    html, rows = rows_for("lena")                      # no grant: only her own review
    assert "People" not in rows and "My review" in rows
    assert re.search(r'href="/shift/reviews/mine".{0,400}2 waiting', html, re.S)
    assert "to finish" not in html and "3 new" not in html and "on hold" not in html
    html, rows = rows_for("hari")                      # hiring only
    assert re.search(r"People</span><span class=\"badge b-red\">3 new", html)
    assert "to finish" not in html and "on hold" not in html
    html, rows = rows_for("rae")                       # reviewer only
    assert re.search(r"People</span><span class=\"badge b-tan\">4 to finish", html)
    assert "3 new" not in html and "My review" in rows
    html, rows = rows_for("operator")
    assert re.search(r"People</span><span class=\"badge b-red\">1 on hold", html)
    assert "My review" not in rows
    # one feature's trouble does not hide the other's count, or the row
    monkeypatch.setattr(people_reviews, "nav_counts", lambda *a: 1 / 0)
    html, rows = rows_for("operator")
    assert re.search(r"People</span><span class=\"badge b-red\">3 new", html)


def test_a_people_fault_never_breaks_team_hub(world, monkeypatch):
    client = world["client"]
    kit.sign_in(client, "operator")

    def boom(*args, **kwargs):
        raise RuntimeError("People is broken")

    real_rows = people.nav_rows
    monkeypatch.setattr(people, "nav_rows", boom)
    assert client.get("/shift/").status_code == 200
    html, rows = menu(client)
    assert "People" not in rows and "Team roster" in rows
    assert client.get("/hub/library").status_code == 200 and client.get("/hub/menu").status_code == 200
    # a row with an icon that does not exist is dropped, not a broken menu
    monkeypatch.setattr(people, "nav_rows", lambda *a: [
        {"icon": "no-such-icon", "label": "People", "url": "/shift/people", "badge": "", "tone": "b-tan"}])
    html, rows = menu(client)
    assert "People" not in rows
    # and with the People store itself unreadable, a leader's pages still open
    monkeypatch.setattr(people, "nav_rows", real_rows)
    hari = cfa_app.app.test_client()
    kit.sign_in(hari, "hari")
    monkeypatch.setattr(people_db, "capabilities", boom)
    assert hari.get("/shift/").status_code == 200
    html, rows = menu(hari)
    assert "People" not in rows and "Shift notes" in rows


def test_the_menu_carries_on_when_people_is_not_registered(world, monkeypatch):
    client = world["client"]
    kit.sign_in(client, "operator")
    monkeypatch.delitem(cfa_app.app.blueprints, "people")
    html, rows = menu(client)
    assert "People" not in rows and "/shift/people" not in html and "Team roster" in rows


# --- house rules for the source ---------------------------------------------------

def test_people_source_never_touches_private_team_hub_content():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("people.py", "people_db.py", "people_ai.py", "people_reviews.py",
                 "people_reviews_db.py", "people_hiring.py", "people_hiring_db.py", "careers.py"):
        source = open(os.path.join(root, name), encoding="utf-8").read()
        for word in ("oneonone", "daily_goals", "goal_updates", "lesson_progress"):
            assert word not in source, (name, word)
    for name in sorted(os.listdir(os.path.join(root, "templates", "people"))):
        if name.endswith(".html"):
            text = open(os.path.join(root, "templates", "people", name), encoding="utf-8").read()
            assert "<script" not in text and "|safe" not in text, name
