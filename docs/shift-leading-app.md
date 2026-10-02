# Shift Leading App — setup & guide

A phone-first shift leadership tool (in the spirit of Huddle) built into the
CFA Sidekick Flask service, at **`/shift`** on the Render deployment. It gives
the leadership team:

- **Today dashboard** — the current daypart's checklists, lineup, goals,
  unread announcements, and today's notes in one glance.
- **Shift checklists** — FOH/BOH opening, breakfast→lunch transition,
  afternoon reset, and closing lists generated fresh every day. Tapping an
  item stamps *who* checked it and *when*. Items marked **critical**
  (food-safety / cash) are flagged red until done. Templates are editable in
  the admin area; past days keep their history exactly as it happened.
- **Lineup / setup sheet** — assign team members to positions per daypart,
  with one-tap **copy from yesterday** or **copy from the previous daypart**,
  and name autosuggest from the roster.
- **Goals** — targets with a metric, unit, direction (higher/lower is
  better), and rhythm; any leader logs progress numbers or notes; progress
  bars everywhere; mark achieved 🏆 or archive.
- **Shift notes** — the store logbook (wins / issues / equipment / staffing /
  guest / food-safety), a handoff for the next shift.
- **Announcements** — Operator/admin posts; every leader taps **Got it**, and
  admins see exactly who has acknowledged.
- **History** — any past day's checklists (who did what), lineups, and notes.
- **Leader to-dos** — any leader assigns tasks to any leader (title,
  details, due date; stamped with who assigned it); each leader's open
  to-dos appear on their Today screen the moment they sign in, they check
  them off from their to-do page (stamped who/when; only the assignee or
  an admin can complete or reopen a task, and only admins delete), and
  everyone sees open/overdue counts per leader with a full completed
  history. When a leader completes a task, the
  Operator gets a **Slack ping** in a private channel (posted by the same
  Slack bot as the coverage flow, with an @-mention so the phone buzzes) —
  see "To-do completion pings" below. An email copy via FormSubmit is
  opt-in with `SHIFT_NOTIFY_EMAIL` (FormSubmit's Cloudflare front
  bot-challenges server-side posts, so treat email as unreliable).
  Completions the Operator records themself don't notify.
- **Guest recovery** — when a guest has a bad experience, any leader logs
  the guest's name and phone/email, what went wrong (order error, food
  quality, wait, service…), and the make-it-right remedy being provided
  (remade on the spot, refund, free entrée card, dessert/drink, catering
  credit…), plus whether the guest expects a call-back. Open recoveries
  sit on everyone's Today screen until someone resolves them (stamped
  who/when, with a note on how it was closed); every leader sees the
  28-day what-keeps-going-wrong breakdown. A middle state covers the common case
  of reaching the guest before they've been made whole: mark it
  **"Contacted — coming back"** (or tick "already talked to the guest"
  when logging) and it moves to a *Waiting to come back* list — every
  leader sees who to expect and hands over the replacement, then taps
  "They came back ✓". Guest name + contact only — never
  payment/card info; injury or damage claims go to the Operator directly.
- **1:1 meetings** — a shared agenda between the Operator and each leader.
  Both add talking points between meetings ("ask about Saturday lineup");
  during the 1-on-1 each topic is checked off (who/when stamped) with an
  optional outcome note, unchecked topics carry forward automatically, and
  past meetings form a browsable thread. Action items drop straight onto
  the leader's to-do list, and the page gathers their personal goals
  (goals can be tagged to a leader; a tagged goal is private to that
  leader + the Operator everywhere — the Goals tab, its detail page, and
  the Today dashboard all hide it from everyone else) and course
  progress — a one-screen sit-down. **Privacy:
  each agenda is visible to that leader and the Operator master login
  only — not to other leaders, and not to admin-role leaders either.**
  Growth and operational topics only; conduct/discipline/wage/health
  conversations never go in this app.
  **Team members too:** every leader — not just the Operator — can hold
  1:1s with anyone on the team roster. More → 1:1 meetings has a dropdown
  of the team (pick a name and the 1:1 opens, no typing); a leader's page
  also links their own agenda with the Operator, and the Operator's
  dropdown lists leaders first, then the team. A team-member 1:1 works
  the same way — talking points between meetings, check-offs with outcome
  notes, carried topics, past-meeting history — minus goals, course, and
  to-dos (those are leader features). **Privacy: each team-member 1:1
  belongs to whoever holds it.** A leader's 1:1s with team members are
  visible to that leader and the Operator only — never to other leaders,
  admin-role included; the Operator's own are Operator-only; and the
  Operator's page has a "Leaders' 1:1s with the team" overview of every
  leader's. Team members have no login. Someone with a leader login
  appears once, under Leaders (1:1s with leaders go through the
  Operator's shared agenda). Each roster member also gets a 🤝 1:1
  shortcut on the Team roster page. Growth and coaching only — conduct,
  attendance, discipline, wage, medical, and accommodation matters stay
  in the `docs/legal-counsel/` process. Two people with the same name
  need distinct roster entries (e.g. "Sam K." and "Sam T.").
- **Shout-outs** — any leader recognizes a team member in ~20 seconds:
  name (roster autosuggest), an optional value tag (2nd-mile service,
  speed, food safety, teamwork, hospitality, cleanliness), and what
  happened. Fresh shout-outs show on everyone's Today screen for a week,
  and by default cross-post to the team Slack channel through the same
  bot as the coverage flow (uncheck the box to keep one in-app; delivery
  is best-effort). Admins get a 28-day recognition radar — most
  recognized and top recognizers.
- **Leadership development** — the Operator's course (from the "Leadership
  Development" folder on Drive: Mindset 101, Leading Others, Leading Teams,
  Leading Organization) tracked per leader. Admins see every leader's
  progress; each leader sees their own. Open a leader during a 1-on-1,
  tap through to the lesson's doc/slides/video on Drive, mark it complete,
  and keep a session note. The course structure is pinned in
  `shift_course.py` — update it there if the course changes on Drive.
  Note: the Drive links open for whoever the files are shared with — share
  the course folder with your leaders (view access) so the links work on
  their phones.

