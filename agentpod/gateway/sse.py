"""Server-Sent Events helpers for streaming RuntimeEvents."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from agentpod.types import (
    ContextSnapshotEvent,
    Done,
    Error,
    MessageStart,
    ReasoningDelta,
    RuntimeEvent,
    TextDelta,
    TodoUpdate,
    ToolCallStart,
    ToolEnd,
    ToolStart,
    TurnComplete,
    UserInputRequired,
)


def _event_body(event: RuntimeEvent) -> tuple[str, str] | None:
    """Return (event_type, json_data) for a RuntimeEvent, or None."""
    if isinstance(event, MessageStart):
        return "message_start", json.dumps({"session_id": event.session_id, "model": event.model}, ensure_ascii=False)
    elif isinstance(event, ReasoningDelta):
        return "reasoning_delta", json.dumps({"content": event.content}, ensure_ascii=False)
    elif isinstance(event, TextDelta):
        return "text_delta", json.dumps({"content": event.content}, ensure_ascii=False)
    elif isinstance(event, ToolCallStart):
        return "tool_call_start", json.dumps({"tool": event.tool}, ensure_ascii=False)
    elif isinstance(event, ToolStart):
        return "tool_start", json.dumps({"tool": event.tool, "input": event.input}, ensure_ascii=False)
    elif isinstance(event, ToolEnd):
        return "tool_end", json.dumps({"tool": event.tool, "result": event.result, "is_error": event.is_error}, ensure_ascii=False)
    elif isinstance(event, TurnComplete):
        return "turn_complete", json.dumps({"turn": event.turn, "usage": event.usage, "cost": event.cost}, ensure_ascii=False)
    elif isinstance(event, UserInputRequired):
        return "user_input_required", json.dumps({"tool_use_id": event.tool_use_id, "question": event.question, "options": event.options}, ensure_ascii=False)
    elif isinstance(event, TodoUpdate):
        return "todo_update", json.dumps({"todos": event.todos}, ensure_ascii=False)
    elif isinstance(event, ContextSnapshotEvent):
        s = event.snapshot
        return "context_snapshot", json.dumps({"available_tokens": s.available_tokens, "used_tokens": s.used_tokens, "usage_ratio": s.usage_ratio}, ensure_ascii=False)
    elif isinstance(event, Done):
        return "done", json.dumps({"usage": event.usage, "cost": event.cost, "stop_reason": event.stop_reason}, ensure_ascii=False)
    elif isinstance(event, Error):
        return "error", json.dumps({"message": event.message, "retryable": event.retryable}, ensure_ascii=False)
    return None


def event_to_sse(event: RuntimeEvent, event_id: int | None = None) -> str:
    """Convert a RuntimeEvent to SSE format string.

    When *event_id* is provided the output includes an ``id:`` field (and
    ``retry: 3000`` on the very first event) so that clients using the
    standard ``EventSource`` API can reconnect automatically.
    """
    body = _event_body(event)
    if body is None:
        return ""
    event_type, data = body
    parts: list[str] = []
    if event_id is not None:
        if event_id == 0:
            parts.append("retry: 3000")
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_type}")
    parts.append(f"data: {data}")
    return "\n".join(parts) + "\n\n"


# ── Event Buffer (per-request, supports reconnection) ────────────


class EventBuffer:
    """Buffer SSE events for a single streaming request.

    Supports producer/consumer: the original request *adds* events while
    a reconnecting client can *subscribe* from a given offset.
    """

    def __init__(self) -> None:
        self._events: list[str] = []  # formatted SSE strings with id:
        self._done = False
        self._notify = asyncio.Event()

    # ── Producer API ──

    def add(self, event: RuntimeEvent) -> str:
        """Format *event* with an incremental id, buffer it, return SSE str."""
        eid = len(self._events)
        sse = event_to_sse(event, event_id=eid)
        if not sse:
            return ""
        self._events.append(sse)
        if isinstance(event, (Done, Error)):
            self._done = True
        self._notify.set()
        self._notify = asyncio.Event()  # reset for next waiter
        return sse

    def mark_done(self) -> None:
        """Signal stream completion without adding an event (e.g. on error)."""
        if not self._done:
            self._done = True
            self._notify.set()

    # ── Consumer API ──

    @property
    def is_done(self) -> bool:
        return self._done

    def replay(self, from_id: int) -> list[str]:
        """Return buffered SSE strings starting at *from_id*."""
        if from_id < 0:
            from_id = 0
        return list(self._events[from_id:])

    async def subscribe(self, from_id: int) -> AsyncIterator[str]:
        """Yield buffered events from *from_id*, then await new ones."""
        idx = max(from_id, 0)
        while True:
            # Yield any events we haven't sent yet
            while idx < len(self._events):
                yield self._events[idx]
                idx += 1
            if self._done:
                return
            # Wait for producer to add more
            waiter = self._notify
            await waiter.wait()


# ── Module-level buffer registry ──────────────────────────────────

_buffers: dict[str, EventBuffer] = {}
_tasks: dict[str, asyncio.Task] = {}

_BUFFER_TTL = 60  # seconds to keep buffer after stream ends


def _buffer_key(user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def get_or_create_buffer(user_id: str, session_id: str) -> EventBuffer:
    key = _buffer_key(user_id, session_id)
    # Always create a fresh buffer for a new query — the old one (if any)
    # was from a previous request on the same session and must not be reused.
    _buffers[key] = EventBuffer()
    return _buffers[key]


def get_buffer(user_id: str, session_id: str) -> EventBuffer | None:
    return _buffers.get(_buffer_key(user_id, session_id))


def remove_buffer(user_id: str, session_id: str) -> None:
    _buffers.pop(_buffer_key(user_id, session_id), None)


async def schedule_buffer_cleanup(user_id: str, session_id: str) -> None:
    """Remove the buffer after a grace period (gives client time to reconnect)."""
    await asyncio.sleep(_BUFFER_TTL)
    remove_buffer(user_id, session_id)


# ── Task registry (for cancel support) ───────────────────────────


def register_task(user_id: str, session_id: str, task: asyncio.Task) -> None:
    _tasks[_buffer_key(user_id, session_id)] = task


def cancel_task(user_id: str, session_id: str) -> bool:
    """Cancel the background producer task. Returns True if found and cancelled."""
    key = _buffer_key(user_id, session_id)
    task = _tasks.get(key)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def remove_task(user_id: str, session_id: str) -> None:
    _tasks.pop(_buffer_key(user_id, session_id), None)


async def event_stream(events: AsyncIterator[RuntimeEvent]):
    """Generate SSE stream from RuntimeEvent iterator (legacy, no id)."""
    try:
        async for event in events:
            sse = event_to_sse(event)
            if sse:
                yield sse
    except asyncio.CancelledError:
        pass  # Client disconnected
