"""The public careers pages: what a posting shows, the two-step
application and its abuse controls, and the applicant's private page.
These pages are for people who do not work here, so they set no cookie,
run no scripts and share nothing with Team Hub. Everyone here is
invented."""

import json
import re

import pytest

import app as cfa_app
import careers
import people_db
import people_hiring_db as hdb
from tests import people_testkit as kit
from tests.test_people_hiring_db import DESCRIPTION, ai_in_use, clock, draft, live, person, table


@pytest.fixture()
def world(monkeypatch, tmp_path):
    w = kit.make_world(monkeypatch, tmp_path)
    people_db.set_setting("hiring_contact_name", "Morgan Example", "Operator")
    people_db.set_setting("hiring_contact_email", "hiring@example.com", "Operator")
    careers.reset_throttles()
    return w


@pytest.fixture()
def public():
    """Someone who has never signed in to anything."""
    return cfa_app.app.test_client()


def text(resp) -> str:
    return resp.data.decode()


def shown(page: str) -> str:
    """The page without its stylesheet (whose comments are not content)."""
    return re.sub(r"<style>.*?</style>", "", page, flags=re.S)


def send(client, slug="team-member", n=1, headers=None, **changes):
    return client.post(f"/careers/{slug}/apply", data=person(n, **changes), headers=headers or {})


def token_of(resp) -> str:
    assert resp.status_code == 302, resp.status_code
    match = re.fullmatch(r"/careers/a/([0-9a-f]{32})", resp.headers["Location"])
    assert match, resp.headers["Location"]
    return match.group(1)


# --- the list and the posting -------------------------------------------------------

def test_the_list_shows_open_postings_only(world, public):
    empty = text(public.get("/careers/"))
    assert "We're not hiring for a posted position right now. Check back soon." in empty
    live()
    closed = live(title="Closed job")
    hdb.close_posting(closed, "Operator")
    draft(title="Draft job")
    page = text(public.get("/careers/"))
    assert 'href="/careers/team-member"' in page and "Part-time · $17.95 to $19.50 an hour" in page
    assert "Closed job" not in page and "Draft job" not in page and "Check back soon" not in page


def test_a_posting_shows_every_required_statement_and_the_apply_button(world, public):
    live()
    resp = public.get("/careers/team-member")
    page = text(resp)
    assert resp.status_code == 200
    parts = ['<h1 class="headline">Team member</h1>', "Part-time", "$17.95 to $19.50 an hour",
             "Serve guests at the counter", "What we look for", "Reliable", "Guest care",
             "This posting is for an existing vacancy.",
             "welcomes applications from people with disabilities",
             "contact Morgan Example at hiring@example.com",
             "Ontario law sets a minimum age of 14 to work in a restaurant&#39;s serving areas and 15 in its kitchen.",
             'href="/careers/team-member/apply"']
    at = -1
    for part in parts:
        found = page.find(part, at + 1)
        assert found > at, part
        at = found
    assert "Chick-fil-A Wharncliffe &amp; Wonderland" in page
    # no AI on this posting: nothing is said about AI, and never "no AI is used"
    assert "artificial intelligence" not in page and "No AI" not in page and "no AI" not in page


def test_the_ai_statement_appears_only_when_ai_assist_is_on_for_the_posting(world, public, monkeypatch):
    posting_id = live()
    assert "artificial intelligence" not in text(public.get("/careers/team-member/apply"))
    hdb.set_ai(posting_id, True, "Operator")
    assert "artificial intelligence" not in text(public.get("/careers/team-member"))   # AI can't run yet
    ai_in_use(monkeypatch)
    statement = ("We use artificial intelligence (AI) to help assess applications for this position. "
                 "The AI tool compares your written answers with the job-related criteria in this posting "
                 "and suggests a score. A member of our leadership team reads every application and makes "
                 "every decision.")
    posting = text(public.get("/careers/team-member"))
    assert statement in posting
    assert posting.index("This posting is for an existing vacancy.") < posting.index(statement) \
        < posting.index("welcomes applications from people with disabilities")
    form = text(public.get("/careers/team-member/apply"))
    assert statement in form and 'name="ai" value="1"' in form
    assert "may be sent to an AI service run by an outside company" in form
    assert "Your name and contact details are not sent with them." in form
    # the Operator switches AI off: the posting stops saying it, and that version is kept too
    copies = len(hdb.snapshots(posting_id))
    people_db.set_setting("ai_hiring", "0", "Operator")
    quiet = text(public.get("/careers/team-member/apply"))
    assert "artificial intelligence" not in quiet and 'name="ai"' not in quiet and "AI service" not in quiet
    assert len(hdb.snapshots(posting_id)) == copies + 1 and hdb.snapshots(posting_id)[-1]["ai_statement"] == 0


