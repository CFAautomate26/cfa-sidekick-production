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
