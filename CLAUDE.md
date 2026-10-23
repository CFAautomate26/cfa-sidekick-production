# CLAUDE.md

This repo runs CFA Sidekick, the GroupMe team-member support bot for
Chick-fil-A Wharncliffe & Wonderland (London, Ontario), and also houses the
business's standing advisory documentation.

## Legal counsel role

`docs/legal-counsel/` establishes a standing arrangement: in sessions touching
employment, HR, discipline, termination, accommodation, leave, wage/hour, or
investigation matters for this business, act as the Operator's E2R-style
employment-law advisor per the protocol in
[docs/legal-counsel/README.md](docs/legal-counsel/README.md). Follow the
escalation matrix strictly — it defines what is handled here versus what must
be flagged for licensed counsel before action. Jurisdiction is Ontario,
Canada.

## Bot

- `app.py` — Flask webhook for GroupMe; OpenAI-backed responses. Team members
  invoke the bot with the `cow:` keyword.
- **Team Hub** — the one app for the whole team: the leader tools (`/shift`)
  and Systems & Procedures (`/hub`) under one name, one sign-in
  (`templates/shared/front_door.html`: team members first name + clock-in
  number, leaders their PIN; optional "shared phone or computer" session)
  and one look styled from chick-fil-a.ca's tokens
  (`templates/shared/cfa_style.html`, `_chrome.html`, `menu.html`; the
  round logo in `assets/cfa-round-logo.png` is used as supplied, icons in
  `static/teamhub/`). `teamhub_nav.py` builds each role's tabs and ☰ menu
  (links only where that role may go — routes stay the gate;
  `tests/test_roles.py` checks every page × role). The app's front page is
  `/hub/` (one manifest, scope `/`); a leader's home is Today (`/shift/`).
  Guide: [docs/team-hub.md](docs/team-hub.md).
