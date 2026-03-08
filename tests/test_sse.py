"""Tests for SSE event buffering and reconnection support."""

import asyncio

import pytest

from agentpod.gateway.sse import (
    EventBuffer,
    _buffers,
    event_to_sse,
    get_buffer,
    get_or_create_buffer,
    remove_buffer,
    schedule_buffer_cleanup,
)
from agentpod.types import (
    Done,
    Error,
    MessageStart,
    TextDelta,
    ToolStart,
    TurnComplete,
)


# ── event_to_sse with id ─────────────────────────────────────────


class TestEventToSseWithId:
    def test_without_id_no_id_field(self):
        sse = event_to_sse(TextDelta(content="hello"))
        assert "id:" not in sse
        assert "event: text_delta" in sse

    def test_with_id_includes_id_field(self):
        sse = event_to_sse(TextDelta(content="hello"), event_id=5)
        assert "id: 5\n" in sse
        assert "event: text_delta" in sse

    def test_first_event_includes_retry(self):
        sse = event_to_sse(TextDelta(content="hi"), event_id=0)
        assert "retry: 3000\n" in sse
        assert "id: 0\n" in sse

    def test_non_first_event_no_retry(self):
        sse = event_to_sse(TextDelta(content="hi"), event_id=1)
        assert "retry:" not in sse

    def test_unknown_event_returns_empty(self):
        class FakeEvent:
            pass
        assert event_to_sse(FakeEvent()) == ""  # type: ignore[arg-type]


# ── EventBuffer ───────────────────────────────────────────────────


class TestEventBuffer:
    def test_add_and_replay(self):
        buf = EventBuffer()
        buf.add(MessageStart(session_id="s1", model="m1"))
        buf.add(TextDelta(content="hello"))
        buf.add(TextDelta(content=" world"))

        assert len(buf.replay(0)) == 3
        assert len(buf.replay(1)) == 2
        assert len(buf.replay(3)) == 0

    def test_replay_negative_offset(self):
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))
        assert len(buf.replay(-5)) == 1

    def test_is_done_on_done_event(self):
        buf = EventBuffer()
        assert not buf.is_done
        buf.add(Done(usage={}, cost=0.0))
        assert buf.is_done

    def test_is_done_on_error_event(self):
        buf = EventBuffer()
        buf.add(Error(message="fail"))
        assert buf.is_done

    def test_events_have_incremental_ids(self):
        buf = EventBuffer()
        s0 = buf.add(TextDelta(content="a"))
        s1 = buf.add(TextDelta(content="b"))
        assert "id: 0\n" in s0
        assert "id: 1\n" in s1

    def test_first_event_has_retry(self):
        buf = EventBuffer()
        s0 = buf.add(TextDelta(content="a"))
        s1 = buf.add(TextDelta(content="b"))
        assert "retry: 3000" in s0
        assert "retry:" not in s1

    @pytest.mark.asyncio
    async def test_subscribe_replays_then_awaits(self):
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))
        buf.add(TextDelta(content="b"))

        collected = []

        async def consumer():
            async for sse in buf.subscribe(0):
                collected.append(sse)

        # Start consumer, let it drain buffered events
        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        # Add more events
        buf.add(TextDelta(content="c"))
        await asyncio.sleep(0.05)

        # Finish the stream
        buf.add(Done(usage={}, cost=0.0))
        await asyncio.sleep(0.05)

        await task
        assert len(collected) == 4

    @pytest.mark.asyncio
    async def test_subscribe_from_middle(self):
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))
        buf.add(TextDelta(content="b"))
        buf.add(TextDelta(content="c"))
        buf.add(Done(usage={}, cost=0.0))

        collected = []
        async for sse in buf.subscribe(2):
            collected.append(sse)
        # Should get events 2 and 3 (c + done)
        assert len(collected) == 2

    @pytest.mark.asyncio
    async def test_subscribe_already_done(self):
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))
        buf.add(Done(usage={}, cost=0.0))

        collected = []
        async for sse in buf.subscribe(0):
            collected.append(sse)
        assert len(collected) == 2

    def test_mark_done_unblocks_subscriber(self):
        """mark_done() should set is_done and unblock waiting subscribers."""
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))
        assert not buf.is_done
        buf.mark_done()
        assert buf.is_done

    def test_mark_done_idempotent(self):
        buf = EventBuffer()
        buf.mark_done()
        buf.mark_done()  # should not raise
        assert buf.is_done

    @pytest.mark.asyncio
    async def test_mark_done_ends_subscribe(self):
        """subscribe() should exit when mark_done() is called without Done event."""
        buf = EventBuffer()
        buf.add(TextDelta(content="a"))

        collected = []

        async def consumer():
            async for sse in buf.subscribe(0):
                collected.append(sse)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        # Producer calls mark_done instead of adding Done event
        buf.mark_done()
        await asyncio.sleep(0.05)

        await task
        assert len(collected) == 1  # only the TextDelta

    @pytest.mark.asyncio
    async def test_decoupled_producer_survives_consumer_cancel(self):
        """Simulate client disconnect: cancel consumer, producer keeps running."""
        buf = EventBuffer()

        async def producer():
            for i in range(5):
                buf.add(TextDelta(content=f"chunk{i}"))
                await asyncio.sleep(0.02)
            buf.add(Done(usage={}, cost=0.0))

        async def consumer():
            async for sse in buf.subscribe(0):
                pass  # just drain

        prod_task = asyncio.create_task(producer())
        cons_task = asyncio.create_task(consumer())

        # Let consumer get a few events, then cancel it (simulating disconnect)
        await asyncio.sleep(0.05)
        cons_task.cancel()
        try:
            await cons_task
        except asyncio.CancelledError:
            pass

        # Producer should still finish
        await prod_task
        assert buf.is_done
        assert len(buf.replay(0)) == 6  # 5 TextDelta + 1 Done


# ── Buffer registry ───────────────────────────────────────────────


class TestBufferRegistry:
    def setup_method(self):
        _buffers.clear()

    def test_get_or_create(self):
        buf = get_or_create_buffer("u1", "s1")
        assert isinstance(buf, EventBuffer)
        # Same key returns same instance
        assert get_or_create_buffer("u1", "s1") is buf

    def test_get_nonexistent(self):
        assert get_buffer("u1", "s1") is None

    def test_get_existing(self):
        buf = get_or_create_buffer("u1", "s1")
        assert get_buffer("u1", "s1") is buf

    def test_remove(self):
        get_or_create_buffer("u1", "s1")
        remove_buffer("u1", "s1")
        assert get_buffer("u1", "s1") is None

    def test_remove_nonexistent_no_error(self):
        remove_buffer("u1", "s1")  # should not raise

    @pytest.mark.asyncio
    async def test_schedule_cleanup(self):
        from agentpod.gateway import sse
        original_ttl = sse._BUFFER_TTL
        sse._BUFFER_TTL = 0.1  # 100ms for test
        try:
            get_or_create_buffer("u1", "s1")
            assert get_buffer("u1", "s1") is not None
            await schedule_buffer_cleanup("u1", "s1")
            assert get_buffer("u1", "s1") is None
        finally:
            sse._BUFFER_TTL = original_ttl
