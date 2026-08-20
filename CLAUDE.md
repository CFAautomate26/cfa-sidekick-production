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

## Life Planning Workshop sign-up

- The live sign-up sheet is a Google Form owned by
  marketing.cfalondon@gmail.com ("Life Planning Workshop Sign Up |
  Chick-fil-A Wharncliffe & Wonderland", copied from the leadership
  application form); responses collect there. The committed QR code and
  poster in `assets/life-planning-qr/` point at it. Each group runs 7
  weeks with 7 spots (5 Team Members + 2 Leaders); a new group launches
  once the current one completes the workshop.
- `scripts/generate_life_planning_poster.py` regenerates the branded QR
  code and 8.5x11 print poster; it defaults to the live Google Form
  responder URL (pass a different URL as an argument if the form ever
  moves).
- `GET/POST /life-planning` — CFA-branded fallback sign-up form served by
  the Flask app. Submissions are emailed privately to the Operator via
  FormSubmit (`APPLICATION_EMAIL`) and logged in full to Render logs as a
  backup. Never post sign-ups to the team chat.
