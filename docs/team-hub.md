# Team Hub — setup & guide

**Team Hub** is the one app for Chick-fil-A Wharncliffe & Wonderland: the
leader tools that used to be "Shift Lead" and the store's "here's how we do
it" library, under one name, one sign-in and one look (styled from
chick-fil-a.ca's own design: white masthead, the round logo on a hairline, a
red ☰ menu of big navy section words, red headlines). Same Render service,
same database, same backups.

**The app is `/hub/`** — that's what the home-screen icon, the crew-room
poster and new installs open. The office `/shift` poster, old "Shift Lead"
home-screen icons and every old link keep working.

## The sections

| Section | Who | What's there |
|---|---|---|
| **Today** (leaders) / **Home** (team members) | everyone | Leaders: a calm start — today's leadership goal and operational goal (private to them and the Operator), their open to-dos, and a tile into each section. Team members: their required reading, the categories, what's new. |
| **Systems & Procedures** | everyone (filtered) | HR, FOH Operations, BOH Operations, Leadership Development (leaders only) — pages, required reading, search, editor tools. |
| **Checklists** | leaders | Daypart lists, station cleaning sheets, scorecard, past days. |
| **Lineups & Setups** | leaders | The position lineup per daypart. |
| **Goals** | leaders | Store goals and updates. |
| **Meetings** | leaders | Your 1:1 agenda with the Operator, 1:1s with your team, to-dos. Private to each leader and the Operator. |
| **To-dos** | leaders | Every leader's to-do list: assign one, see open and overdue, check yours off. A tile on Today and a word in the ☰ menu. |
| **Leadership Development** | leaders | The 48-lesson course, plus the leadership library pages. |

Guest recovery and Equipment & repairs are tiles on Today (a red count
while a guest is waiting to be reached, or something broken hasn't been
called in) and in the ☰ menu. Shift notes, announcements, shout-outs, the
roster and history are in the ☰ menu; admin leaders also see Manage pages and Leader
logins & checklist templates; the Operator also sees Backups, Team Hub
access (clock-in numbers) and Categories.

Bottom tabs — leaders: Today · Checklists · Lineups · Procedures · Meetings;
team members: Home · Procedures · To read · Me. On the office computer the
tabs become a row under the logo.

## Signing in (one front door)

- **Team members:** first name + clock-in number (given by the Operator — see
  *Giving the team access* below). A clock-in sign-in only ever opens team
  member content; trying a leader page lands back on their Home.
- **Leaders:** tap **Shift leader**, pick your name, enter your PIN. The
  Operator signs in as **Operator** with the `SHIFT_ADMIN_PIN` (kept in
  Render → Environment; changing it signs every Operator session out).
- **Shared phone or computer:** tick it at sign-in (do this on the office
  PC). The sign-in then ends when the browser closes or after 30 minutes
  away, instead of lasting 30 days.
- If the Systems & Procedures part ever failed to load, leaders can still
  sign in at `/shift/login` and run the shift.

## Systems & Procedures

- **Categories** (seeded on first boot, editable under Manage): **HR**,
  **FOH Operations**, **BOH Operations**, **Leadership Development**
  (leaders only). Each category has **sections** — Training, Procedures,
  Food safety, whatever fits — so FOH/BOH training lives inside Operations.
- **Pages** — written in a simple format (headings, bullets, numbered steps,
  bold, links, photos). Start blank, or **import a Word document** as a
  draft. Photos and PDFs show on the page; Word/Excel/PowerPoint files
  download.
- **Drafts → publish.** New pages are drafts only editors can see. Every
  publish is kept as a version (History shows each one exactly as it was).
- **Required reading.** Tick "Required reading" and the page lands on
  everyone's **To read** list. Each person taps *"I've received and read
  this version"*; the record keeps their name, the time, the wording and a
  fingerprint of exactly what the page said. Editors see **Who's read it**
  and can download every confirmation as CSV. When you change a page,
  choose *small fix* (no re-read) or *we changed how we do it* (everyone
  re-reads, with your "what changed" note shown to them).
- **Search** across titles, summaries, "also called" words, page text and
  Word attachments.
- **Yearly review** (e.g. the workplace harassment policy — OHSA requires
  an annual review): tick it and the page shows under Manage when due.
