"""Server-Sent Events helpers for streaming RuntimeEvents."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

from agentpod.types import (
    Done,
    Error,
    RuntimeEvent,
    TextDelta,
    TodoUpdate,
    ToolEnd,
    ToolStart,
    TurnComplete,
    UserInputRequired,
)


def event_to_sse(event: RuntimeEvent) -> str:
    """Convert a RuntimeEvent to SSE format string."""
    if isinstance(event, TextDelta):
        return f"event: text_delta\ndata: {json.dumps({'content': event.content})}\n\n"
    elif isinstance(event, ToolStart):
        return f"event: tool_start\ndata: {json.dumps({'tool': event.tool, 'input': event.input})}\n\n"
    elif isinstance(event, ToolEnd):
        return f"event: tool_end\ndata: {json.dumps({'tool': event.tool, 'result': event.result, 'is_error': event.is_error})}\n\n"
    elif isinstance(event, TurnComplete):
        return f"event: turn_complete\ndata: {json.dumps({'turn': event.turn})}\n\n"
    elif isinstance(event, UserInputRequired):
        return f"event: user_input_required\ndata: {json.dumps({'tool_use_id': event.tool_use_id, 'question': event.question, 'options': event.options})}\n\n"
    elif isinstance(event, TodoUpdate):
        return f"event: todo_update\ndata: {json.dumps({'todos': event.todos})}\n\n"
    elif isinstance(event, Done):
        return f"event: done\ndata: {json.dumps({'usage': event.usage, 'cost': event.cost})}\n\n"
    elif isinstance(event, Error):
        return f"event: error\ndata: {json.dumps({'message': event.message, 'retryable': event.retryable})}\n\n"
    return ""


async def event_stream(events: AsyncIterator[RuntimeEvent]):
    """Generate SSE stream from RuntimeEvent iterator."""
    try:
        async for event in events:
            sse = event_to_sse(event)
            if sse:
                yield sse
    except asyncio.CancelledError:
        pass  # Client disconnected
