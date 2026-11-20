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
count when something is waiting (a leader's comments to follow up, a
decision notice due, a live posting that no longer passes its check, a
review on hold, new applications). A leader gets a **My review** row from the day a
cycle asks them for a reflection or a review is shared with them. It shows
"waiting" while something needs them, and it stays after they have
confirmed a review, so they can always read it again.

## The lock (why it asks for your PIN again)

People pages hold private information about people, so being signed in to
Team Hub is not enough: the first People page asks for **your PIN again**
(the same PIN you sign in with). It then stays open while you use it and
locks itself after **30 minutes without a tap**, and in any case **8 hours
after you entered the PIN**. Change the minutes under Settings → The lock
(5 to 120). **Lock now** is at the bottom of every People page: tap it
before you hand the phone or the office computer to someone else.

Things to know:

- The lock is kept on the server, not only in the browser. **Lock now**,
  signing out of Team Hub, and signing in as someone else all close it at
  once, including for any copy of the browser's sign-in cookie.
- **Five wrong PINs hold the lock for ten minutes.** That count belongs to
  the lock and to your own sign-in: wrong PINs typed at the sign-in page,
  by you or by anyone else, never hold your People lock, and wrong PINs
  here do not lock your sign-in. The activity log notes each wrong PIN and
  each hold.
- The PIN box is deliberately not a password box, so a browser that saved
  your sign-in PIN does not fill it in for whoever is holding the phone.
  **On a phone or computer other people use, don't let the browser save
  your PIN.** This was not tried on the store's own devices when it was
  built: open People on each and check the box stays empty.
- If the lock closes while a form is open, that form is **not saved**. You
  are asked for your PIN and brought back to the page to do it again. Save
  as you go on a long review.
- Page titles never carry an applicant's or a leader's name, so the
  browser's history and tab list do not show who a page was about.

## First-time setup, in order

0. **Set `FLASK_SECRET_KEY` on the server first** (Render → Environment),
   to a long random value: `python3 -c "import secrets;
   print(secrets.token_hex(32))"`. **On the live server People does not
   load without it**: no People row appears in the menu, and the server log
   says "People is NOT loaded". The sign-in and the PIN lock both rest on
   the key that signs the sign-in cookie, and the key the app falls back to
   is worked out from `SCHEDULE_SECRET` and the Operator's PIN, which is
   not strong enough for records about people. Everyone signs in again
   once after it is set. `/shift`, `/hub` and the bots are unaffected.
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
   figures against ontario.ca and tap **Still correct today** under each
   (or change it): Settings shows when each was last confirmed, and after
   every 1 October it asks you to look again, on Settings, on the People
   home page and on each posting. Lowering a figure needs a tick. Leave
   both AI switches **off** for now. Before the first posting goes live,
   open *For whoever set up the server* at the bottom of Settings and check
   the address it shows is your own (see *The careers pages*).
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
with names taken out (see *AI* below). **A note whose wording touches on
health, family, age, religion, origin, gender, time away or a complaint is
not sent at all**, and neither is a box of the leader's reflection worded
that way: the page says how many were held back, and they are yours to read.
What comes back is a second read of the notes that were sent:

- The draft for a standard appears **only after you have saved your own
  rating** for that standard. Suggested wording for the summary and
  coaching questions appear only after every standard is rated.
- It is labelled **"Draft prepared with AI assistance. Not a decision."**
- Our code, not the AI, decides what is kept: a suggested level with no
  note behind it is dropped; a note the AI cites that was never sent is
  dropped; a sentence that uses a word about health, family, age or the
  like is taken out of the draft and shown as a line for you to check, and
  the suggested level for that standard is withheld.
- The "Human check needed" lines at the top of the draft are **fixed
  sentences written by our own code**. The AI can ask for one of four of
  them; anything else it writes there in its own words is never shown,
  because those lines are on the page before you have rated anything.
- **A draft does not outlive its notes.** If the notes that count change
  after a draft was made (you mark time away, leave a note out, remove one,
  add one, or the leader changes their reflection), the draft is no longer
  shown and the page says to ask for a new one.
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
  reason, and the reviewer's own level and your reason are kept. The
  reviewer's comment for that standard was written for the level before,
  so read it before you share: the leader's copy says "This level was set
  by the Operator after reading all the reviews together" under that
  standard, with no reason and no earlier level.
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
"before you finish" answers, that a review was ever on hold, or why a
level was changed in calibration.

After you have talked it through, the leader confirms on their own
sign-in: the date it was discussed, optional comments, and this wording:

> I confirm that I received this review and that it was discussed with me
> on [date]. This confirms receipt and discussion only. It does not mean I
> agree with the review. I can add comments below, and they will be kept
> with this review.