- **Lawyer review flag** — any editor can flag a page; it can't be
  published until the Operator clears the flag with a note.

### Who can do what

| | Team member | Leader | Admin leader | Operator |
|---|---|---|---|---|
| Sign in | first name + clock-in number | PIN | PIN | master PIN |
| Read published pages | ✓ (not leaders-only) | ✓ all | ✓ all | ✓ all |
| Drafts, write, publish | | | ✓ | ✓ |
| Publish / change **HR** (policy) pages | | | drafts only | ✓ after the review checks |
| Clear a lawyer-review flag | | | | ✓ |
| Give / remove clock-in access, categories | | | | ✓ |

Leaders reach Systems & Procedures from the Procedures tab or the ☰ menu
— it's the same sign-in as the rest of Team Hub.

## Giving the team access

Manage → **Who can sign in** (Operator only). The list is the team roster
from the Shift Lead app.

- Type a person's clock-in number and tap **Give access**, or use **Add many
  at once**: one person per line, `Name, clock-in number`. A first name
  alone works when only one person on the roster has it; tick "add names
  that aren't on the roster yet" for new hires.
- **Clock-in numbers act as passwords.** Type them into this page only —
  never into a chat, email or Slack. They're stored scrambled (keyed
  HMAC), never shown again, never written to logs, and left out of backups.
  The same number can't be given to two people (catches typos).
- **Remove** signs the person out on every phone they used, for good —
  giving the same number back later means signing in again. Changing their
  number, or making them inactive on the roster, does the same.
