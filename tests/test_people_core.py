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
