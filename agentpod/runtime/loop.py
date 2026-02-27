"""The core agentic loop: LLM call -> tool execution -> repeat."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from agentpod.providers.base import ModelProvider, calculate_cost
from agentpod.tools import ToolRegistry
from agentpod.types import (
    Done,
    Error,
    RuntimeEvent,
    RuntimeOptions,
    TextDelta,
    TodoUpdate,
    ToolEnd,
    ToolStart,
    TurnComplete,
    UserInputRequired,
)

from agentpod.runtime.context import ContextManager


class AgenticLoop:
    def __init__(
        self,
        provider: ModelProvider,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
    ):
        self.provider = provider
        self.tools = tool_registry
        self.context = context_manager

    async def run(
        self,
        messages: list[dict],
        options: RuntimeOptions,
        cwd: Path,
    ) -> AsyncIterator[RuntimeEvent]:
        model_info = self.provider.list_models()[0]
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        total_cost = 0.0
        turn = 0
        while turn < options.max_turns:
            turn += 1

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

            async for chunk in result:
                if chunk["type"] == "text_delta":
                    full_content += chunk["content"]
                    yield TextDelta(content=chunk["content"])
                elif chunk["type"] == "tool_use":
                    tool_calls = chunk["tool_calls"]
                    stop_reason = "tool_use"
                    # Execute tools immediately (interleaved with streaming text)
                    for tc in tool_calls:
                        tool_events = await self._execute_tool(tc, messages, cwd)
                        for evt in tool_events:
                            yield evt
                            if isinstance(evt, UserInputRequired):
                                total_usage["turns"] = turn
                                yield Done(usage=total_usage, cost=total_cost)
                                return
                elif chunk["type"] == "done":
                    usage = chunk.get("usage", {})
                    stop_reason = chunk.get("stop_reason", stop_reason)

            # Update usage tracking
            total_usage["input_tokens"] += usage.get("input_tokens", 0)
            total_usage["output_tokens"] += usage.get("output_tokens", 0)
            total_usage["cached_tokens"] += usage.get("cached_tokens", 0)
            self.context.update_from_response(usage)

            # Calculate cost
            turn_cost = calculate_cost(usage, model_info)
            total_cost += turn_cost

            # Append assistant message
            assistant_msg: dict = {"role": "assistant", "content": full_content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            # Handle tool calls
            if stop_reason == "tool_use" and tool_calls:
                # Tools already executed during streaming; just do turn bookkeeping
                yield TurnComplete(turn=turn)

                # Budget check
                if options.max_budget_usd and total_cost >= options.max_budget_usd:
                    yield Done(usage=total_usage, cost=total_cost)
                    return

                continue  # Next turn

            # End turn (no tool calls)
            yield TurnComplete(turn=turn)
            yield Done(usage=total_usage, cost=total_cost)
            return

        # Max turns reached
        yield Done(usage=total_usage, cost=total_cost)

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