- `shift_app.py` + `shift_db.py` — the leader tools at `/shift` (formerly "Shift Lead")
  (Huddle-style: daily/weekly/monthly checklists with who-did-what stamps,
  Owner + Manager check, lock-at-period-end, and a consistency scorecard
  at `/shift/checklists/scorecard` — station cleaning sheets pinned in
  `shift_cleaning.py`; position
  lineups, goals, shift notes, announcements with read receipts,
  a calm Today home (`/shift/`: greeting, the leader's two daily goals —
  leadership + operational for their area of ownership, table
  `daily_goals`, one per kind per holder per business day, NULL holder =
  the Operator, private to that leader + the Operator like 1:1s; the
  leader's area is `leaders.area` — then their open to-dos and section
  tiles; triage lives on each section's own page),
  per-leader assigned to-dos under `/shift/todo` (any leader assigns),
  1:1 meeting agendas under `/shift/oneonone` (private to each leader +
  the Operator master login — not other admins; plus 1:1 threads with any
  team-roster member under `/shift/oneonone/member/<id>`, table
  `oneonone_member_topics`, one thread per (member, holder): each leader's
  are private to that leader + the Operator, the Operator's own are
  Operator-only, never visible to other leaders or admin-role leaders;
  roster seeded once from `shift_roster_seed.py`), team-member shout-outs
  under `/shift/shoutouts` (optional cross-post to the team Slack channel
  via `SLACK_BOT_TOKEN` + `SHIFT_SHOUTOUT_SLACK_CHANNEL`), a guest
  recovery log
  under `/shift/recovery` (guest + issue + make-it-right remedy; states
  open → contacted/coming-back → resolved, surfaced on the Today screen
  until closed; guest name/contact only — never payment info), and
  per-leader
  leadership-course tracking under `/shift/development` — course
  structure pinned from Drive in `shift_course.py`). Blueprint
  registers fault-isolated so it can never take down the bots. Storage is
  stdlib SQLite at `SHIFT_DB_PATH` (a Render persistent disk in
  production). Templates in `templates/shift/`; tests in `tests/` (run
  `python -m pytest tests/`). Setup guide:
  [docs/shift-leading-app.md](docs/shift-leading-app.md). Env vars:
  `SHIFT_ADMIN_PIN` (Operator master login), `SHIFT_DB_PATH`,
  `FLASK_SECRET_KEY` (optional; falls back to a key derived from the
  `SCHEDULE_SECRET`/`SHIFT_ADMIN_PIN` env vars, or a random per-boot key if
  neither is set), `SHIFT_TZ` (default America/Toronto),
  `SHIFT_NOTIFY_SLACK_CHANNEL` + `SHIFT_NOTIFY_SLACK_MENTION` (to-do
  completion pings to a private Slack channel via `SLACK_BOT_TOKEN`;
  empty channel disables), `SHIFT_NOTIFY_EMAIL` (opt-in email copy via
  FormSubmit — unreliable, Cloudflare bot-challenges server-side posts;
  off by default), `SHIFT_ROSTER_SLACK_CHANNEL` (Operator-only "Pull team
  from Slack" roster sync; empty = #general; bot needs `channels:read` +
  `users:read`; stores names + Slack user IDs only). Never store
  HR/discipline/wage/medical content in it. The office QR poster in
  `assets/shift-qr/` points at the live `/shift` URL;
  `scripts/generate_shift_qr.py` regenerates it (pass a different URL as
  an argument if the service ever moves).
- `hub.py` + `hub_db.py` + `hub_text.py` — Systems & Procedures at `/hub`: the
  store's "here's how we do it" library (categories HR / FOH Operations /
  BOH Operations / Leadership Development, with sections inside; drafts →
  versioned publishing; required reading with append-only "I've received
  and read this version" confirmations; search; photos/files; Word import;
  Operator-only library-bundle import (a zip of `.md` pages with a
  `---` header — how the confidential Chick-fil-A Operational Requirements
  Guide is loaded, since this repo is public and must never hold its text);
  yearly-review and lawyer-review flags; activity log). Page text is a
  Markdown subset incl. `| a | b |` tables and `~~struck~~` text. Team members sign
  in with first name + clock-in number (`hub_access`, HMAC under
  `meta.hub_pepper`, Operator-granted, never exported or logged); leaders
  reuse the Shift Lead session (`shift_app.leader_session_valid`). Editors
  = Operator + admin-role leaders; the HR "policy" category is published
  and changed only by the Operator after the review checks. Same SQLite DB
  and backups (hub tables in `EXPORT_TABLES`, minus `hub_access`; files in
  `hub_files/` beside the DB or `SHIFT_HUB_FILES_DIR`, not in backups).
  Registered fault-isolated after `/shift`. No scripts; strict CSP. Pages
  hold processes and policies only — never records about a specific
  person. Templates in `templates/hub/`; guide:
  [docs/team-hub.md](docs/team-hub.md); crew-room QR poster in
  `assets/hub-qr/` (`scripts/generate_hub_qr.py`).
- `slack_coverage.py` — Slack Events API blueprint (`POST /slack/events`)
  that auto-responds in-thread in the #shift-coverage Slack channel:
  validates `🔄 COVERAGE REQUEST` template posts, nags un-released shifts
  and missing fields, and answers freeform coverage/sick messages with the
  3-step flow. Setup guide: [docs/slack-coverage-bot-setup.md](docs/slack-coverage-bot-setup.md).
- `patterns.py` — coverage/sick message patterns shared by both bots.
- Deployed on Render; configuration via service environment variables
  (`OPENAI_API_KEY`, `GROUPME_BOT_ID`, `SCHEDULE_SECRET`, `ENV`,
  `APPLICATION_EMAIL`, and for Slack: `SLACK_BOT_TOKEN`,
  `SLACK_SIGNING_SECRET`, `SLACK_COVERAGE_CHANNEL_ID`).

## Leadership application form

- The live application form is a Google Form owned by
  marketing.cfalondon@gmail.com ("Leadership Application — Chick-fil-A
  Wharncliffe & Wonderland"); responses collect there. The committed QR
  code and poster in `assets/leadership-qr/` point at it.
- `scripts/generate_leadership_qr.py` regenerates the branded QR code and
  print poster; it defaults to the live Google Form responder URL (pass a
  different URL as an argument if the form ever moves).
- `GET/POST /apply` — CFA-branded fallback form served by the Flask app.
  Submissions are emailed privately to the Operator via FormSubmit
  (`APPLICATION_EMAIL`, default joshua.huesser@cfafranchisee.ca) and logged
  in full to Render logs as a backup. Never post applications to the team
  chat.