- Sign-in tries are limited: 5 misses locks a first name for 10 minutes (a
  successful sign-in doesn't reset it), and
  50 misses store-wide pauses clock-in sign-ins for 10 minutes (leaders
  aren't affected). A clock-in number is a short, guessable credential —
  fine for "how we do it" content, which is why the Hub must never hold
  anything about a specific person.

## Writing pages

- **Never records about a person.** No discipline, wages, medical or
  accommodation details, availability, contact info, SINs or clock-in
  numbers; no photos of people or of paperwork with personal info. The
  Hub is for how we do things. (Personnel records stay in HR files.)
- Formatting: `# Heading`, `## Smaller heading`, `- bullet`, `1. step`,
  `**bold**`, `_italic_`, `~~crossed out~~` (what a procedure *used* to
  say), `[link text](https://…)` or a Hub page `[Opening](/hub/page/12)`.
  A table is lines of `| cell | cell |`; put `|---|---|` under the first
  line to make it the header row (write `\|` for a pipe inside a cell).
  Wide tables scroll sideways on a phone. Upload photos to a page, then put
  `[photo: 1]` on its own line to place the first one (unplaced photos show
  at the bottom). Tap **Preview** before saving.
- Word import keeps headings, lists and tables; it leaves out hidden text,
  deleted tracked changes and comments (and says so). Old `.doc` files:
  open in Word → Save As `.docx` first.
- **Library bundles** (Operator only, Manage → *Import a library bundle*):
  many pages at once. A bundle is one `.txt`/`.md` file holding the pages
  separated by a line of five `=` signs, a `.zip` of one `.md` per page, or
  several `.md` files picked together. Each page starts with a header
  between `---` lines — `category:`, `section:`, `title:`, optional
  `summary:` / `keywords:`, `publish: yes` (or `no` to leave a draft),
  `leaders_only: yes/no` — then the page text in the format above. Pages go
  in file order; a title already in its category is skipped, so importing
  the same bundle twice is safe; pages for a policy category (HR) always
  arrive as drafts for the Operator's review checks. On a phone use the
  one-file `.txt` form, or paste the whole text into the box under the
  file picker: phones unzip a downloaded `.zip` and then won't let you
  pick the folder. This is
  how the Chick-fil-A Operational Requirements Guide was loaded: that text
  is confidential to the Chick-fil-A system, so it travels as a bundle the
  Operator uploads, never through the (public) code repository.
- Files: PDF, JPG/PNG/GIF/WebP, and Word/Excel/PowerPoint without macros,
  up to 15 MB each. The type is checked from the file itself, not its name.
- Photo numbers are fixed when a photo is uploaded and never reused, so
  removing photo 1 can't make `[photo: 2]` show the wrong picture.
- On a **published** page, adding or removing a file is a change people
  read, so it makes a new version (with the same "small fix / everyone
  re-reads" choice, and the policy checks on HR pages). A removed file stays
  in the page's History; the Operator can **Delete for good** from History
  if it should never have been uploaded.
- If someone else saves a page while you have it open, your save (or
  publish) is refused instead of overwriting their change — your text is
  kept on screen to copy. Publishing a draft publishes exactly the version
  you were looking at.

## HR and policies (Ontario)

The HR category is a **policy category**: admin leaders can draft, but only
the Operator publishes or changes a published page, after three checks (no
personal info; checked against current Ontario rules and the
`docs/legal-counsel/` notes; a lawyer has reviewed anything that changes
someone's rights). Escalate per [the escalation
matrix](legal-counsel/escalation-matrix.md) before publishing anything Tier 2.

Keep in mind:

- The Hub **doesn't replace** the physical postings the law requires (the
  ESA poster, the OHSA poster, the workplace violence and harassment
  policies) — every HR page shows a footer saying so, plus "need this in
  another format? Ask a leader" (AODA).
- **Required reading is paid work time.** The To read list says so; make
  sure people can do it on shift.
- The **electronic monitoring policy** (required at 25+ employees) should
  mention that the Hub records sign-ins and read confirmations.
- New-hire paperwork that names the person (offer letters, the ESA
  information they must receive, tax forms) doesn't belong here — the Hub
  can hold the generic version only.

## Backups

The Shift Lead backup (Admin → Download backup) now includes Hub
categories, pages, versions, read confirmations, reviews and the activity
log. It does **not** include:

- **Clock-in access.** On a restore, each person's access is re-attached by
  name if they're on the restored roster; team members and leaders sign
  in again.
- **Uploaded files.** They live on the persistent disk next to the database
  (`hub_files/`, or `SHIFT_HUB_FILES_DIR`). A restore onto a new disk keeps
  the page text; the files themselves need re-uploading.

A backup from before the Hub existed leaves the Hub as it is.

## Environment

Nothing new is required: the Hub uses `SHIFT_DB_PATH` (its files go in
`hub_files/` beside the database, on the Render disk), `SHIFT_ADMIN_PIN`
and `FLASK_SECRET_KEY` like the Shift Lead app. Optional:
`SHIFT_HUB_FILES_DIR` to put files elsewhere. Changing `SHIFT_ADMIN_PIN`
now signs every Operator session out.

## QR poster and app icon

`assets/hub-qr/` has a branded QR code and an 8.5×11 poster (PNG + PDF)
pointing at the live `/hub` URL — print it for the crew room. The
home-screen icon and the masthead logo are the round logo Josh supplied
(`assets/cfa-round-logo.png`, used as-is); `scripts/generate_app_icons.py`
rebuilds `static/teamhub/` from it.
`scripts/generate_hub_qr.py` regenerates it (pass a different URL if the
service ever moves).

## Security notes

- Page text is escaped before formatting is applied, so nothing typed into
  a page can run as code; links are limited to `https`, `mailto`, `tel` and
  Hub pages. Hub pages run no scripts at all and send a strict
  Content-Security-Policy, `no-store` caching, `nosniff` and no framing.
- Files are served only to people who can see their page, from random
  names on disk, with the type checked on upload.
- Every change (drafts, publishes, retirements, files, access grants,
  counsel flags, reviews) is written to the activity log (Manage).
- Sessions are signed cookies (30 days), shared with Shift Lead; a backup
  restore signs leaders and team members out, and changing
  `SHIFT_ADMIN_PIN` signs the Operator out.

## Development

- Shared look and navigation: `templates/shared/` (`cfa_style.html` design
  tokens + components, `_chrome.html` masthead/tab bar, `menu.html`,
  `front_door.html`), `teamhub_nav.py` (each role's tabs and menu — links
  only where that role may go), `teamhub_ui.py` (icons, section art,
  progress rings).
- Leader tools: `shift_app.py`, `shift_db.py`, `templates/shift/`.
- Systems & Procedures: `hub.py` (routes), `hub_db.py` (data), `hub_text.py`
  (formatting + Word import), `templates/hub/`.
- Tests: `python -m pytest tests/` — `tests/test_roles.py` checks every page
  against every role.