def test_the_student_rate_is_explained_and_never_says_students_only(world, public):
    live(pay_min="16.90", student_rate="1")
    page = text(public.get("/careers/team-member"))
    assert "$16.90 to $19.50 an hour" in page
    assert ("The lower rate is Ontario&#39;s student minimum wage, which applies to students under 18 who "
            "work 28 hours a week or less during the school year or work during a school break.") in page
    assert "students only" not in page.lower()


def test_the_description_is_rendered_safely(world, public):
    live(description=DESCRIPTION + "\n\n## What we offer\n- **Flexible** shifts\n- <script>alert(1)</script>")
    page = text(public.get("/careers/team-member"))
    assert "<h3>What we offer</h3>" in page and "<strong>Flexible</strong>" in page
    assert "<script" not in page and "&lt;script&gt;" in page


@pytest.mark.parametrize("path", [
    "/careers/no-such-job", "/careers/no-such-job/apply", "/careers/draft-job", "/careers/closed-job",
    "/careers/closed-job/apply", "/careers/a/" + "0" * 32, "/careers/a/short", "/careers/x/y/z",
    "/careers/a", "/careers/UPPER", "/careers/a/apply",
])
def test_anything_that_is_not_an_open_posting_gets_the_same_page(world, public, path):
    draft(title="Draft job")
    hdb.close_posting(live(title="Closed job"), "Operator")
    resp = public.get(path)
    assert resp.status_code == 404, path
    page = text(resp)
    assert "That page isn&#39;t here" in page and 'href="/careers/"' in page
    assert "Draft job" not in page and "Closed job" not in page
    assert page == text(public.get("/careers/definitely-not-here"))     # one page for all of them
    post = public.post(path, data=person())
    assert post.status_code == 404 and table("hr_applications") == []


# --- the application form ------------------------------------------------------------

def test_the_form_asks_only_what_the_job_needs(world, public):
    live()
    form = shown(text(public.get("/careers/team-member/apply")))
    names = set(re.findall(r'<(?:input|select|textarea)[^>]*\sname="([^"]+)"', form))
    availability = {f"av_{day}_{part}" for day, _ in hdb.DAYS for part, _ in hdb.DAYPARTS}
    assert len(availability) == 21
    assert names == availability | {"website", "first_name", "last_name", "phone", "email", "work_entitled",
                                    "adult", "heard_from", "referrer", "experience", "consent"}
    asked = form[form.index("<form"):form.index("</form>")]
    lowered = re.sub(r"<[^>]+>", " ", asked).lower() + " " + " ".join(names)
    for banned in ("date of birth", "birth", "dob", "gender", "sex", "marital", "married", "maiden",
                   "religio", "church", "sin ", "social insurance", "photo", "picture", "nationality",
                   "citizen", "born", "children", "childcare", "emergency contact", "next of kin",
                   "health", "medical", "criminal", "convict", "licence", "license", "postal", "address",
                   "height", "weight", "pronoun", "canadian experience", "resume"):
        assert banned not in lowered, banned
    assert 'type="file"' not in form and 'type="date"' not in form
    # what is there, with the words the brief asks for
    for needed in ("Are you legally entitled to work in Canada?", "Are you 18 or older?",
                   "We ask only because some rules differ for people under 18. It is not used to assess "
                   "your application.", "When could you work?", "How did you hear about us?",
                   "Any work or volunteer experience you would like us to know about?",
                   "A friend or family member who works here", "Indeed or another job site",
                   "If you are under 18, you may wish to read this with a parent or guardian.",
                   "It is seen only by the Operator and the leaders involved in hiring.",
                   "We do not sell your information or use it for marketing.",
                   "for 24 months after the hiring decision", "for 3 years",
                   "You can ask to see, correct or withdraw your application by contacting Morgan Example"):
        assert needed in form, needed
    assert "why" not in re.sub(r"<[^>]+>", " ", form[form.index("When could you work?"):
                                                      form.index("How did you hear about us?")]).lower()
    # the accommodation notice is at the top of the form, before the first field
    assert form.index("Accommodation is available on request") < form.index('name="first_name"')
    assert form.count('required') >= 5 and 'name="consent" value="1" required' in form


