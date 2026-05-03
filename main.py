"""Cloud Run Service entrypoint.

Receives Pub/Sub events from Eventarc as HTTP POST requests.
The event body is a Pub/Sub envelope:

    {
      "message": {
        "data": "<base64-encoded log entry>",
        "attributes": {...},
        "messageId": "...",
        "publishTime": "..."
      },
      "subscription": "projects/.../subscriptions/..."
    }

Returning 2xx acknowledges the message. Returning 4xx/5xx causes Eventarc to retry.

For local testing, set ERROR_MESSAGE to bypass the HTTP server.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import re

import requests
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request as flask_request
from google.genai import types

from agent import set_correlation_id
from runner import APP_NAME, build_runner
from utils import emit_event, resolve_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

_USER_ID = "eventarc-trigger"

# In-memory dedup: fingerprint -> last handled datetime
_dedup_cache: dict[str, datetime] = {}
_dedup_lock = threading.Lock()
_DEDUP_WINDOW = timedelta(minutes=5)

_SLACK_WEBHOOK_URL: str = ""


def _resolve_slack_webhook() -> None:
    global _SLACK_WEBHOOK_URL
    val = resolve_secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_SECRET")
    if val:
        _SLACK_WEBHOOK_URL = val
    else:
        log.warning("SLACK_WEBHOOK_URL and SLACK_WEBHOOK_SECRET are both unset — Slack notifications disabled")


def _build_slack_blocks(summary: str) -> dict:
    # Convert GitHub-flavoured markdown to Slack mrkdwn
    text = re.sub(r"^## (.+)$", r"*\1*", summary, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = text.strip()

    # Split into ≤2900-char chunks (Block Kit section limit is 3000)
    chunks, limit = [], 2900
    for i in range(0, len(text), limit):
        chunks.append(text[i : i + limit])
    chunks = chunks[:3]  # max 3 sections to stay under Slack's 50-block limit

    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": "DinoAgent Remediation", "emoji": True}},
        {"type": "divider"},
    ]
    for chunk in chunks:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})

    return {"text": "DinoAgent Remediation", "blocks": blocks}


def _notify_slack(summary: str) -> None:
    if not _SLACK_WEBHOOK_URL:
        return
    try:
        resp = requests.post(
            _SLACK_WEBHOOK_URL,
            json=_build_slack_blocks(summary),
            timeout=10,
        )
        if resp.status_code != 200 or resp.text != "ok":
            log.warning("Slack notification failed: HTTP %s — %s", resp.status_code, resp.text)
        else:
            log.info("Slack notification sent")
    except Exception as e:
        log.warning("Slack notification failed: %s", e)


def _fingerprint(error_message: str) -> str:
    """Stable key for a (service, error type) pair, ignoring timestamps and instance IDs."""
    try:
        entry = json.loads(error_message)
        service = entry.get("resource", {}).get("labels", {}).get("service_name", "")
        text = entry.get("textPayload") or json.dumps(entry.get("jsonPayload", ""))
        key = f"{service}:{text[:200]}"
    except (json.JSONDecodeError, AttributeError):
        key = error_message[:200]
    return hashlib.sha256(key.encode()).hexdigest()


def _is_duplicate(error_message: str) -> bool:
    fp = _fingerprint(error_message)
    now = datetime.now(timezone.utc)
    with _dedup_lock:
        last = _dedup_cache.get(fp)
        if last and (now - last) < _DEDUP_WINDOW:
            return True
        _dedup_cache[fp] = now
    return False


def _decode_envelope(envelope: dict) -> str:
    raw = envelope.get("message", {}).get("data", "")
    return base64.b64decode(raw).decode("utf-8")


async def _run(error_message: str, session_id: str) -> None:
    set_correlation_id(session_id)
    emit_event("DinoAgent", "detected_error", {"error_preview": error_message[:200]}, session_id)
    runner = build_runner()
    await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=_USER_ID,
        session_id=session_id,
    )
    log.info("Starting remediation. Error preview: %.200s", error_message)
    final_response = ""
    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=error_message)],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_response = event.content.parts[0].text
    if final_response:
        emit_event("DinoAgent", "thinking", {"summary": final_response[:300]}, session_id)
    log.info("Remediation complete:\n%s", final_response)
    _notify_slack(final_response)


@app.route("/", methods=["POST"])
def handle_event():
    envelope = flask_request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        log.error("Invalid envelope: %s", envelope)
        return "Bad Request: missing or invalid message", 400

    try:
        error_message = _decode_envelope(envelope)
    except Exception as e:
        log.error("Failed to decode message data: %s", e)
        return "Bad Request: could not decode message data", 400

    message_id = envelope["message"].get("messageId", "unknown")

    if _is_duplicate(error_message):
        log.info("Skipping duplicate event %s (same error seen within %s)", message_id, _DEDUP_WINDOW)
        return ("", 204)

    log.info("Received event %s", message_id)

    try:
        asyncio.run(_run(error_message, session_id=f"session-{message_id}"))
    except Exception:
        log.exception("Remediation failed for message %s", message_id)
        return "Internal Server Error", 500

    return ("", 204)


_resolve_slack_webhook()

if __name__ == "__main__":
    direct = os.environ.get("ERROR_MESSAGE")
    if direct:
        asyncio.run(_run(direct, session_id="local-test"))
    else:
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
