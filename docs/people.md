# People — leader reviews and hiring

**People** is the private part of Team Hub: **leader reviews**
(`/shift/reviews`) and **hiring** (`/shift/hiring`, plus the public careers
pages at `/careers/`). It runs on the same Render service as the rest of
Team Hub, but it keeps its records in **its own database file** and opens
only for the Operator and the leaders the Operator ticks.

Two rules shape everything here:

- **A person decides, every time.** Nothing in People rates a leader,
  moves an applicant, turns anyone down or triggers a consequence on its
  own. Where AI is switched on it prepares a draft or suggests a level for
  a person to check, after that person has recorded their own.
- **Records about a named person live here and nowhere else.** Not in the
  Team Hub database, not in its backups, not in Slack, GroupMe, email or
  the server logs.

The legal background (the Ontario rules that shaped each screen, the
wording to use and when to call a lawyer) is in
[legal-counsel/hiring-and-leader-reviews.md](legal-counsel/hiring-and-leader-reviews.md).
It is legal information, not legal advice.

## Who can see what

| Person | Sees |
|---|---|
| **Operator** (master login) | Everything: both features, Who has access, Our standards, Settings, Activity log, Backup. |
| **Hiring leader** (you ticked *Hiring*) | Every posting and every applicant: answers, contact details, interviews, notices, the numbers. Not publishing or closing a posting, the AI switch on a posting, reopening an application, taking a "do not delete" hold off, adding a hire to the roster, the hiring-records download or Retention: those stay with you. |
| **Reviewer** (you ticked *Leader reviews*) | Only the leaders you assign to them: their observation log and their review. Never their own review, never calibration. |
| **Any leader** | Their own review once it has been shared with them, and their own reflection when a cycle asks for one. Nothing else. |
| **Team members** | Nothing. A clock-in sign-in is sent back to the Hub. |