If their comments use words about harassment or safety, you (only you) see
a note on the review that it may need follow-up. It is also listed on the
Leader reviews page, on the People home page and in the menu count, **and
it stays there, even after the cycle is closed, until you tick "Followed up
outside this tool"** on the review. Only the tick and the date are kept,
never what you found. The leader is never stopped or questioned by the
tool. Their reviewer can read the comments too (the leader's page says so),
which is why a comment about the reviewer or another senior leader is a
call to a lawyer first: the note on the review says so.

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

**The publish check** has to pass before a posting can go live, and a
change to a live posting is refused if it would make it fail:

- a title, and a description of at least 200 characters;
- both pay figures, lowest not above highest, not below the minimum wage
  in Settings (the student rate only with the student box ticked), and a
  range no wider than $50,000 a year;
- existing vacancy or not;
- a hiring contact in Settings;
- at least two criteria with questions;
- no question, for applicants or for the interview, that asks what an
  application may not ask: age or date of birth, pregnancy, marriage or
  children, religion, citizenship or where someone was born, health or
  disability, a criminal record, gender, a SIN, a photo or a licence. The
  check works from a list of phrases, so it catches the plain cases and
  nothing subtler: **read every question yourself before you publish**;
- if AI assist is ticked for the posting, AI that can really run (see *AI*
  below), so the posting goes live with its AI statement.

It also flags words to read again ("young", "recent grad", "strong",
"Canadian experience", gendered job titles, tips or bonuses as pay, and any
word about health, family, religion, age, origin, gender or leave). They
do not block, but you tick that you have read them and the wording is
about the job.

**A setting can leave a live posting failing** without anyone touching the
posting: the minimum wage goes up on 1 October, or the hiring contact
changes. Settings names the postings the moment you save, the People home
page and the menu count show them, and the Hiring page says what is wrong.
**The posting stays public and keeps taking applications until you fix it
or close it**, so do that the same day. The hiring contact cannot be
emptied while a posting is live.

**Only the Operator publishes.** A live posting shows, in this order: the
title, the employer, the type, the pay, the description, what we look for,
the vacancy statement, the AI statement (only for a posting that has used
AI since the day it went live, and only while AI is really in use), the
accommodation notice with the hiring contact, the minimum-age
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
**Anywhere else the job is advertised** (a job site, a social post that
names the job, a poster) has to carry the same pay, vacancy, AI and
accommodation statements itself. A general "we're hiring" sign that names
no position can simply point to the careers page.

Applying takes two short steps on a phone:

1. **About a minute.** Name, a phone number or an email, "Are you legally
   entitled to work in Canada?", "Are you 18 or older?", when they could
   work (never why not), how they heard about us, and any experience they
   want to mention. They read how their information is looked after and
   tick to agree. This step asks for nothing else: no date of birth, photo,
   address, gender, family, religion, SIN or health.
2. **About five minutes.** The posting's quick questions (the ones you
   wrote or picked, which the publish check screens), answered once, on
   a **private link** that is theirs alone. The link takes answers for 7
   days (Settings). After that, and any time later, the same link shows
   only that we have their application and, once you have logged a notice,
   where it stands: "A hiring decision has not yet been made", "A hiring
   decision has been made for this posting" (only for someone who was
   interviewed or offered the job), or, for someone never interviewed, "We
   will not be moving forward with your application". It never shows a
   score, a note or a stage.

**An application that gives the same email or phone number as an earlier
one** to the same posting (within 30 days) is taken like any other, with
its own private link, and marked **Possible duplicate** for you: the
pipeline shows the badge and each applicant's Contact page links to the
other. It may be one person applying twice, or two people who share a
phone. If it is one person, move the spare one to withdrew. The form never
tells the sender that those details were used before, because that would
tell anyone who typed someone else's email whether that person had
applied. Only a scrambled form of the private link is kept, so a lost link
cannot be sent again: the applicant contacts the hiring contact instead.

**More time, or another way to answer.** The posting promises
accommodation at every stage, and the quick questions are a stage. On an
applicant's Contact page, **Give more time on their link** opens their link
again for the usual number of days, and **Enter answers they gave another
way** records what they told you by phone, on paper or with someone's help.
Either way the answers are scored like any others. Don't ask why, and don't
write why: the history keeps who did it and when, nothing else.

The careers pages limit how many applications one address can send in an
hour (and how many wrong private links it can try). The address counted is
the one the hosting platform's proxy reports, never one a visitor can type.
**Check it once before the first posting goes live:** Settings → *For
whoever set up the server* shows the address being counted for your own
request. It should be your own (what a "what is my IP" site shows). If it
is not, set `PEOPLE_PROXY_HOPS` (below).

One limit to know: like any web address that is opened, a private link's
address can show up in the hosting service's own request log. The app
itself never writes it anywhere, including when a careers page fails (the
log then gets one fixed line naming the kind of error and nothing else).
The page behind the link never shows the applicant's name or contact
details, a score or a note, and once the questions are answered it only
shows the status lines above.

