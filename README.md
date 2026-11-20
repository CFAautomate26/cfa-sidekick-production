# CFA Sidekick

The support stack for **Chick-fil-A Wharncliffe & Wonderland** (London,
Ontario): team chat bots plus the leadership team's shift app, all served by
one Flask service deployed on Render.

## What's in here

| Piece | Where | What it does |
| --- | --- | --- |
| GroupMe bot | `app.py` | Team-member support bot — answers `cow:` questions (OpenAI-backed), auto-responds to coverage/sick messages, posts daily affirmations via `/scheduled` endpoints |
| Slack coverage bot | `slack_coverage.py` | Auto-responds in the #shift-coverage channel: validates 🔄 COVERAGE REQUEST posts and walks people through the release → post → approve flow |
| **Shift Lead app** | `shift_app.py` + `shift_db.py` | Phone-first leadership tool at `/shift`: daily checklists with who-did-what stamps (each leader sees their side of the store), to-dos, shift notes, announcements with read receipts, and per-leader leadership-course tracking (`shift_course.py`) |
| People | `people*.py` + `careers.py` | Private leader reviews (`/shift/reviews`) and hiring (`/shift/hiring`, public job postings at `/careers`), kept in their own database with their own access, PIN lock and backup — guide: [`docs/people.md`](docs/people.md) |
| Leadership application | `templates/apply.html` + `assets/leadership-qr/` | CFA-branded fallback application form at `/apply` and the QR poster for the live Google Form |
| Office QR poster | `assets/shift-qr/` | Print-ready poster pointing leaders at the Shift Lead app |

Setup guides live in [`docs/`](docs/) — start with
[`docs/shift-leading-app.md`](docs/shift-leading-app.md) for the shift app's
environment variables, Render persistent-disk setup, and backup story.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
ENV=dev SHIFT_ADMIN_PIN=1234 python app.py   # http://localhost:5000 (/shift for the app)
python -m pytest tests/                      # full test suite
```

`ENV=dev` (or `test`, `local`) is what makes a plain `http://` address work.
Without it the app behaves as the live server: the sign-in cookie is
HTTPS-only, so signing in over plain http loops back to the sign-in page
(on a phone at your computer's LAN address, and in Safari even on
localhost), and People and the careers pages do not load unless
`FLASK_SECRET_KEY` is set to 32 characters or more.

`GET /` is the health check; it reports the deployed git revision on Render.

## Deployment

Deployed on Render from `main` (auto-deploy on commit); configuration is
entirely through service environment variables — see
[`CLAUDE.md`](CLAUDE.md) for the full list. The shift app's SQLite database
lives on a persistent disk (`SHIFT_DB_PATH=/var/data/shift_data.db`).