Being an **admin leader opens nothing here**. Only your tick does. A tick
is tied to the leader's sign-in number *and* name together, so if Team Hub
is ever restored and leaders are renumbered, old ticks open nothing until
you give them again (the access page lists them as "No longer matching a
sign-in").

In the ☰ menu, the Operator and ticked leaders get a **People** row, with a
count when something is waiting (a review on hold, new applications, a
decision notice due). A leader gets a **My review** row from the day a
cycle asks them for a reflection or a review is shared with them. It shows
"waiting" while something needs them, and it stays after they have
confirmed a review, so they can always read it again.

## The lock (why it asks for your PIN again)

People pages hold private information about people, so being signed in to
Team Hub is not enough: the first People page asks for **your PIN again**
(the same PIN you sign in with). It then stays open while you use it and
locks itself after **30 minutes without a tap**. Change the minutes under
Settings → The lock (5 to 120). **Lock now** is at the bottom of every
People page: tap it before you hand the phone or the office computer to
someone else.

Two things to know:

- Wrong PINs here count toward the same lockout as wrong PINs at sign-in.
- If the lock closes while a form is open, that form is **not saved**. You
  are asked for your PIN and brought back to the page to do it again. Save
  as you go on a long review.

## First-time setup, in order

1. **Check the disk.** People keeps its records in `people.db`, beside the
   Team Hub database on the Render persistent disk. If `SHIFT_DB_PATH` is
   not set, a red banner on People pages says records will not survive a
   deploy: fix that first (see [shift-leading-app.md](shift-leading-app.md)).
2. **Open it.** ☰ → **People** → enter your PIN.
3. **Who has access.** Tick *Hiring* and *Leader reviews* for the leaders
   who should have them. One tap each; removing a tick takes effect on
   their next tap.
4. **Our standards.** Load the store's standards file (next section).
   Reviews and hiring scorecards both need it.
5. **Settings.** Set the **hiring contact** (a name, plus an email or a
   phone number): no posting can go live without one, because the
   accommodation notice needs a named person. Check the two **minimum wage**
   figures against ontario.ca. Leave both AI switches **off** for now.
6. **Leader reviews → Leaders.** *Add every leader* puts every active
   Team Hub leader under review with you as the reviewer. Then set each
   one's title, area of ownership and reviewer.
7. **Tell your leaders before the first cycle.** Wording to adapt is under
   Settings → AI drafting for leader reviews → *Wording you can use to tell
   leaders*, and in the legal reference, Part C.

## Our standards

One model, shared by reviews and hiring: the store's values, standards and
expectations, each described at **four levels**. It is the Operator's own
wording, loaded here from a file. **It is not in this repository**, which
is public.

- **The file** is a JSON text file: a name, four level names (lowest
  first), and one to eight groups of standards (30 standards at most). Each
  standard has a short key (like `guest-care`), a name, a definition, four
  level descriptions, optional "what counts as evidence" examples, and
  optionally a hiring question with its own four level descriptions.
- **To load it:** People → Our standards → choose the file → **Check and
  load**. If anything is wrong the page lists every problem in plain words
  and changes nothing. **See an example file** downloads a small neutral
  example to copy the shape from.
- **To change wording:** open a standard on the page → *Change this
  wording* → **Save as a new version**. Or download the current file, edit it and load it again.
- **Versions.** Every save is a new version and the newest one is the one
  in use. A review keeps the version it was started with, so a review
  already written never changes wording under anyone. A posting copies a
  standard's wording in at the moment you pick it.
- Leaders you have ticked can read the standards. Only you can change them.

Before a first cycle, read each standard once with one question in mind:
*is this about behaviour at work that someone could see or count?* A
standard about belief, personality or "fit" has to be reworded first.

## Leader reviews, end to end

A review is a conversation starter, not a verdict. There is **no overall
score, no ranking, and no comparison of one leader with another** anywhere
in it.

### Through the period: observations

Leader reviews → a leader's **Observation log** → *Write down what you
saw*: the date you saw it, the kind (Did well / To work on / Note), which
standard it is about, and what happened. Tap the microphone on your
keyboard to talk instead of type.

- The **What to write, and what never to write** box sits on the page.
  Work conduct only: never health, family, leave, religion, age, or that
  someone raised a concern.
- A plain word-list check runs on every note and shows a flag above it if
  it finds a word to read again. It never blocks; it is yours to fix.
- **Leave out of the review** keeps a note on the log without counting it.
  **Remove** takes it off the log (who removed it, and when, is kept).
- Notes that count toward a review are **shown to the leader with that
  review**. Write each one as something you would say to them.

### A cycle

Leader reviews → **Start a review cycle**: a name, the period it covers,
the day reviews are due, and whether to **ask leaders for a reflection
first**. Starting it makes a draft review for every leader under review, on
the standards as they are worded that day, assigned to that leader's
reviewer. A leader added later can be added to an open cycle from the
cycle's page.

If you asked for reflections, each leader sees **My review** in their menu
and writes how they see their own work against each standard. Only they
see it until they send it. Their reviewer then reads it beside the
evidence. Writing it is paid time: the page says so.

### Writing the review

Open the leader's review. For **one standard at a time** you see the
store's definition, what each level looks like, the notes from the period
filed under it, and then choose a level or **Not enough to rate yet**
(always a fair answer, never a mark against the leader), with a comment.
Then:

- **Strengths** and **growth focus**, in your own words.
- **Agreed actions.** Each needs an action, a measure, a target and a day
  it gets checked. One without all four is refused.
- **Time away.** Dates only, never the reason. Notes dated inside those
  dates stop counting toward the review.

### The AI draft (only if you have switched it on)

**Ask the AI for a draft** sends the notes for the period to the AI service
with names taken out (see *AI* below). What comes back is a second read of
the same notes:

- The draft for a standard appears **only after you have saved your own
  rating** for that standard. Suggested wording for the summary and
  coaching questions appear only after every standard is rated.
- It is labelled **"Draft prepared with AI assistance. Not a decision."**
- Our code, not the AI, decides what is kept: a suggested level with no
  note behind it is dropped; a note the AI cites that was never sent is
  dropped; a sentence that uses a word about health, family, age or the
  like is taken out of the draft and shown as a line for you to check, and
  the suggested level for that standard is withheld.
- Nothing is copied into the review for you. You write it.

### Before you finish

**Check and finish** lists anything unrated, shows any wording flags (tick
that you have read them) and warns about a level with no notes behind it.
Then it asks six questions about the review period: leave of any kind, an
accommodation, a known or suspected health condition or disability,
pregnancy or a recent return from it, a workplace injury or WSIB claim, and
any concern or complaint the leader raised.

- **All "No"** → the review is **finalised**.
- **Any "Yes or not sure"** → the review goes **on hold**. Nothing is
  wrong: it means you look at it with the reviewer, outside the tool, so
  that nothing protected counts against anyone. Nothing on it can be
  changed, and it is not to be discussed with the leader yet.

Only you can see which question stopped it, and only you can release it.
Releasing records one of two fixed sentences (never the reason) and makes
it a draft again; the reviewer is then asked one question, whether
anything has changed since. **Before releasing a held review, read the
note on that page about the escalation matrix**: a review of someone who
is pregnant, on or just back from a leave, has a disability or an
accommodation, has a WSIB claim or has recently raised a concern needs a
lawyer's read before it is used toward any consequence.

### Calibration and process health (Operator)

- **Calibration** lays one cycle out as leaders (alphabetical, never by
  level) against standards, with the number of notes behind each level.
  Below it: how each reviewer spread their levels, as questions to ask and
  not verdicts, and where the team as a whole sits on each standard. You
  can change a level on a **finalised, not yet shared** review. It needs a
  reason, and the reviewer's own level and your reason are kept.
- **Process health** shows how well the process itself is being run:
  finished and on time, the spread of levels, how many notes sit behind a
  rating, how often a saved level matched the AI suggestion, actions done
  by their check date. Counts about the process, never about one leader.
- You can **reopen** a finalised review while its cycle is open.
  **Closing a cycle** freezes any draft still unfinished; finished reviews
  can still be shared and confirmed.

### Sharing, and the leader's side

**Share with …** on a finalised review puts it under the leader's **My
review**. They see the level for each standard with the store's
description of that level, the comments, the notes it is based on,
strengths, growth focus and next steps. They never see AI output, the
"before you finish" answers, or that a review was ever on hold.

After you have talked it through, the leader confirms on their own
sign-in: the date it was discussed, optional comments, and this wording:

> I confirm that I received this review and that it was discussed with me
> on [date]. This confirms receipt and discussion only. It does not mean I
> agree with the review. I can add comments below, and they will be kept
> with this review.

If their comments use words about harassment or safety, you (only you) see
a quiet note on the review that it may need follow-up. The leader is never
stopped or questioned by the tool.

**Print view** (finalised reviews onward) carries the same content with
signature lines and "Leader declined to sign; copy provided on ____". Use
it for a leader with no Team Hub sign-in. Version 1 cannot record a paper
signature: keep the signed copy in the office file.

## Hiring, end to end

### A posting

Hiring → Postings → **New posting**: title, part-time or full-time, the
description, the lowest and highest hourly wage, whether the lower figure
is the student minimum wage, and whether it is for an existing vacancy or
for future openings. Then **what you will score applications on**: pick
standards from Our standards (their hiring questions come with them) or
write a criterion by hand. A criterion with a question is asked of the
applicant; one without is for the interview only. Two to six applicant
questions.

**The publish check** has to pass before a posting can go live, and has to
keep passing while it is live:

- a title, and a description of at least 200 characters;
- both pay figures, lowest not above highest, not below the minimum wage
  in Settings (the student rate only with the student box ticked), and a
  range no wider than $50,000 a year;
- existing vacancy or not;
- a hiring contact in Settings;
- at least two criteria with questions.

It also flags words to read again ("young", "recent grad", "strong",
"Canadian experience", gendered job titles, tips or bonuses as pay). They
do not block, but you tick that you have read them and the wording is
about the job.

**Only the Operator publishes.** A live posting shows, in this order: the
title, the employer, the type, the pay, the description, what we look for,
the vacancy statement, the AI statement (only while AI is really in use
for it), the accommodation notice with the hiring contact, the minimum-age
line and the Apply button.

**An exact copy is kept** every time: when it is published, each time it is
changed, when it is closed, and when a setting it prints has changed (the
hiring contact, the employer name, the months applications are kept),
taken the next time the careers page is opened. Each copy is the posting as
shown plus the blank application form. Nothing deletes them. A closed posting cannot be
reopened; **Copy to a new draft** starts the next one.

### The careers pages

`https://<your Render address>/careers/` lists the live postings, and each
posting has its own address, shown on the posting's page under *Applicants
apply here*. Share that link, or turn it into a QR code for a poster.
**Anywhere else the job is advertised** (a job site, a social post, a
poster) has to carry the same pay, vacancy, AI and accommodation statements
or link straight to that page.

Applying takes two short steps on a phone:

1. **About a minute.** Name, a phone number or an email, "Are you legally
   entitled to work in Canada?", "Are you 18 or older?", when they could
   work (never why not), how they heard about us, and any experience they
   want to mention. They read how their information is looked after and
   tick to agree. The form asks for nothing else: no date of birth, photo,
   address, gender, family, religion, SIN or health.
2. **About five minutes.** The posting's quick questions, answered once, on
   a **private link** that is theirs alone. The link takes answers for 7
   days (Settings). After that, and any time later, the same link shows
   only that we have their application and, once you have logged a notice,
   whether a hiring decision has been made. It never shows a score, a note
   or a stage.

The same person applying to the same posting again within 30 days is
thanked and not stored twice. Only a scrambled form of the private link is
kept, so a lost link cannot be sent again: the applicant contacts the
hiring contact instead.

One limit to know: like any web address that is opened, a private link's
address can show up in the hosting service's own request log. The page
behind it never shows the applicant's name or contact details, a score or
a note, and once the questions are answered it only shows the status lines
above.

### The pipeline

Hiring opens on the **Pipeline**: each live posting with its applicants by
stage (New → Screening → Interview → Offer → Hired), listed by how long
they have waited, never by score. **Every move is a person's tap.**

- **Not selected** needs a reason from a short, job-related list. If the
  real reason touches a disability, an accommodation request, availability
  limited by religion or a record of offences, stop and read the note
  beside the form: that is a call to a lawyer first.
- **Hired** comes only from Offer, and needs a tick that the offer checks
  are done.
- Only the Operator can reopen a not-selected or withdrawn application.

An applicant's page is a **scoring screen**: their written answers, each
beside the criterion's definition and level descriptions, with your own
level and a note. Other leaders' levels show only after you have saved
yours. There is no total and no average. The page never shows the email,
phone number, the 18-or-over or work-entitlement answers, or how they
heard about us. Those are on the separate **Contact and offer checks**
page.

### Interviews and the 45-day notice

Add an interview (date, time, in person / phone / video, who led it), then
record what happened. An **attended** interview does two things:

- it opens a **scorecard**: the same criteria, in the same order, for every
  applicant. A submitted scorecard is final, and you see other
  interviewers' scorecards only after submitting your own;
- it starts a **45-day clock**. Ontario law says anyone you interviewed
  must be told whether a hiring decision has been made within 45 days of
  their last interview. "No decision yet" is a valid notice, and is owed if
  that is the truth on day 45.

**Notices** lists everyone who is owed one, soonest deadline first, overdue
in red; the People row in the menu shows the count. A second interview
inside the 45 days moves the deadline; interviews further apart each start
their own clock.

### Messages

On the Contact page, four messages are ready to copy: interview invitation,
offer, not moving forward, no decision yet. **Open in email** and **Open in
text messages** open your own mail or text app with the message filled in.
**The tool never sends anything itself.** After you have told the applicant
(by email, text, phone or in person), **Log this notice**: what they were
told, how and on which day. That record is kept for three years.

### The offer

Moving someone to Offer opens **Offer checks**: their two yes-or-no
answers, and the reminders (ask for proof of age only after a conditional
offer; minimum age 14 for serving areas and 15 for the kitchen; a trial
shift is paid work; give the new hire their written job information before
their first day; tell them about the accommodation policies). Record the
offer and their reply. Once they are **hired**, the Operator can tap **Add
to the Team Hub roster**: only their name is copied across.

### Numbers

Hiring → **Numbers**, for one posting or all, over 30, 90 or 365 days: the
funnel from application to hire, how many answered the quick questions,
days to hire and days in each stage, hours to first contact, where
applicants and hires heard about us, interview no-shows, offers accepted,
and decision notices on time, late, due and overdue. Every percentage has
its count beside it, and below five cases it says "not enough data yet".

### Retention (Operator)

Applicants who are not hired are told their application is kept for a set
number of months after the decision (24 by default, Settings) and that you
then remove it. **Nothing is removed automatically.** Hiring → **Retention**
lists who is due, and the People home page reminds you when anyone is.

- **Removed:** name, contact details, the yes-or-no answers, availability,
  experience, written answers, leaders' notes, the notes on scores, any AI
  copy of the answers and the private link.
- **Kept:** the bare record (posting, stages, dates, levels) so the numbers
  stay true; every decision notice until it is three years old; postings
  and their kept copies; anyone hired.
- **Hold: do not delete** on an applicant's Contact page exempts them. Use
  it the day you hear of a complaint or claim. Only you can take it off.
- An application on a closed posting that was never marked not selected,
  withdrawn or hired never becomes due. The page counts these: decide them.

**Download hiring records** (Postings page) gives one file with every
posting, every kept copy and every decision notice: what an employment
standards officer may ask to see.

Leader reviews have no removal routine in version 1: they are kept.

## AI: what is sent, what never is, and switching it on

AI is **off** until you switch it on, and each feature has its own switch
under People → Settings, with a short go-live checklist above it.

| | Leader reviews | Hiring |
|---|---|---|
| **Sent** | The wording of our standards; the observations that count toward the review; the leader's reflection if they sent one. | The posting's criteria and the applicant's written answers to the quick questions. |
| **Taken out first** | The leader's name; the names of everyone on the Team Hub roster, the leader list and the review list; email addresses and phone numbers; he/she words. | The applicant's name; the names of everyone on the roster and the leader list; email addresses and phone numbers; he/she words. |
| **Never sent** | The leader's name, title or area; the reviewer's own ratings and comments; time-away dates; the "before you finish" answers; anything from Team Hub activity (checklists, to-dos, training, read receipts, 1:1s). | Name and contact details; availability; how they heard about us; experience; the 18-or-over and work-entitlement answers; interview notes and scores; any answer that may mention health or an accommodation. |

Taking names out is done by matching the names Team Hub knows. A name it
does not know (a guest, a nickname) can slip through, which is one more
reason notes describe what happened and not who.

**To switch it on**

1. The server needs `OPENAI_API_KEY` (the same key the GroupMe bot uses).
   With no key, nothing is sent whatever the switches say, and Settings
   says so.
2. **Leader reviews:** go through the checklist (leaders told in writing;
   standards checked; the AI provider's data terms read; a first cycle run
   without AI), then *Switch on AI for leader reviews*.
3. **Hiring:** go through the checklist (a lawyer has looked at it; data
   terms read), then *Switch on AI for hiring*, **and** switch it on for
   each posting that should use it. That posting then tells applicants AI
   is used. AI only ever runs for applicants who applied **while the
   posting was saying so**. Switch it off, or lose the key, and the posting
   stops saying it and nothing more is sent.

Every request is kept on record exactly as it was sent, with the reply,
the model and the date: it is in the People backup. A request that has not
come back after three minutes shows as failed, and you can ask again. Which
of the provider's models is used is set in `people_ai.py`, unless
`PEOPLE_AI_MODEL` names another; each request's record shows the one it
used.

## Backups

People → **Backup** → *Download the backup* gives one file with everything
on People pages. **The Team Hub backup does not include any of it**, and no
scheduled job copies it anywhere, so this download is the only copy besides
the disk. Download it after each review cycle and after each hiring round.

- The file holds private information about people. Keep it somewhere only
  you can open. Never in email, a group chat, Slack or a shared drive.
- Keep the newest copy and delete the ones before it (an old copy still
  holds applications you have since removed), unless a complaint or legal
  claim is under way: then keep every copy and call the lawyer.
- **There is no restore in version 1.** The file is a safety copy; if the
  disk were ever lost, records would be rebuilt from it by hand.
- Every download is written in the Activity log, like every grant, setting
  change, open of a review or an applicant, and every change to either.
  The log records that something happened, never what was written.

## Environment variables (Render → Environment)

| Variable | What it is |
|---|---|
| `PEOPLE_DB_PATH` | Where the People database lives. Leave it unset and it is `people.db` beside the Team Hub database (`SHIFT_DB_PATH`), on the persistent disk. |
| `OPENAI_API_KEY` | The AI service key. Already set for the GroupMe bot. People uses it only while an AI switch is on. |
| `PEOPLE_AI_MODEL` | Optional. Which of the AI provider's models to use; the default in `people_ai.py` if unset. |

## Left out of version 1, on purpose

- **Team Hub activity as review evidence** (checklist sign-offs, to-dos,
  training, read receipts). Using it in reviews is electronic monitoring
  put to a new use: the store's electronic monitoring policy has to say so
  and every employee has to be given the changed policy first. Until that
  is done, reviews rest on what a reviewer saw and wrote down.
- **Resume uploads.** A resume brings a photo, an address, dates that show
  age and more that must not be collected or scored. The form asks only
  what the job needs.
- **Automatic emails or texts.** A person sends each message from their own
  app and logs it. Nothing reaches an applicant that a person did not read
  first.
- **Restore from backup.**
- **A careers QR poster.** The link on each live posting is ready to share;
  no poster is generated.
- Smaller things: recording a paper signature on a review; sending a lost
  private link again; removing old leader reviews; a limit on how often an
  AI draft can be asked for.

## For whoever maintains it

Files: `people.py` (the gate, the lock, the Operator's pages), `people_db.py`
(the separate store), `people_ai.py` (the AI engine), `people_reviews.py` +
`people_reviews_db.py`, `people_hiring.py` + `people_hiring_db.py`,
`careers.py` (public pages), templates in `templates/people/` and
`templates/careers/`. No scripts on any page; strict CSP. People registers
on its own after the Hub, so a fault in it never takes down `/shift`,
`/hub` or the bots. Tests: `tests/test_people_*.py` and
`tests/test_careers.py` (`tests/test_people_roles.py` is the
who-may-open-what table; `tests/test_people_privacy.py` walks both flows
and checks nothing private leaves the People store).
