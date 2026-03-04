"""The core agentic loop: LLM call -> tool execution -> repeat."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from agentpod.providers.base import ModelProvider, calculate_cost
from agentpod.tools import ToolRegistry
from agentpod.types import (
    ContextSnapshotEvent,
    Done,
    Error,
    ReasoningDelta,
    RuntimeEvent,
    RuntimeOptions,
    TextDelta,
    TodoUpdate,
    ToolCallStart,
    ToolEnd,
    ToolStart,
    TurnComplete,
    UserInputRequired,
)

from agentpod.runtime.context import ContextManager

_log = logging.getLogger("agentpod.loop")


class AgenticLoop:
    def __init__(
        self,
        provider: ModelProvider,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        user_id: str = "",
        shared_dir: Path | None = None,
    ):
        self.provider = provider
        self.tools = tool_registry
        self.context = context_manager
        self._user_id = user_id
        self._shared_dir = shared_dir

    async def run(
        self,
        messages: list[dict],
        options: RuntimeOptions,
        cwd: Path,
    ) -> AsyncIterator[RuntimeEvent]:
        model_info = self.provider.get_model_info(options.model) or self.provider.list_models()[0]
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        total_cost = 0.0
        turn = 0
        while turn < options.max_turns:
            turn += 1

            # Refresh Edge tools from Edge Agent (no-op if not connected)
            await self._refresh_edge_tools()

            # Check compression
            estimated = self.context.estimate_tokens(
                messages, self.tools.to_llm_schema()
            )
            if self.context.should_compress(
                estimated, options.context_window, options.compress_threshold
            ):
                summary = await self.context.compress(messages, self.provider)
                messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": f"[Previous conversation summary]\n{summary}",
                    },
                ]

            # Map effort
            effort_map = {
                "low": "low",
                "medium": "medium",
                "high": "high",
                "max": "high",
            }
            kwargs = {"reasoning_effort": effort_map.get(options.effort, "high")}

            # Call LLM with streaming
            tools_schema = self.tools.to_llm_schema()
            result = await self._call_with_retry(
                messages, options.model, tools_schema, kwargs
            )
            if isinstance(result, Error):
                yield result
                return
            # Process streaming response — execute tools immediately when encountered
            full_content = ""
            tool_calls = None
            usage = {}
            stop_reason = "end_turn"
            assistant_insert_pos = None

            async for chunk in result:
                if chunk["type"] == "reasoning_delta":
                    yield ReasoningDelta(content=chunk["content"])
                elif chunk["type"] == "text_delta":
                    full_content += chunk["content"]
                    yield TextDelta(content=chunk["content"])
                elif chunk["type"] == "tool_call_start":
                    yield ToolCallStart(tool=chunk["name"])
                elif chunk["type"] == "tool_use":
                    tool_calls = chunk["tool_calls"]
                    stop_reason = "tool_use"
                    # Record position BEFORE tool results are appended
                    if assistant_insert_pos is None:
                        assistant_insert_pos = len(messages)
                    # Execute tools immediately (interleaved with streaming text)
                    for tc in tool_calls:
                        tool_events = await self._execute_tool(tc, messages, cwd)
                        for evt in tool_events:
                            yield evt
                            if isinstance(evt, UserInputRequired):
                                total_usage["turns"] = turn
                                yield Done(usage=total_usage, cost=total_cost, stop_reason="end_turn")
                                return
                elif chunk["type"] == "done":
                    usage = chunk.get("usage", {})
                    stop_reason = chunk.get("stop_reason", stop_reason)

            # Update usage tracking
            total_usage["input_tokens"] += usage.get("input_tokens", 0)
            total_usage["output_tokens"] += usage.get("output_tokens", 0)
            total_usage["cached_tokens"] += usage.get("cached_tokens", 0)
            self.context.update_from_response(usage)

            # Emit context snapshot so the frontend can track usage in real time
            snapshot = self.context.get_snapshot(
                messages, options.context_window, tools_schema
            )
            yield ContextSnapshotEvent(snapshot=snapshot)

            # Calculate cost
            turn_cost = calculate_cost(usage, model_info)
            total_cost += turn_cost

            # Append assistant message at correct position.
            # When tools were executed during streaming, their results were
            # already appended to messages.  The assistant message must come
            # BEFORE those tool results so the conversation order is valid:
            #   … → assistant (with tool_calls) → tool result(s)
            assistant_msg: dict = {"role": "assistant", "content": full_content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            if assistant_insert_pos is not None:
                messages.insert(assistant_insert_pos, assistant_msg)
            else:
                messages.append(assistant_msg)

            # Handle tool calls
            if stop_reason == "tool_use" and tool_calls:
                # Tools already executed during streaming; just do turn bookkeeping
                yield TurnComplete(turn=turn, usage=total_usage.copy(), cost=total_cost)

                # Budget check
                if options.max_budget_usd and total_cost >= options.max_budget_usd:
                    total_usage["turns"] = turn
                    yield Done(usage=total_usage, cost=total_cost, stop_reason="budget")
                    return

                continue  # Next turn

            # End turn (no tool calls)
            yield TurnComplete(turn=turn, usage=total_usage.copy(), cost=total_cost)
            total_usage["turns"] = turn
            yield Done(usage=total_usage, cost=total_cost, stop_reason="end_turn")
            return

        # Max turns reached
        total_usage["turns"] = turn
        yield Done(usage=total_usage, cost=total_cost, stop_reason="max_turns")

    async def _refresh_edge_tools(self):
        """Discover Edge tools from Edge Agent, replacing any previous ones."""
        if not self._user_id:
            return

        # Remove existing edge_ tools
        for name in [n for n in self.tools._tools if n.startswith("edge_")]:
            self.tools.unregister(name)

        from agentpod.edge import edge_manager
        conn = edge_manager.get(self._user_id)
        if conn is None:
            return

        from agentpod.tools.edge import discover_edge_tools, load_edge_config
        edge_config = load_edge_config(self._shared_dir)
        tools = await discover_edge_tools(conn, edge_config)
        for tool in tools:
            self.tools.register(tool)
        if tools:
            _log.info("Refreshed %d Edge tools for user %s", len(tools), self._user_id)

    async def _call_with_retry(
        self,
        messages: list[dict],
        model: str,
        tools_schema: list[dict],
        kwargs: dict,
    ) -> AsyncIterator[dict] | Error:
        last_error: Exception | None = None
        for attempt in range(4):  # 1 initial + 3 retries
            try:
                return await self.provider.chat(
                    messages=messages,
                    model=model,
                    tools=tools_schema,
                    stream=True,
                    **kwargs,
                )
            except Exception as e:
                last_error = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (429, 529) and attempt < 3:
                    await asyncio.sleep(2**attempt)
                    continue
                return Error(
                    message=str(e),
                    retryable=status in (429, 529) if status else False,
                )
        return Error(message=str(last_error), retryable=True)

    async def _execute_tool(
        self,
        tc: dict,
        messages: list[dict],
        cwd: Path,
    ) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            tool_input = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_input = {}
        tool_id = tc.get("id", "")

        events.append(ToolStart(tool=tool_name, input=tool_input))

        # Special handling for ask_user
        if tool_name == "ask_user":
            events.append(
                UserInputRequired(
                    tool_use_id=tool_id,
                    question=tool_input.get("question", ""),
                    options=tool_input.get("options"),
                )
            )
            return events

        # Special handling for todo_write
        if tool_name == "todo_write":
            todos = tool_input.get("todos", [])
            events.append(TodoUpdate(todos=todos))

        # Execute tool
        try:
            tool = self.tools.get(tool_name)
            tool_result = await tool.execute(tool_input, cwd)
            events.append(
                ToolEnd(
                    tool=tool_name,
                    result=tool_result.content,
                    is_error=tool_result.is_error,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result.content,
                }
            )
        except Exception as e:
            error_msg = f"Tool execution error: {e}"
            events.append(ToolEnd(tool=tool_name, result=error_msg, is_error=True))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": error_msg,
                }
            )

        return events
