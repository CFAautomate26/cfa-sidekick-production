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
- Deployed on Render; configuration via service environment variables
  (`OPENAI_API_KEY`, `GROUPME_BOT_ID`, `SCHEDULE_SECRET`, `ENV`,
  `APPLICATION_EMAIL`).

## Leadership application form

- `GET/POST /apply` — public, CFA-branded leadership application form
  (templates in `templates/`). Submissions are emailed privately to the
  Operator via FormSubmit (`APPLICATION_EMAIL`, default
  joshua.huesser@cfafranchisee.ca) and always logged in full to Render logs
  as a backup. Never post applications to the team GroupMe.
- `scripts/generate_leadership_qr.py` regenerates the branded QR code and
  print poster in `assets/leadership-qr/` (pass the form URL as an argument
  if the Render URL changes).
