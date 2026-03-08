"""Tests for gateway/webhook.py – event delivery, retry, and dead letters."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agentpod.gateway.webhook import emit_event, _build_headers


class TestBuildHeaders:
    def test_headers_without_secret(self):
        headers = _build_headers("evt_abc", '{"test": 1}', "")
        assert headers["Content-Type"] == "application/json"
        assert headers["X-AgentPod-Event-Id"] == "evt_abc"
        assert headers["X-AgentPod-Timestamp"] != ""
        assert headers["X-AgentPod-Signature"] == ""

    def test_headers_with_secret(self):
        headers = _build_headers("evt_abc", '{"test": 1}', "mysecret")
        assert headers["X-AgentPod-Signature"].startswith("sha256=")
        assert len(headers["X-AgentPod-Signature"]) > len("sha256=")

    def test_signature_is_deterministic(self):
        with patch("agentpod.gateway.webhook.time") as mock_time:
            mock_time.time.return_value = 1000000
            h1 = _build_headers("evt_1", "body", "secret")
            h2 = _build_headers("evt_1", "body", "secret")
        assert h1["X-AgentPod-Signature"] == h2["X-AgentPod-Signature"]

    def test_different_body_different_signature(self):
        with patch("agentpod.gateway.webhook.time") as mock_time:
            mock_time.time.return_value = 1000000
            h1 = _build_headers("evt_1", "body_a", "secret")
            h2 = _build_headers("evt_1", "body_b", "secret")
        assert h1["X-AgentPod-Signature"] != h2["X-AgentPod-Signature"]


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_noop_when_no_url(self):
        """emit_event should silently return when webhook_url is empty."""
        db = MagicMock()
        await emit_event("query_done", {"user_id": "u1"}, db, webhook_url="")
        db.insert_dead_letter.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        """First attempt succeeds — no retries, no dead letter."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        db = MagicMock()

        with patch("agentpod.gateway.webhook.httpx.AsyncClient", return_value=mock_client):
            await emit_event(
                "query_done",
                {"user_id": "u1", "cost_amount": 0.01},
                db,
                webhook_url="https://example.com/hook",
            )

        mock_client.post.assert_called_once()
        db.insert_dead_letter.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_retries_fail_writes_dead_letter(self):
        """All 4 attempts fail — should write to dead letter table."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        db = MagicMock()

        with (
            patch("agentpod.gateway.webhook.httpx.AsyncClient", return_value=mock_client),
            patch("agentpod.gateway.webhook.asyncio.sleep", new_callable=AsyncMock),
        ):
            await emit_event(
                "query_done",
                {"user_id": "u1"},
                db,
                webhook_url="https://example.com/hook",
            )

        assert mock_client.post.call_count == 4
        db.insert_dead_letter.assert_called_once()
        args = db.insert_dead_letter.call_args[0]
        assert args[0].startswith("evt_")  # event_id
        assert args[1] == "query_done"     # event_type
        assert args[3] == 4               # attempts

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        """First attempt fails, second succeeds — no dead letter."""
        fail_resp = AsyncMock()
        fail_resp.status_code = 503
        fail_resp.text = "Service Unavailable"

        ok_resp = AsyncMock()
        ok_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post.side_effect = [fail_resp, ok_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        db = MagicMock()

        with (
            patch("agentpod.gateway.webhook.httpx.AsyncClient", return_value=mock_client),
            patch("agentpod.gateway.webhook.asyncio.sleep", new_callable=AsyncMock),
        ):
            await emit_event(
                "query_done",
                {"user_id": "u1"},
                db,
                webhook_url="https://example.com/hook",
            )

        assert mock_client.post.call_count == 2
        db.insert_dead_letter.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_triggers_retry(self):
        """Network exception should trigger retry, not crash."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = ConnectionError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        db = MagicMock()

        with (
            patch("agentpod.gateway.webhook.httpx.AsyncClient", return_value=mock_client),
            patch("agentpod.gateway.webhook.asyncio.sleep", new_callable=AsyncMock),
        ):
            await emit_event(
                "query_done",
                {"user_id": "u1"},
                db,
                webhook_url="https://example.com/hook",
            )

        assert mock_client.post.call_count == 4
        db.insert_dead_letter.assert_called_once()

    @pytest.mark.asyncio
    async def test_payload_contains_event_fields(self):
        """Emitted payload should contain event_id, event, and timestamp."""
        captured_body = {}

        mock_resp = AsyncMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def capture_post(url, content, headers):
            captured_body.update(json.loads(content))
            return mock_resp

        mock_client.post = capture_post

        db = MagicMock()

        with patch("agentpod.gateway.webhook.httpx.AsyncClient", return_value=mock_client):
            await emit_event(
                "budget_exhausted",
                {"user_id": "u1", "budget_remaining": 0},
                db,
                webhook_url="https://example.com/hook",
            )

        assert captured_body["event"] == "budget_exhausted"
        assert captured_body["event_id"].startswith("evt_")
        assert "timestamp" in captured_body
        assert captured_body["user_id"] == "u1"
