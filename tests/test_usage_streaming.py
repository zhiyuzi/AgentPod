"""Tests for v1.5: Usage streaming via TurnComplete + disconnect cleanup."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentpod.types import Done, TurnComplete
from agentpod.gateway.sse import event_to_sse


# ---------------------------------------------------------------------------
# TurnComplete carries usage
# ---------------------------------------------------------------------------


class TestTurnCompleteUsage:
    def test_turn_complete_has_usage_fields(self):
        tc = TurnComplete(
            turn=1,
            usage={"input_tokens": 100, "output_tokens": 50, "cached_tokens": 10},
            cost=0.003,
        )
        assert tc.turn == 1
        assert tc.usage["input_tokens"] == 100
        assert tc.cost == 0.003

    def test_turn_complete_defaults(self):
        tc = TurnComplete(turn=1)
        assert tc.usage == {}
        assert tc.cost == 0.0

    def test_turn_complete_sse_includes_usage(self):
        tc = TurnComplete(
            turn=2,
            usage={"input_tokens": 500, "output_tokens": 200, "cached_tokens": 0},
            cost=0.005,
        )
        sse = event_to_sse(tc)
        assert sse.startswith("event: turn_complete\n")
        data = json.loads(sse.split("data: ", 1)[1].strip())
        assert data["turn"] == 2
        assert data["usage"]["input_tokens"] == 500
        assert data["cost"] == 0.005


# ---------------------------------------------------------------------------
# Done carries stop_reason
# ---------------------------------------------------------------------------


class TestDoneStopReason:
    def test_done_default_stop_reason(self):
        d = Done(usage={"input_tokens": 100}, cost=0.01)
        assert d.stop_reason == "end_turn"

    def test_done_custom_stop_reason(self):
        d = Done(
            usage={"input_tokens": 100},
            cost=0.01,
            stop_reason="max_turns",
        )
        assert d.stop_reason == "max_turns"

    def test_done_sse_includes_stop_reason(self):
        d = Done(
            usage={"input_tokens": 100, "output_tokens": 50},
            cost=0.01,
            stop_reason="budget",
        )
        sse = event_to_sse(d)
        data = json.loads(sse.split("data: ", 1)[1].strip())
        assert data["stop_reason"] == "budget"
        assert data["usage"]["input_tokens"] == 100

    def test_done_stop_reason_values(self):
        """All expected stop_reason values should be accepted."""
        for reason in ("end_turn", "max_turns", "budget"):
            d = Done(usage={}, cost=0.0, stop_reason=reason)
            assert d.stop_reason == reason


# ---------------------------------------------------------------------------
# Sandbox cancellation (process termination on CancelledError)
# ---------------------------------------------------------------------------


class TestSandboxCancellation:
    async def test_cancelled_error_terminates_process(self):
        """When CancelledError interrupts run_sandboxed, the subprocess
        should be terminated so it doesn't become an orphan."""
        from agentpod.sandbox.isolate import run_sandboxed
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)

            async def run_and_cancel():
                task = asyncio.create_task(
                    run_sandboxed("sleep 60", cwd, timeout=120)
                )
                # Give the subprocess a moment to start
                await asyncio.sleep(0.3)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_and_cancel()
            # If we get here without hanging, the process was terminated.
            # (If terminate didn't work, sleep 60 would block for 60s.)