def test_step_one_creates_an_application_and_hands_over_a_private_link(world, public):
    posting_id = live()
    token = token_of(send(public, phone="519-555-0142"))
    rows = table("hr_applications")
    assert len(rows) == 1 and rows[0]["posting_id"] == posting_id and rows[0]["stage"] == "new"
    assert rows[0]["token_hash"] == people_db.token_hash(token) and token not in json.dumps(rows)
    assert [e["kind"] for e in table("hr_events")] == ["applied"]
    page = text(public.get(f"/careers/a/{token}"))
    assert "A few quick questions" in page and "about five minutes" in page
    assert "Tap the microphone on your keyboard to talk instead of type." in page
    assert page.count("<textarea") == 2 and 'maxlength="1500"' in page
    assert "Tell us about a time you showed reliable." in page


@pytest.mark.parametrize("changes, said", [
    ({"first_name": ""}, "Enter your first name."),
    ({"email": "", "phone": ""}, "Give us a phone number or an email so we can reach you."),
    ({"email": "nope"}, "That email doesn&#39;t look right. Check it and try again."),
    ({"phone": "12345"}, "That phone number needs 10 to 15 digits."),
    ({"work_entitled": ""}, "Answer yes or no: are you legally entitled to work in Canada?"),
    ({"adult": ""}, "Answer yes or no: are you 18 or older?"),
    ({"consent": ""}, "Tick the box to say you have read how we look after your information."),
])
def test_a_form_with_something_missing_comes_back_with_what_was_typed(world, public, changes, said):
    live()
    resp = send(public, experience="Two summers at a farm stand.", **changes)
    page = text(resp)
    assert resp.status_code == 400 and said in page and 'role="alert"' in page
    assert "Two summers at a farm stand." in page and 'name="av_mon_morning" value="1" aria-label="Monday morning" checked' in page
    assert table("hr_applications") == [] and "Set-Cookie" not in resp.headers


def test_the_hidden_box_catches_robots_and_keeps_nothing(world, public):
    live()
    form = text(public.get("/careers/team-member/apply"))
    assert 'name="website"' in form and 'class="cr-hp" aria-hidden="true"' in form and 'tabindex="-1"' in form
    resp = send(public, website="https://spam.example")
    page = text(resp)
    assert resp.status_code == 200 and "Thank you" in page and "We have your application for Team member." in page
    assert "/careers/a/" not in page and table("hr_applications") == [] and table("hr_events") == []


def test_the_same_person_twice_gets_thanks_and_no_second_application(world, public):
    live()
    token_of(send(public))
    resp = send(public, email="JORDAN1@EXAMPLE.COM")
    page = text(resp)
    assert resp.status_code == 200 and "/careers/a/" not in page
    assert "We already have your application for this role. To change it, contact Morgan Example" in page
    assert len(table("hr_applications")) == 1


def test_five_applications_an_hour_from_one_address(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-20 10:00")
    live()
    there = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    for n in range(1, 6):
        token_of(send(public, n=n, headers=there))
    resp = send(public, n=6, headers=there)
    assert resp.status_code == 429 and "Please try again later" in text(resp)
    assert "contact Morgan Example at hiring@example.com" in text(resp)
    assert len(table("hr_applications")) == 5
    # the first hop is what counts: another address is not held back by this one
    token_of(send(public, n=7, headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}))
    assert send(public, n=8, headers={"X-Forwarded-For": "203.0.113.9, 10.9.9.9"}).status_code == 429
    # a form that fails its checks does not use up a turn, and an hour later the address is free
    clock(monkeypatch, "2026-11-20 11:01")
    for _ in range(8):
        assert send(public, n=9, headers=there, first_name="").status_code == 400
    token_of(send(public, n=9, headers=there))
    # the address itself is held nowhere
    held = json.dumps([list(careers._applied), list(careers._wrong_links)])
    assert "203.0.113" not in held
    assert "203.0.113" not in people_db.export_json("Operator")