The app registers fault-isolated in `app.py`: if it ever fails to load, the
GroupMe and Slack bots keep running untouched.

## Environment variables (Render → Environment)

| Variable | Required | Purpose |
| --- | --- | --- |
| `SHIFT_ADMIN_PIN` | **Yes** | Master PIN for the **Operator** login. Until it's set, `/shift` shows a "not configured" page. |
| `SHIFT_DB_PATH` | **Strongly recommended** | Where the SQLite database lives. Point it at a persistent disk (see below) or data is wiped on every deploy. |
| `FLASK_SECRET_KEY` | Recommended | Signs session cookies. If unset, a stable key is derived from the `SCHEDULE_SECRET` + `SHIFT_ADMIN_PIN` env vars (sessions survive deploys); if none of the three is set, a random per-boot key is used and logins reset each deploy. Generate one: `python3 -c "import secrets; print(secrets.token_hex(32))"`. |
| `SHIFT_TZ` | No | Store timezone; defaults to `America/Toronto`. |
| `SHIFT_NOTIFY_SLACK_CHANNEL` | For completion pings | Slack channel ID the to-do completion pings post to. The CFA Sidekick Slack bot (`SLACK_BOT_TOKEN`) must be invited to it. Empty (default) disables the pings. Production: `C0C51833JAH` (#sidekick-alerts). |
| `SHIFT_NOTIFY_SLACK_MENTION` | No | Slack user ID to @-mention in each ping so it triggers a phone notification. Production: `U05R80802EB` (the Operator). |
| `SHIFT_SHOUTOUT_SLACK_CHANNEL` | For shout-out cross-posts | Slack channel ID where shout-outs post for the whole team (the CFA Sidekick bot must be invited to it). Empty (default) keeps shout-outs in-app. Production: `C0682FXBY3T` (#recognition-). |
| `SHIFT_ROSTER_SLACK_CHANNEL` | No | Slack channel ID whose members the *Pull team from Slack* button adds to the team roster. Empty (default) = the workspace's #general (`C05S15A1XMF` in production). Needs the bot scopes below. |
| `SHIFT_NOTIFY_EMAIL` | No | Opt-in email copy of completion notifications, via FormSubmit. Off by default — FormSubmit sits behind Cloudflare bot protection that challenges server-side posts, so email delivery is unreliable; Slack is the supported path. |

### To-do completion pings (Slack)

When a leader checks off an assigned to-do, the app posts to a private
Slack channel using the same bot token as the coverage bot (scope
`chat:write`). One-time setup:

1. Create (or pick) a private channel — production uses **#sidekick-alerts**.
2. In that channel: `/invite @CFA Sidekick`.
3. On Render set `SHIFT_NOTIFY_SLACK_CHANNEL` to the channel ID (channel →
   name at the top → About → Channel ID) and `SHIFT_NOTIFY_SLACK_MENTION`
   to the Operator's Slack member ID, then deploy.

Every ping shows who completed what, the due date, how many to-dos that
leader still has open, and a link to their to-do page. Failures are logged
(`To-do completion Slack status: …` in Render logs) and never shown to the
leader tapping the checkmark.

### Pulling the team roster from Slack #general

The Operator's 1:1 page and the Team roster page have a **↻ Pull team from
Slack #general** button (Operator master login only). It adds everyone in
#general to the team roster under their Slack real name, so they show up
in the 1:1 dropdown (and lineup autosuggest):

- Leaders with a login are skipped — they're already under Leaders.
  Matching is by full name, Slack display name, or first name.
- The workspace owner (the Operator), bots, deactivated accounts, and
  guests are skipped.
- An existing roster name is linked instead of duplicated (same full
  name, or a one-word name like "Calla" matching exactly one person's
  first name). Nobody is ever renamed.
- People you've **removed** from the roster stay removed; the pull never
  takes anyone off the roster either — remove leavers on Team roster.
- Only names (and the Slack user ID, to avoid duplicates) are stored —
  never emails or anything else from Slack.
- After the first pull, it refreshes itself once a day in the background
  when the Operator opens the 1:1 page, so new hires appear on their own.

The first boot after this feature shipped also seeded the roster once
from a pinned snapshot of #general (`shift_roster_seed.py`, names + Slack
user IDs only, same merge rules), so the dropdown was full before the
Slack setup below. It never runs again, and never after a live pull.

One-time Slack setup (the bot can only post until you do this):

1. api.slack.com/apps → **CFA Sidekick** → **OAuth & Permissions** →
   **Bot Token Scopes** → add `channels:read` and `users:read`.
2. Click **Reinstall to Workspace** (top of that page) and approve.
3. Slack normally keeps the same Bot User OAuth Token. If the page shows
   a different `xoxb-` token, paste it into `SLACK_BOT_TOKEN` on Render.
4. Tap the pull button. If a permission is still missing, the button says
   exactly which one.

## The persistent disk (do this before rollout)

Render wipes the service filesystem on every deploy. Without this step the
app still works, but **all checklist history, goals, and notes vanish each
deploy** — the admin page shows a red warning banner while that's the case.

1. Render dashboard → the CFA Sidekick service → **Disks** → **Add Disk**
   (1 GB is plenty; requires a paid instance type).
2. Mount path: `/var/data`.
3. Add env var `SHIFT_DB_PATH=/var/data/shift_data.db`.
4. Deploy. The banner disappears.

Note: a persistent disk pins the service to a single instance — fine at this
scale.

## First run

1. Set `SHIFT_ADMIN_PIN`, deploy, and open `https://<service>/shift` on your
   phone.
2. Sign in as **Operator** with that PIN.
3. **More → Admin**: add each shift lead/director as a leader with their own
   4+ digit PIN (role `lead`, or `admin` for directors who should manage
   templates/announcements/backups).
4. **Admin → Edit checklist templates**: the app ships with seeded FOH/BOH
   opening, transition, afternoon, and closing lists — walk them with a
   senior director and reword to match the store before launch. One item per
   line; a leading `!` marks it critical.
5. Have every leader open the site, sign in (sessions last 30 days), and
   **Add to Home Screen** so it launches like an app.

## Backups

- Backups and leader PIN resets are **Operator-master-login only** (not
  admin-role leaders): the backup JSON contains every leader's private
  1:1 content, and a PIN reset would allow logging in as another leader.
  That includes re-adding an existing leader's name under *Add a leader*
  (which replaces their PIN): admins can add brand-new leaders, but only
  the Operator can re-add an existing one.
- **Manual**: Admin → *Download backup (JSON)* — everything except PINs.
  Backups now include guest recovery rows (guest names + contact info), so
  treat exported files with the same care as the database itself.
- **Automated**: `GET /scheduled/shift-backup?token=<SCHEDULE_SECRET>`
  returns the same JSON. Point a free cron pinger (e.g. cron-job.org) at it
  daily and keep the response, following the same token convention as the
  other `/scheduled` endpoints. `SCHEDULE_SECRET` must be explicitly set on
  the service (the endpoint answers 401 without it — there is deliberately
  no default), and since the backup carries guest recovery contact info,
  only store it with services you'd trust with the database itself —
  that includes every leader's 1:1 agendas and notes, and the Operator's
  private team-member 1:1 notes.
- **PII aging**: phone/email on recoveries resolved more than 90 days ago
  are cleared automatically (next time the recovery page loads), so guest
  contact info doesn't accumulate forever in the DB or in new backups.
- **Restore**: Admin → *Restore from a backup file* (tick the confirmation
  box). Leader accounts come back with **locked PINs** (PIN hashes never
  leave the database) — reset each leader's PIN in the admin page after a
  restore. Every leader's signed-in session is also invalidated (a restore
  can renumber leader accounts, and a stale cookie must never attach to a
  different leader). Don't restore a new backup onto a rolled-back old
  build — the old code wouldn't restore the newer tables it deleted (for
  team-member 1:1s, a restore on a build from before they existed
  permanently deletes them: clearing the roster cascades). Restoring a
  backup taken before team-member 1:1s existed clears them too, so take
  a fresh backup first. Course progress and everything else comes back as it was.

## Security model (know its limits)

- Auth is name + personal PIN over signed 30-day session cookies; wrong PINs
  lock a name for 10 minutes after 5 attempts. Deactivating a leader cuts
  their access on their next request.
- This is honor-system-grade auth for operational content (checklists,
  lineups, notes). **Never store HR, discipline, wage, or medical
  information in this app** — that stays in the processes under
  `docs/legal-counsel/`.
- Shift notes are operational handoffs. Anything about a specific team
  member's conduct or health belongs in a conversation with the Operator,
  not the logbook.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
SHIFT_ADMIN_PIN=1234 python app.py         # http://localhost:5000/shift
python -m pytest tests/                    # storage + route tests
```

The store "business day" rolls over at 4 a.m., not midnight, so post-close
work after 12 a.m. lands on the shift it belongs to.