Where a posting's lower pay figure is a student rate, the list of
positions and the top of the form print it as "$16.90 (student rate) to
$19.50 an hour"; the posting itself carries the full sentence about who
the student rate applies to.

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

Log **every** interview that was held. One that was held before the
decision was recorded can still be added afterwards (a decided application
takes a past-dated interview, never a new one). Changing an **attended**
interview to cancelled, rescheduled or no-show while its notice is still
owed takes that notice off the list, so it asks for a tick that the
interview was not held, and once the notice is overdue only the Operator
can do it.

**Notices** lists everyone who is owed one, soonest deadline first, overdue
in red; the People row in the menu shows the count. Our own aim is 30
days, and the list marks anyone past it. A second interview inside the 45
days moves the deadline; interviews further apart each start their own
clock. **Someone told "no decision yet" stays on the Notices page**, under
*Told "not yet": follow up when decided*, until an offer or a
not-moving-forward notice is logged for them, and the Hiring page and the
menu count say so once their application has been decided. Whether the law
requires that second notice is not settled, so we send one.

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
  stay true; every decision notice for three full years after the day it
  was given (the same button then removes its wording and the name, and
  says how many it removed); postings and their kept copies; anyone hired.
- **Hold: do not delete** on an applicant's Contact page exempts them, and
  keeps the wording of their decision notices too. It can be put on even
  after the details have been removed. Use it the day you hear of a
  complaint or claim. Only you can take it off.
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
| **Taken out first** | The leader's name (a two-letter name such as Jo only where it is written with a capital); the names of everyone on the Team Hub roster, the leader list and the review list; email addresses and phone numbers; he/she words. | The applicant's name; the names of everyone on the roster and the leader list; email addresses and phone numbers; he/she words. |
| **Never sent** | The leader's name, title or area; the reviewer's own ratings and comments; time-away dates; the "before you finish" answers; any note, or box of the reflection, whose wording touches on health, family, age, religion, origin, gender, time away or a complaint; anything from Team Hub activity (checklists, to-dos, training, read receipts, 1:1s). | Name and contact details; availability; how they heard about us; experience; the 18-or-over and work-entitlement answers; interview notes and scores; any answer that may mention health or an accommodation. |

Taking names out is done by matching the names Team Hub knows. A name it
does not know (a guest, a nickname) can slip through, and so can a
two-letter name typed without its capital or a single initial, which is one
more reason notes describe what happened and not who. Holding back notes
about health and the like is done by a word list too: it errs on the side
of not sending (a note about a guest's "family" is held back as well), and
it cannot catch what the list does not name.

**To switch it on**

1. The server needs `OPENAI_API_KEY` (the same key the GroupMe bot uses).
   With no key, nothing is sent whatever the switches say, and Settings
   says so.
2. **Leader reviews:** go through the checklist (leaders told in writing;
   standards checked; the AI provider's data terms read; a first cycle run
   without AI), then *Switch on AI for leader reviews*.
3. **Hiring:** go through the checklist (a lawyer has looked at it; data
   terms read), then *Switch on AI for hiring*, **and** switch it on for
   each posting that should use it **while that posting is still a draft**.
   The posting then tells applicants AI is used from the day it goes live.
   It cannot be switched on for a posting that is already live: a posting
   that has been public without its AI statement never uses AI. To use AI
   for that job, close the posting and copy it to a new draft. AI only ever
   runs for applicants who applied **while the posting was saying so**.
   Switch it off, or lose the key, and the posting stops saying it and
   nothing more is sent; if anyone opens the posting while it is not saying
   it, AI assist is off for that posting for good.

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
| `FLASK_SECRET_KEY` | **Required on the live server** (32 characters or more): the key that signs the sign-in cookie. Without it People does not load there. Any `ENV` other than `test`, `dev`, `development` or `local` counts as the live server, and there the sign-in cookie is also marked HTTPS-only. |
| `PEOPLE_DB_PATH` | Where the People database lives. Leave it unset and it is `people.db` beside the Team Hub database (`SHIFT_DB_PATH`), on the persistent disk. |
| `OPENAI_API_KEY` | The AI service key. Already set for the GroupMe bot. People uses it only while an AI switch is on. |
| `PEOPLE_AI_MODEL` | Optional. Which of the AI provider's models to use; the default in `people_ai.py` if unset. |
| `PEOPLE_PROXY_HOPS` | Optional, 0 to 5, default 1. How many of the hosting platform's proxies stand in front of the app: the careers pages count their limits against the address that many places from the end of `X-Forwarded-For` (0 = the connection itself). Never set it so that it points at an entry a visitor could have typed. Check it on Settings → *For whoever set up the server*. |

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
and checks nothing private leaves the People store;
`tests/test_people_findings.py` has one or more tests for each thing two
independent reviews of the finished section found, named by finding).
