"""Webhook event delivery with retry and dead-letter support."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time

import httpx

_log = logging.getLogger("agentpod.webhook")

# Retry delays in seconds: immediate, 5s, 30s, 5min
_RETRY_DELAYS = [0, 5, 30, 300]


async def emit_event(
    event_type: str,
    payload: dict,
    db,
    *,
    webhook_url: str,
    webhook_secret: str = "",
) -> None:
    """Send a webhook event. No-op if webhook_url is empty."""
    if not webhook_url:
        return

    event_id = "evt_" + secrets.token_hex(16)
    payload["event_id"] = event_id
    payload["event"] = event_type
    payload["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    body = json.dumps(payload, ensure_ascii=False)
    last_error: str | None = None

    for delay in _RETRY_DELAYS:
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            headers = _build_headers(event_id, body, webhook_secret)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                return
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_error = str(e)[:500]

    # All retries failed — write to dead letter table
    _log.warning("Webhook delivery failed after %d attempts: %s", len(_RETRY_DELAYS), last_error)
    try:
        db.insert_dead_letter(event_id, event_type, body, len(_RETRY_DELAYS), last_error)
    except Exception:
        _log.exception("Failed to write dead letter for event %s", event_id)


def _build_headers(event_id: str, body: str, secret: str) -> dict:
    timestamp = str(int(time.time()))
    signature = ""
    if secret:
        sig_payload = f"{timestamp}.{body}"
        signature = hmac.new(
            secret.encode(), sig_payload.encode(), hashlib.sha256
        ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-AgentPod-Event-Id": event_id,
        "X-AgentPod-Timestamp": timestamp,
        "X-AgentPod-Signature": f"sha256={signature}" if signature else "",
    }