def test_sixty_an_hour_and_three_hundred_a_day_from_everyone(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-20 08:00")
    live()
    monkeypatch.setattr(careers, "ALL_AN_HOUR", 6)
    monkeypatch.setattr(careers, "ALL_A_DAY", 9)
    for n in range(1, 7):
        token_of(send(public, n=n, headers={"X-Forwarded-For": f"198.51.100.{n}"}))
    assert send(public, n=7, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 429
    clock(monkeypatch, "2026-11-20 09:01")
    for n in range(7, 10):
        token_of(send(public, n=n, headers={"X-Forwarded-For": f"198.51.100.{n}"}))
    assert send(public, n=10, headers={"X-Forwarded-For": "198.51.100.10"}).status_code == 429     # the day's limit
    clock(monkeypatch, "2026-11-21 09:02")
    token_of(send(public, n=10, headers={"X-Forwarded-For": "198.51.100.10"}))
    assert (careers.PER_ADDRESS_AN_HOUR, careers.MAX_BODY) == (5, 64 * 1024)
    careers.reset_throttles()
    assert not careers._applied and not careers._applied_all and not careers._wrong_links


def test_the_real_limits_are_sixty_an_hour_and_three_hundred_a_day():
    assert (careers.ALL_AN_HOUR, careers.ALL_A_DAY, careers.MAX_ADDRESSES) == (60, 300, 5000)


def test_the_tables_that_hold_the_limits_stay_bounded(world, monkeypatch):
    monkeypatch.setattr(careers, "MAX_ADDRESSES", 10)
    monkeypatch.setattr(careers, "ALL_AN_HOUR", 1000)
    monkeypatch.setattr(careers, "ALL_A_DAY", 1000)
    for n in range(40):
        assert careers._take_application_slot(f"address-{n}", 1000.0 + n)
        careers._count_wrong_link(f"address-{n}", 1000.0 + n)
    assert len(careers._applied) <= 10 and len(careers._wrong_links) <= 10


def test_too_much_text_gets_a_friendly_page(world, public):
    live()
    resp = public.post("/careers/team-member/apply", data=person(experience="x" * 70_000))
    assert resp.status_code == 413 and "That was too much to send" in text(resp)
    assert "Shorten what you wrote and send it again." in text(resp)
    assert table("hr_applications") == []
    # a long answer on step two as well
    token = token_of(send(public))
    question = hdb.application_by_token(token)["questions"][0]["id"]
    resp = public.post(f"/careers/a/{token}", data={f"q_{question}": "y" * 70_000})
    assert resp.status_code == 413 and table("hr_answers") == []


@pytest.mark.parametrize("headers, accepted", [
    ({}, True),
    ({"Origin": "http://localhost"}, True),
    ({"Sec-Fetch-Site": "same-origin"}, True),
    ({"Sec-Fetch-Site": "same-site"}, True),
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
    ({"Sec-Fetch-Site": "cross-site"}, False),
])
def test_a_form_sent_from_another_site_is_refused(world, public, headers, accepted):
    live()
    resp = send(public, headers=headers)
    if accepted:
        token_of(resp)
    else:
        assert resp.status_code == 403 and "didn&#39;t come from this page" in text(resp)
        assert table("hr_applications") == []


# --- the private page ----------------------------------------------------------------

def test_answers_are_taken_once_and_then_the_page_shows_status_only(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    live()
    token = token_of(send(public))
    mine = hdb.application_by_token(token)
    first, second = (q["id"] for q in mine["questions"])
    blank = public.post(f"/careers/a/{token}", data={f"q_{first}": "  ", f"q_{second}": ""})
    assert blank.status_code == 400 and "Write an answer to at least one question." in text(blank)
    resp = public.post(f"/careers/a/{token}", data={f"q_{first}": "I opened the stand every Saturday.",
                                                     f"q_{second}": "I walked a lost guest to her table."})
    assert resp.status_code == 302 and resp.headers["Location"] == f"/careers/a/{token}"
    page = text(public.get(f"/careers/a/{token}"))
    assert "We have your application for Team member, received Nov 1." in page
    assert "We have your answers to the quick questions." in page
    assert "<textarea" not in page and "<form" not in page
    assert "contact Morgan Example at hiring@example.com" in page.lower().replace("contact morgan example", "contact Morgan Example")
    assert "hiring decision" not in page                               # nothing has been decided or said
    # a second try changes nothing
    again = public.post(f"/careers/a/{token}", data={f"q_{first}": "Something else."})
    assert again.status_code == 302
    assert {a["body"] for a in table("hr_answers")} == {"I opened the stand every Saturday.",
                                                       "I walked a lost guest to her table."}
    assert [e["kind"] for e in table("hr_events")] == ["applied", "answered"]


def test_the_questions_close_after_the_window(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    live()
    token = token_of(send(public))
    question = hdb.application_by_token(token)["questions"][0]["id"]
    assert "You have until Nov 8, 9:00 am." in text(public.get(f"/careers/a/{token}"))
    clock(monkeypatch, "2026-11-08 09:01")
    page = text(public.get(f"/careers/a/{token}"))
    assert "<textarea" not in page and "The quick questions for this application are now closed." in page
    assert "We have your application for Team member, received Nov 1." in page
    public.post(f"/careers/a/{token}", data={f"q_{question}": "Too late."})
    assert table("hr_answers") == []


def test_status_wording_follows_the_notice_and_never_shows_what_leaders_wrote(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    posting_id = live()
    token = token_of(send(public))
    app_id = table("hr_applications")[0]["id"]
    first = hdb.get_posting(posting_id)["criteria"][0]["id"]
    hdb.save_answers(app_id, {first: "I opened the stand every Saturday."})
    hari = world["ids"]["hari"]
    hdb.save_answer_scores(app_id, "Hari", hari, {first: ("4", "Outstanding and specific.")})
    hdb.add_note(app_id, "Private leader note.", "Hari")
    hdb.move_stage(app_id, "new", "interview", "Hari")
    hdb.add_interview(app_id, "2026-11-01", "", "phone", "Hari", "Hari")
    hdb.log_notice(app_id, "no-decision-yet", "email", "Internal wording of the notice.", "2026-11-01", "Hari")
    page = text(public.get(f"/careers/a/{token}"))
    assert "A hiring decision has not yet been made for this posting." in page
    assert "A hiring decision has been made for this posting." not in page
    hdb.move_stage(app_id, "interview", "not-selected", "Hari", reason="Position filled")
    clock(monkeypatch, "2026-11-02 09:00")
    hdb.log_notice(app_id, "not-moving-forward", "email", "Second internal wording.", "2026-11-02", "Hari")
    page = text(public.get(f"/careers/a/{token}"))
    assert "A hiring decision has been made for this posting." in page
    assert "not yet been made" not in page
    lowered = page.lower()
    for private in ("outstanding", "level", "score", "private leader note", "internal wording",
                    "not selected", "not-selected", "position filled", "interview", "screening", "hari",
                    "stage"):
        assert private not in lowered, private


def test_wrong_links_get_the_ordinary_missing_page_and_are_limited(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    live()
    token = token_of(send(public))
    missing = text(public.get("/careers/no-such-job"))
    there = {"X-Forwarded-For": "203.0.113.50"}
    for n in range(20):
        resp = public.get(f"/careers/a/{n:032x}", headers=there)
        assert resp.status_code == 404 and text(resp) == missing
    blocked = public.get(f"/careers/a/{token}", headers=there)           # even the right one, for now
    assert blocked.status_code == 429 and "Wait an hour" in text(blocked)
    assert public.get(f"/careers/a/{token}").status_code == 200          # someone else is not held back
    clock(monkeypatch, "2026-11-01 10:01")
    assert public.get(f"/careers/a/{token}", headers=there).status_code == 200
    # opening your own link over and over is never a wrong link
    for _ in range(30):
        assert public.get(f"/careers/a/{token}", headers={"X-Forwarded-For": "203.0.113.77"}).status_code == 200


# --- no cookies, no scripts, no Team Hub ---------------------------------------------

def test_public_pages_set_no_cookie_and_carry_the_strict_headers(world, public, monkeypatch):
    clock(monkeypatch, "2026-11-01 09:00")
    hub_csp = cfa_app.app.test_client().get("/hub/login").headers["Content-Security-Policy"]
    ai_in_use(monkeypatch)
    posting_id = live()
    hdb.set_ai(posting_id, True, "Operator")
    assert "artificial intelligence" in text(public.get("/careers/team-member"))
    seen = [("list", public.get("/careers/")), ("posting", public.get("/careers/team-member")),
            ("form", public.get("/careers/team-member/apply")),
            ("form with a problem", send(public, first_name="")),
            ("robot", send(public, website="x")), ("missing", public.get("/careers/nope")),
            ("too big", public.post("/careers/team-member/apply", data=person(experience="x" * 70_000))),
            ("another site", send(public, headers={"Origin": "https://evil.example"}))]
    created = send(public)
    token = token_of(created)
    seen += [("created", created), ("questions", public.get(f"/careers/a/{token}")),
             ("duplicate", send(public))]
    question = hdb.application_by_token(token)["questions"][0]["id"]
    seen.append(("answered", public.post(f"/careers/a/{token}", data={f"q_{question}": "An answer."})))
    seen.append(("status", public.get(f"/careers/a/{token}")))
    for name, resp in seen:
        assert "Set-Cookie" not in resp.headers, name
        assert resp.headers["Cache-Control"] == "private, no-store", name
        assert resp.headers["X-Content-Type-Options"] == "nosniff", name
        assert resp.headers["Referrer-Policy"] == "same-origin", name
        assert resp.headers["X-Frame-Options"] == "DENY", name
        if resp.status_code == 302:
            continue
        assert resp.headers["Content-Security-Policy"] == hub_csp, name
        page = shown(text(resp))
        assert "<script" not in text(resp).lower() and "javascript:" not in page.lower(), name
        assert not re.search(r"<[^>]+\son[a-z]+\s*=", page), name
        assert not re.search(r'(?:src|href)="https?://', page), name          # nothing from another site
        # none of Team Hub's chrome: no menu, no install prompt, no staff sign-in link
        for staff in ("Team Hub", "/shift", "/hub/", "manifest", "apple-mobile-web-app", "Sign out",
                      "tabbar", "burger"):
            assert staff not in page, (name, staff)
        assert 'alt="Chick-fil-A"' in page and "Chick-fil-A Wharncliffe &amp; Wonderland" in page, name
    with public.session_transaction() as sess:
        assert dict(sess) == {}                                          # the session was never written


def test_a_signed_in_leaders_session_is_not_read_or_changed(world, public):
    live()
    kit.sign_in(public, "hari")
    with public.session_transaction() as sess:
        before = dict(sess)
    page = text(public.get("/careers/team-member"))
    assert "Hari" not in page and "Sign out" not in page
    token_of(send(public))
    with public.session_transaction() as sess:
        assert dict(sess) == before


def test_nothing_about_an_applicant_is_printed(world, public, capsys):
    live()
    token = token_of(send(public, phone="519-555-0142", experience="Secret experience text."))
    question = hdb.application_by_token(token)["questions"][0]["id"]
    public.post(f"/careers/a/{token}", data={f"q_{question}": "Secret answer text."})
    send(public)
    send(public, first_name="")
    out = capsys.readouterr()
    for private in ("Jordan1", "Sample", "jordan1@example.com", "519-555-0142", "Secret", token):
        assert private not in out.out and private not in out.err, private


def test_careers_source_has_no_session_flash_or_script():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import ast
    tree = ast.parse(open(os.path.join(root, "careers.py"), encoding="utf-8").read())
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    used |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert not {"session", "flash", "print", "get_flashed_messages"} & used
    folder = os.path.join(root, "templates", "careers")
    for name in sorted(os.listdir(folder)):
        page = open(os.path.join(folder, name), encoding="utf-8").read()
        assert "<script" not in page and "|safe" not in page, name
        assert "get_flashed_messages" not in page and "flashes(" not in page and "session" not in page, name
        assert "head_tags" not in page and "masthead(" not in page and "login_url" not in page, name


def test_the_old_leadership_application_form_still_works(world, public):
    resp = public.get("/apply")
    assert resp.status_code == 200 and b"Leadership" in resp.data
    assert "/apply" in {r.rule for r in cfa_app.app.url_map.iter_rules()}


def test_what_the_public_is_shown_is_always_on_record_first(world, public):
    posting_id = live()
    public.get("/careers/team-member")
    assert len(hdb.snapshots(posting_id)) == 1
    people_db.set_setting("hiring_contact_name", "Riley Example", "Operator")
    assert len(hdb.snapshots(posting_id)) == 1                         # nobody has been shown it yet
    assert "contact Riley Example" in text(public.get("/careers/team-member"))
    copies = hdb.snapshots(posting_id)
    assert len(copies) == 2 and "contact Riley Example" in hdb.get_snapshot(copies[-1]["id"])["body"]
    public.get("/careers/team-member/apply")
    public.get("/careers/")
    assert len(hdb.snapshots(posting_id)) == 2
