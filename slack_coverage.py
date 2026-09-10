"""Slack Events API endpoint for the #shift-coverage channel.

Auto-responds in-thread to coverage requests and sick messages so the
coverage flow (release in HotSchedules -> post the template -> leader
approval in-thread) enforces itself. Announcements and ordinary chatter
are left alone; the bot only replies to messages it recognizes.

Setup (see docs/slack-coverage-bot-setup.md):
  SLACK_BOT_TOKEN          xoxb- bot token, needs chat:write
  SLACK_SIGNING_SECRET     from the Slack app's Basic Information page
  SLACK_COVERAGE_CHANNEL_ID  channel to watch (default: #shift-coverage)
"""

import hashlib
import hmac
import os
import time
from collections import OrderedDict

import requests
from flask import Blueprint, jsonify, request

from patterns import COVERAGE_PATTERNS, SICK_PATTERNS, matches_patterns

slack_bp = Blueprint("slack", __name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_COVERAGE_CHANNEL_ID = os.getenv("SLACK_COVERAGE_CHANNEL_ID", "C0C0S22PV5X")

# Slack retries deliveries, so remember recent event_ids to reply only once.
_MAX_SEEN_EVENTS = 500
_seen_event_ids: OrderedDict[str, None] = OrderedDict()

TEMPLATE_MARKER = "coverage request"

# Template line labels -> what we call them when nagging about blanks.
TEMPLATE_FIELDS = {
    "name": "Name",
    "date of shift": "Date of shift",
    "shift time": "Shift time (exact start–end)",
    "position": "Position/area",
    "released in hotschedules": "Released in HotSchedules? (Y/N)",
}

TEMPLATE_BLOCK = (
    "```\n"
    "\U0001f504 COVERAGE REQUEST\n"
    "Name:\n"
    "Date of shift:\n"
    "Shift time (exact start–end):\n"
    "Position/area:\n"
    "Released in HotSchedules? (Y/N)\n"
    "Reason (optional):\n"
    "```"
)

REPLY_REQUEST_OK = (
    "✅ *Coverage request received.* Next steps:\n"
    "• Anyone who can take this shift, reply *in this thread*. Coverage must "
    "match the exact start time and required skill set.\n"
    "• A leader will approve or decline here. *No approval = no coverage* — "
    "the shift stays your responsibility until a leader confirms in this thread.\n"
    "• Once approved, the person covering accepts the shift in HotSchedules."
)

REPLY_NOT_RELEASED = (
    "⚠️ *Your shift isn't released in HotSchedules yet.* Please release it now "
    "(HotSchedules → My Schedule → select the shift → Release) so whoever picks "
    "it up can actually claim it, then reply here once it's done."
)

REPLY_MISSING_FIELDS = (
    "✏️ *Almost there — your request is missing:*\n{missing}\n"
    "Please edit your message or reply in this thread with the missing details "
    "so a leader can approve it."
)

REPLY_FREEFORM_COVERAGE = (
    "\U0001f504 *Looks like you need shift coverage.* Here's the flow:\n"
    "1️⃣ Release the shift in HotSchedules (My Schedule → select the shift → Release)\n"
    "2️⃣ Post a new message in this channel using this template:\n"
    f"{TEMPLATE_BLOCK}\n"
    "3️⃣ A leader approves in the thread. The shift is yours until that happens.\n"
    "_See the pinned Shift Coverage Playbook for details._"
)

REPLY_SICK = (
    "\U0001f912 I'm sorry you're not feeling well — please don't come in sick, and tell "
    "a leader directly. A doctor's note is required for sick callouts.\n"
    "You're still responsible for the coverage steps unless a leader says otherwise:\n"
    "1️⃣ Release the shift in HotSchedules\n"
    "2️⃣ Post a new message here using the coverage template (see the pinned "
    "Shift Coverage Playbook)\n"
    "3️⃣ Wait for leader approval in the thread. Feel better soon! \U0001f49b"
)


def slack_signature_valid(req) -> bool:
    if not SLACK_SIGNING_SECRET:
        print("ERROR: SLACK_SIGNING_SECRET is missing; rejecting Slack event.")
        return False
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    try:
        if abs(time.time() - float(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    base = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, req.headers.get("X-Slack-Signature", ""))


def send_slack_reply(channel: str, thread_ts: str, text: str) -> None:
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN is missing, cannot reply in Slack.")
        return
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": channel, "thread_ts": thread_ts, "text": text},
            timeout=15,
        )
        print(f"Slack chat.postMessage status: {resp.status_code}, body: {resp.text[:300]}")
    except Exception as e:
        print(f"Error sending Slack reply: {e}")


def find_missing_template_fields(text: str) -> list[str]:
    """Return labels for template lines that are absent or left blank."""
    filled: set[str] = set()
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label_key = label.strip("*_ •-").lower()
        for key in TEMPLATE_FIELDS:
            if key in label_key and value.strip():
                filled.add(key)
    return [label for key, label in TEMPLATE_FIELDS.items() if key not in filled]


def shift_not_released(text: str) -> bool:
    for line in text.splitlines():
        label, _, value = line.partition(":")
        if "released in hotschedules" in label.lower():
            return value.strip().lower().lstrip("(").startswith("n")
    return False


def coverage_reply_for(text: str) -> str | None:
    """Pick the auto-reply for a top-level message, or None to stay silent."""
    if TEMPLATE_MARKER in text.lower():
        missing = find_missing_template_fields(text)
        if missing:
            bullets = "\n".join(f"• {label}" for label in missing)
            return REPLY_MISSING_FIELDS.format(missing=bullets)
        if shift_not_released(text):
            return REPLY_NOT_RELEASED
        return REPLY_REQUEST_OK
    if matches_patterns(text, SICK_PATTERNS):
        return REPLY_SICK
    if matches_patterns(text, COVERAGE_PATTERNS):
        return REPLY_FREEFORM_COVERAGE
    return None


@slack_bp.route("/slack/events", methods=["POST"])
def slack_events():
    if not slack_signature_valid(request):
        return "invalid signature", 401

    data = request.json or {}

    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")}), 200

    # Slack redelivers on slow responses; the first delivery already handled it.
    if request.headers.get("X-Slack-Retry-Num"):
        return "ok", 200, {"X-Slack-No-Retry": "1"}

    event_id = data.get("event_id", "")
    if event_id:
        if event_id in _seen_event_ids:
            return "ok", 200
        _seen_event_ids[event_id] = None
        while len(_seen_event_ids) > _MAX_SEEN_EVENTS:
            _seen_event_ids.popitem(last=False)

    event = data.get("event") or {}
    if (
        event.get("type") != "message"
        or event.get("subtype")  # edits, joins, bot_message, etc.
        or event.get("bot_id")
        or event.get("thread_ts")  # only top-level posts start the flow
        or event.get("channel") != SLACK_COVERAGE_CHANNEL_ID
    ):
        return "ok", 200

    text = event.get("text", "") or ""
    reply = coverage_reply_for(text)
    if reply:
        send_slack_reply(event["channel"], event.get("ts", ""), reply)

    return "ok", 200
