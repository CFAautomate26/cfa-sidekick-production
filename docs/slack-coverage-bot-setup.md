# Slack coverage bot — setup

The Flask app now serves `POST /slack/events`, a Slack Events API endpoint
that watches the **#shift-coverage** channel and auto-replies in-thread:

| Someone posts…                                   | Bot replies with…                                              |
| ------------------------------------------------ | -------------------------------------------------------------- |
| The `🔄 COVERAGE REQUEST` template, fully filled | Confirmation + next steps (pickup + leader approval in HotSchedules) |
| The template with `Released in HotSchedules? N`  | Reminder to release the shift in HotSchedules first            |
| The template with blank fields                   | List of the missing fields                                     |
| A sick message ("I'm sick", "I have a fever"…)   | Sick guidance (stay home, doctor's note, coverage steps)       |
| Any other top-level post                         | The 3-step flow + the template to copy                         |

Only **top-level** messages trigger replies; thread replies are ignored, so
the approval conversation is never interrupted.

## One-time setup (~10 minutes)

1. **Create the Slack app.** Go to <https://api.slack.com/apps> → *Create New
   App* → *From scratch*. Name: `CFA Sidekick`, workspace: Wharncliffe &
   Wonderland.
2. **Add the bot scope.** *OAuth & Permissions* → *Bot Token Scopes* → add
   `chat:write`.
3. **Install to workspace.** Still under *OAuth & Permissions*, click
   *Install to Workspace*. Copy the **Bot User OAuth Token** (`xoxb-…`).
4. **Copy the signing secret.** *Basic Information* → *App Credentials* →
   **Signing Secret**.
5. **Set the Render environment variables** on the service and redeploy:
   - `SLACK_BOT_TOKEN` — the `xoxb-…` token
   - `SLACK_SIGNING_SECRET` — the signing secret
   - `SLACK_COVERAGE_CHANNEL_ID` — optional; defaults to `C0C0S22PV5X`
     (#shift-coverage)
6. **Enable event subscriptions.** *Event Subscriptions* → toggle on →
   Request URL: `https://<your-render-app>.onrender.com/slack/events`
   (Slack verifies the URL instantly — the app must already be deployed with
   the signing secret set). Under *Subscribe to bot events* add
   `message.channels`, then *Save Changes* and reinstall if prompted.
7. **Invite the bot to the channel.** In #shift-coverage post:
   `/invite @CFA Sidekick`.

## Verifying

Post `need coverage` in #shift-coverage — the bot should reply in-thread with
the flow within a second or two. Then post a filled-in template and confirm
the ✅ reply. Render logs show every event received and every reply sent.

## Notes

- Slack signs every request; the endpoint rejects anything without a valid
  signature, so the URL being public is fine.
- Slack redelivers events if the app responds slowly; redeliveries and
  duplicate event IDs are ignored, so the bot never double-replies.
- The channel it watches is controlled by `SLACK_COVERAGE_CHANNEL_ID` — point
  it at a different channel ID to move the flow.
