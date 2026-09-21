# CFA Sidekick

The support stack for **Chick-fil-A Wharncliffe & Wonderland** (London,
Ontario): team chat bots plus the leadership team's shift app, all served by
one Flask service deployed on Render.

## What's in here

| Piece | Where | What it does |
| --- | --- | --- |
| GroupMe bot | `app.py` | Team-member support bot — answers `cow:` questions (OpenAI-backed), auto-responds to coverage/sick messages, posts daily affirmations via `/scheduled` endpoints |
| Slack coverage bot | `slack_coverage.py` | Auto-responds in the #shift-coverage channel: validates 🔄 COVERAGE REQUEST posts and walks people through the release → post → approve flow |
| **Shift Lead app** | `shift_app.py` + `shift_db.py` | Phone-first leadership tool at `/shift`: daily checklists with who-did-what stamps, position lineups, goals, shift notes, announcements with read receipts, and per-leader leadership-course tracking (`shift_course.py`) |
| Leadership application | `templates/apply.html` + `assets/leadership-qr/` | CFA-branded fallback application form at `/apply` and the QR poster for the live Google Form |
| Office QR poster | `assets/shift-qr/` | Print-ready poster pointing leaders at the Shift Lead app |

Setup guides live in [`docs/`](docs/) — start with
[`docs/shift-leading-app.md`](docs/shift-leading-app.md) for the shift app's
environment variables, Render persistent-disk setup, and backup story.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
SHIFT_ADMIN_PIN=1234 python app.py     # http://localhost:5000 (/shift for the app)
python -m pytest tests/                # full test suite
```

`GET /` is the health check; it reports the deployed git revision on Render.

## Deployment

Deployed on Render from `main` (auto-deploy on commit); configuration is
entirely through service environment variables — see
[`CLAUDE.md`](CLAUDE.md) for the full list. The shift app's SQLite database
lives on a persistent disk (`SHIFT_DB_PATH=/var/data/shift_data.db`).
