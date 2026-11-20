"""Who can open what in the People section: every page against every kind
of person.

tests/test_roles.py does this for the rest of Team Hub. People pages need
more than a role (the Operator's tick, the PIN again, and for reviews the
assignment), so they get their own table here: every People GET page, for
someone signed out, a team member, a lead with no access, an admin-role
leader with no access, a hiring leader, a reviewer, a leader whose own
review has been shared with them, and the Operator. Everyone is invented.
"""

import re

import pytest

import app as cfa_app
import people_hiring_db as hdb
import people_reviews_db as rdb
import shift_db
from tests import people_testkit as kit
from tests import test_people_hiring_db as hk
from tests import test_people_reviews_kit as rk
from tests import test_roles

# sign-in name and PIN for each leader (Sol is added by the fixture)
LEADERS = {"lena": ("Lena", "1111"), "ada": ("Ada", "2222"), "hari": ("Hari", "3333"),
           "rae": ("Rae", "4444"), "sol": ("Sol", "5555"), "operator": ("Operator", "9999")}
SIGNED_IN = set(LEADERS)
OPERATOR = {"operator"}
UNLOCK = "/shift/people/unlock"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """The test kit's store, plus what the pages need to exist: Sol, whose
    review (written by Rae) has been shared with them; a second leader
    under review with a draft, also Rae's; and a live posting with one
    applicant."""
    w = kit.make_world(monkeypatch, tmp_path)
    ids = w["ids"]
    assert shift_db.add_leader("Sol", "5555", "lead") is None
    ids["sol"] = next(l["id"] for l in shift_db.leaders() if l["name"] == "Sol")
    reviewer = dict(reviewer_leader_id=ids["rae"], reviewer_name="Rae")
    sol, problem = rdb.add_subject("Sol", "Operator", leader_id=ids["sol"], leader_name="Sol", **reviewer)
    assert problem is None
    pat, problem = rdb.add_subject("Pat Example", "Operator", **reviewer)     # no sign-in linked
    assert problem is None
    cycle = rk.add_cycle()
    shared, draft = rk.review_of(cycle, sol), rk.review_of(cycle, pat)
    rk.observe(sol, "Ran the lunch handover and briefed the next leader.", by_id=ids["rae"])
    rk.rate_all(shared)
    assert rk.finalise(shared) == "final" and rdb.share(shared, "Rae")
    rk.rate_all(draft)
    hk.people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    hk.people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    posting = hk.live()
    w["at"] = {"subject": sol, "cycle": cycle, "shared": shared, "draft": draft, "posting": posting,
               "copy": hdb.snapshots(posting)[0]["id"], "candidate": hk.applied(posting)}
    return w


def table(w) -> list[tuple[str, set]]:
    """(path, who may open it once they are past the lock). Everyone else
    is turned away."""
    at = w["at"]
    hiring = {"hari", "operator"}
    writers = {"rae", "operator"}                       # the assigned reviewer, and the Operator
    review, posting, person = (f"/shift/reviews/review/{at['shared']}",
                               f"/shift/hiring/postings/{at['posting']}",
                               f"/shift/hiring/candidates/{at['candidate']}")
    return [
        # the platform
        ("/shift/people", SIGNED_IN),
        ("/shift/people/standards", {"hari", "rae", "operator"}),     # read-only for a leader
        ("/shift/people/access", OPERATOR), ("/shift/people/settings", OPERATOR),
        ("/shift/people/audit", OPERATOR), ("/shift/people/backup", OPERATOR),
        # leader reviews: the Operator, and a reviewer for the leaders assigned to them
        ("/shift/reviews", writers),                    # anyone else is sent to their own review
        ("/shift/reviews/leaders", OPERATOR),
        (f"/shift/reviews/leaders/{at['subject']}", writers),
        (f"/shift/reviews/cycles/{at['cycle']}", OPERATOR),
        (review, writers),
        (f"/shift/reviews/review/{at['draft']}/finalise", writers),
        (f"{review}/print", writers | {"sol"}),         # and the leader it is about, once shared
        (f"/shift/reviews/calibration/{at['cycle']}", OPERATOR),
        ("/shift/reviews/health", OPERATOR),
        ("/shift/reviews/mine", SIGNED_IN),             # each sees only their own
        (f"/shift/reviews/mine/{at['shared']}", {"sol"}),             # not even the Operator, here
        # hiring: the Operator and hiring leaders
        ("/shift/hiring", hiring), ("/shift/hiring/postings", hiring),
        ("/shift/hiring/postings/new", hiring), (posting, hiring), (f"{posting}/edit", hiring),
        (f"{posting}/copies/{at['copy']}", hiring),
        (person, hiring), (f"{person}/contact", hiring),
        ("/shift/hiring/notices", hiring), ("/shift/hiring/metrics", hiring),
        ("/shift/hiring/retention", OPERATOR),
    ]


