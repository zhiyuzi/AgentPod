"""Tests for query cancellation (v1.14)."""

import asyncio

import pytest

from agentpod.gateway.sse import (
    EventBuffer,
    _buffers,
    _tasks,
    cancel_task,
    get_or_create_buffer,
    register_task,
    remove_task,
)
from agentpod.types import Done, TextDelta


class TestTaskRegistry:
    def setup_method(self):
        _tasks.clear()
        _buffers.clear()

    @pytest.mark.asyncio
    async def test_register_and_cancel(self):
        task = asyncio.create_task(asyncio.sleep(999))
        register_task("u1", "s1", task)
        assert cancel_task("u1", "s1") is True
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_cancel_nonexistent_returns_false(self):
        assert cancel_task("u1", "s1") is False

    @pytest.mark.asyncio
    async def test_cancel_already_done_returns_false(self):
        task = asyncio.create_task(asyncio.sleep(0))
        await task  # let it finish
        register_task("u1", "s1", task)
        assert cancel_task("u1", "s1") is False

    @pytest.mark.asyncio
    async def test_remove_task(self):
        task = asyncio.create_task(asyncio.sleep(999))
        register_task("u1", "s1", task)
        remove_task("u1", "s1")
        # After removal, cancel should return False
        assert cancel_task("u1", "s1") is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def test_remove_nonexistent_no_error(self):
        remove_task("u1", "s1")  # should not raise


class TestCancelProducerIntegration:
    """Simulate the _produce() cancel flow at the EventBuffer level."""

    def setup_method(self):
        _tasks.clear()
        _buffers.clear()

    @pytest.mark.asyncio
    async def test_cancel_pushes_done_cancelled(self):
        """When producer task is cancelled, it should push Done(stop_reason='cancelled')."""
        buf = EventBuffer()

        async def fake_produce():
            try:
                buf.add(TextDelta(content="hello"))
                await asyncio.sleep(999)  # simulate long LLM call
            except asyncio.CancelledError:
                buf.add(Done(usage={"turns": 0}, cost=0.0, stop_reason="cancelled"))
            finally:
                buf.mark_done()

        task = asyncio.create_task(fake_produce())
        register_task("u1", "s1", task)

        await asyncio.sleep(0.01)
        assert cancel_task("u1", "s1") is True

        # Wait for task to finish its CancelledError handler
        await asyncio.sleep(0.05)

        assert buf.is_done
        events = buf.replay(0)
        assert len(events) == 2  # TextDelta + Done
        assert '"cancelled"' in events[-1]

    @pytest.mark.asyncio
    async def test_subscriber_receives_cancelled_done(self):
        """A connected subscriber should receive the cancelled Done event."""
        buf = EventBuffer()

        async def fake_produce():
            try:
                buf.add(TextDelta(content="working..."))
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                buf.add(Done(usage={}, cost=0.0, stop_reason="cancelled"))
            finally:
                buf.mark_done()

        collected = []

        async def consumer():
            async for sse in buf.subscribe(0):
                collected.append(sse)

        prod_task = asyncio.create_task(fake_produce())
        cons_task = asyncio.create_task(consumer())
        register_task("u1", "s1", prod_task)

        await asyncio.sleep(0.01)
        cancel_task("u1", "s1")

        await asyncio.gather(prod_task, cons_task, return_exceptions=True)

        assert len(collected) == 2
        assert '"cancelled"' in collected[-1]
        assert "done" in collected[-1]

    @pytest.mark.asyncio
    async def test_cancel_during_tool_execution(self):
        """Cancel while a tool is 'executing' (simulated by sleep)."""
        buf = EventBuffer()
        tool_interrupted = False

        async def fake_tool():
            nonlocal tool_interrupted
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                tool_interrupted = True
                raise

        async def fake_produce():
            try:
                buf.add(TextDelta(content="calling tool..."))
                await fake_tool()
            except asyncio.CancelledError:
                buf.add(Done(usage={"turns": 1}, cost=0.001, stop_reason="cancelled"))
            finally:
                buf.mark_done()

        task = asyncio.create_task(fake_produce())
        register_task("u1", "s1", task)

        await asyncio.sleep(0.01)
        cancel_task("u1", "s1")
        await asyncio.sleep(0.05)

        assert tool_interrupted
        assert buf.is_done
        assert '"cancelled"' in buf.replay(0)[-1]

    @pytest.mark.asyncio
    async def test_cancel_after_completion_is_noop(self):
        """Cancelling after the stream is already done should return False."""
        buf = EventBuffer()

        async def fake_produce():
            buf.add(TextDelta(content="done"))
            buf.add(Done(usage={}, cost=0.0, stop_reason="end_turn"))
            buf.mark_done()

        task = asyncio.create_task(fake_produce())
        register_task("u1", "s1", task)
        await task

        assert cancel_task("u1", "s1") is False
