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

- **Manual**: Admin → *Download backup (JSON)* — everything except PINs.
- **Automated**: `GET /scheduled/shift-backup?token=<SCHEDULE_SECRET>`
  returns the same JSON. Point a free cron pinger (e.g. cron-job.org) at it
  daily and keep the response, following the same token convention as the
  other `/scheduled` endpoints.
- **Restore**: Admin → *Restore from a backup file* (tick the confirmation
  box). Leader accounts come back with **locked PINs** (PIN hashes never
  leave the database) — reset each leader's PIN in the admin page after a
  restore. Course progress and everything else comes back as it was.

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