def sign_in(client, who: str) -> None:
    name, pin = LEADERS[who]
    assert client.post("/shift/login", data={"name": name, "pin": pin}).status_code == 302


def people_get_rules() -> set[str]:
    """Every People page a browser can ask for with GET."""
    return {rule.rule for rule in cfa_app.app.url_map.iter_rules()
            if rule.endpoint.startswith("people.") and "GET" in rule.methods}


def rules_reached(paths) -> set[str]:
    adapter = cfa_app.app.url_map.bind("localhost")
    reached = set()
    for path in paths:
        rule, _ = adapter.match(path, method="GET", return_rule=True)
        reached.add(rule.rule)
    return reached


def test_the_table_covers_every_people_page(world):
    """A new People page that is not in the table fails here, so no page
    can be added without saying who may open it."""
    paths = [path for path, _ in table(world)] + [UNLOCK]
    assert rules_reached(paths) == people_get_rules()
    # and the same pages are in the Team Hub table, where nobody gets one before the lock
    assert rules_reached(test_roles.PEOPLE_PAGES + (test_roles.PEOPLE_LOCK,)) == people_get_rules()


def test_signed_out_gets_the_front_door(world):
    client = cfa_app.app.test_client()
    for path, _ in table(world) + [(UNLOCK, set())]:
        resp = client.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path


def test_a_team_member_is_sent_back_to_the_hub(world):
    client = cfa_app.app.test_client()
    kit.sign_in(client, "member")
    for path, _ in table(world) + [(UNLOCK, set())]:
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/hub/"), path
    assert "leader" in client.get("/hub/").get_data(as_text=True)     # told why, on the Hub


@pytest.mark.parametrize("who", sorted(LEADERS))
def test_every_page_for_every_person(world, who):
    client = cfa_app.app.test_client()
    sign_in(client, who)
    pages = table(world)
    # before the PIN is entered again, nobody gets any of it: not even the Operator
    for path, _ in pages:
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].startswith(UNLOCK + "?next="), \
            (who, path, resp.headers.get("Location"))
    assert client.get(UNLOCK).status_code == 200
    resp = client.post(UNLOCK, data={"pin": LEADERS[who][1]})
    assert resp.status_code == 302 and resp.headers["Location"] == "/shift/people"
    for path, allowed in pages:
        resp = client.get(path)
        if who in allowed:
            assert resp.status_code == 200, (who, path, resp.status_code, resp.headers.get("Location"))
        else:
            assert resp.status_code in (302, 404), (who, path, resp.status_code)
            where = resp.headers.get("Location", "")
            assert where.startswith(("/shift/people", "/shift/reviews/mine")), (who, path, where)
    assert client.get(UNLOCK).status_code == 302          # already open: straight on


PEOPLE_LINK = re.compile(r'href="(/shift/(?:people|reviews|hiring)[^"#?]*)')


