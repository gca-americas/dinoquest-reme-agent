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

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request
from google.genai import types

from runner import APP_NAME, build_runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

_USER_ID = "eventarc-trigger"

# In-memory dedup: fingerprint -> last handled datetime
# Keyed by a hash of (service_name, error_text) so semantically identical
# errors from the same burst are only processed once per DEDUP_WINDOW.
_dedup_cache: dict[str, datetime] = {}
_dedup_lock = threading.Lock()
_DEDUP_WINDOW = timedelta(minutes=5)


def _fingerprint(error_message: str) -> str:
    """Stable key for a (service, error type) pair, ignoring timestamps and instance IDs."""
    try:
        entry = json.loads(error_message)
        service = entry.get("resource", {}).get("labels", {}).get("service_name", "")
        text = entry.get("textPayload") or json.dumps(entry.get("jsonPayload", ""))
        # Truncate to first 200 chars to focus on the error type, not variable details
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
    log.info("Remediation complete:\n%s", final_response)


@app.route("/", methods=["POST"])
def handle_event():
    envelope = request.get_json(silent=True)
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


if __name__ == "__main__":
    direct = os.environ.get("ERROR_MESSAGE")
    if direct:
        asyncio.run(_run(direct, session_id="local-test"))
    else:
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
