# Team Hub — setup & guide

The store's "here's how we do it" library, at **`/hub`** on the same Render
service as the Shift Lead app. Systems, processes, training and policies for
Chick-fil-A Wharncliffe & Wonderland, on everyone's phone.

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

## Who can do what

| | Team member | Leader | Admin leader | Operator |
|---|---|---|---|---|
| Sign in | first name + clock-in number | Shift Lead PIN | Shift Lead PIN | master PIN |
| Read published pages | ✓ (not leaders-only) | ✓ all | ✓ all | ✓ all |
| Drafts, write, publish | | | ✓ | ✓ |
| Publish / change **HR** (policy) pages | | | drafts only | ✓ after the review checks |
| Clear a lawyer-review flag | | | | ✓ |
| Give / remove clock-in access, categories | | | | ✓ |

Leaders who are already signed in to Shift Lead are signed in to the Hub
too (it's the same login), and the Shift Lead **More** page links to it.

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
- **Remove** signs the person out on their next tap; so does changing their
  number or making them inactive on the roster.
- Sign-in tries are limited: 5 misses locks a first name for 10 minutes, and
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
  `**bold**`, `_italic_`, `[link text](https://…)` or a Hub page
  `[Opening](/hub/page/12)`. Upload photos to a page, then put
  `[photo: 1]` on its own line to place the first one (unplaced photos show
  at the bottom). Tap **Preview** before saving.
- Word import keeps headings, lists and tables; it leaves out hidden text,
  deleted tracked changes and comments (and says so). Old `.doc` files:
  open in Word → Save As `.docx` first.
- Files: PDF, JPG/PNG/GIF/WebP, and Word/Excel/PowerPoint without macros,
  up to 15 MB each. The type is checked from the file itself, not its name.

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

## QR poster

`assets/hub-qr/` has a branded QR code and an 8.5×11 poster (PNG + PDF)
pointing at the live `/hub` URL — print it for the crew room.
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

`hub.py` (routes), `hub_db.py` (data), `hub_text.py` (formatting + Word
import), templates in `templates/hub/`, tests in `tests/test_hub.py` and
`tests/test_hub_text.py` (`python -m pytest tests/`).