def people_links(client) -> dict[str, set]:
    """Every link into People that the People home page and the Team Hub
    menu offer this person."""
    found = {}
    for path in ("/shift/people", "/shift/more"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        found[path] = set(PEOPLE_LINK.findall(resp.get_data(as_text=True)))
    return found


@pytest.mark.parametrize("who", sorted(LEADERS))
def test_home_and_menu_link_only_to_pages_that_exist_and_are_theirs(world, who):
    """Nobody is ever offered a People link that is not there or that
    turns them away: every one on People home and in the menu opens."""
    client = cfa_app.app.test_client()
    sign_in(client, who)
    client.post(UNLOCK, data={"pin": LEADERS[who][1]})
    links = people_links(client)
    for link in sorted(links["/shift/people"] | links["/shift/more"]):
        assert client.get(link).status_code == 200, (who, link)
    everywhere = links["/shift/people"] | links["/shift/more"]
    # the People row is for the Operator and ticked leaders; the features follow the tick
    assert ("/shift/people" in links["/shift/more"]) == (who in {"hari", "rae", "operator"})
    assert ("/shift/hiring" in {l.rstrip("/") for l in everywhere}) == (who in {"hari", "operator"})
    assert ("/shift/reviews" in {l.rstrip("/") for l in everywhere}) == (who in {"rae", "operator"})
    assert ("/shift/reviews/mine" in everywhere) == (who == "sol")       # only someone with a review of their own
    assert ("/shift/people/access" in everywhere) == (who == "operator")


def test_a_leader_keeps_the_way_back_to_a_review_they_have_confirmed(world):
    """"My review" is on People home and in the menu while a review is
    waiting, and stays there once it has been confirmed."""
    mine, review = "/shift/reviews/mine", world["at"]["shared"]
    client = cfa_app.app.test_client()
    sign_in(client, "sol")
    client.post(UNLOCK, data={"pin": LEADERS["sol"][1]})
    links = people_links(client)
    assert mine in links["/shift/people"] and mine in links["/shift/more"]
    assert "1 waiting" in client.get("/shift/more").get_data(as_text=True)
    resp = client.post(f"{mine}/{review}/acknowledge",
                       data={"confirm": "1", "discussed_on": shift_db.now_local().date().isoformat()})
    assert resp.status_code == 302 and rdb.get_review(review)["status"] == "acknowledged"
    links = people_links(client)
    assert mine in links["/shift/people"] and mine in links["/shift/more"]
    assert "waiting" not in client.get("/shift/more").get_data(as_text=True)
    assert f'href="{mine}/{review}"' in client.get(mine).get_data(as_text=True)
    assert client.get(f"{mine}/{review}").status_code == 200


def test_what_each_person_is_turned_away_from_holds_nothing_of_it(world):
    """Being turned away is a plain redirect: the page that comes next
    never carries the name of the leader or applicant that was asked for."""
    at = world["at"]
    for who in ("lena", "ada"):
        client = cfa_app.app.test_client()
        sign_in(client, who)
        client.post(UNLOCK, data={"pin": LEADERS[who][1]})
        for path in (f"/shift/reviews/review/{at['shared']}", f"/shift/reviews/leaders/{at['subject']}",
                     f"/shift/hiring/candidates/{at['candidate']}",
                     f"/shift/hiring/candidates/{at['candidate']}/contact"):
            page = client.get(path, follow_redirects=True).get_data(as_text=True)
            for private in ("Sol", "Jordan1", "jordan1@example.com", "lunch handover"):
                assert private not in page, (who, path, private)


def test_the_public_careers_pages_need_no_sign_in(world):
    client = cfa_app.app.test_client()
    slug = hdb.get_posting(world["at"]["posting"])["slug"]
    for path in ("/careers/", f"/careers/{slug}", f"/careers/{slug}/apply"):
        assert client.get(path).status_code == 200, path
    # and they are no way into the leader pages
    assert "/shift" not in client.get(f"/careers/{slug}").get_data(as_text=True)
