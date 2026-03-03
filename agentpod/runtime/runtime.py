"""High-level AgentRuntime facade that wires everything together."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

from agentpod.providers.base import ModelProvider
from agentpod.tools import create_default_registry
from agentpod.types import (
    ContextSnapshot,
    Done,
    MessageStart,
    RuntimeEvent,
    RuntimeOptions,
    SessionMeta,
    TextDelta,
    ToolStart,
    UserInputRequired,
)

from agentpod.runtime.context import ContextManager
from agentpod.runtime.loop import AgenticLoop
from agentpod.runtime.prompt import PromptManager
from agentpod.runtime.session import SessionManager


class AgentRuntime:
    def __init__(self, cwd: Path, config=None):
        self.cwd = Path(cwd)

        # Resolve shared_dir from config if provided
        shared_dir: Path | None = None
        if config and getattr(config, "shared_dir", ""):
            shared_dir = Path(config.shared_dir)

        self.session_mgr = SessionManager(self.cwd / "sessions")
        self.tool_registry = create_default_registry(shared_dir=shared_dir)
        self.prompt_mgr = PromptManager(self.cwd, shared_dir=shared_dir)
        self.context_mgr = ContextManager()
        self._provider: ModelProvider | None = None

    def _get_provider(self) -> ModelProvider:
        if self._provider is None:
            from agentpod.providers import get_provider
            self._provider = get_provider("volcengine")
        return self._provider

    async def create_session(self) -> str:
        return self.session_mgr.create()

    async def list_sessions(self) -> list[SessionMeta]:
        return self.session_mgr.list()

    async def resume_session(self, session_id: str) -> SessionMeta:
        return self.session_mgr.get_meta(session_id)

    async def fork_session(self, session_id: str) -> str:
        return self.session_mgr.fork(session_id)

    async def query(
        self,
        prompt: str | list,
        session_id: str | None = None,
        options: RuntimeOptions | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        if options is None:
            options = RuntimeOptions()

        if session_id is None:
            session_id = self.session_mgr.create()

        # Load session history
        history = self.session_mgr.load(session_id)

        # Build messages
        system_prompt = self.prompt_mgr.load()
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        # If prompt is empty and last message is a tool response, this is a
        # resume after ask_user — skip appending a user message.
        is_resume = (
            not prompt  # empty string or empty list
            and history
            and history[-1].get("role") == "tool"
        )
        if not is_resume:
            messages.append({"role": "user", "content": prompt})
            self.session_mgr.append(session_id, {"role": "user", "content": prompt})

        # Run loop
        provider = self._get_provider()
        loop = AgenticLoop(provider, self.tool_registry, self.context_mgr)

        yield MessageStart(session_id=session_id, model=options.model)

        assistant_content = ""
        assistant_tool_calls = []
        async for event in loop.run(messages, options, self.cwd):
            if isinstance(event, TextDelta):
                assistant_content += event.content
            elif isinstance(event, ToolStart):
                # Track tool calls for session persistence
                assistant_tool_calls.append(event)
            elif isinstance(event, UserInputRequired):
                # Save assistant message (with tool_calls) before closing stream
                msg: dict = {
                    "role": "assistant",
                    "content": assistant_content or None,
                }
                if assistant_tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": event.tool_use_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool,
                                "arguments": json.dumps(
                                    tc.input, ensure_ascii=False
                                ),
                            },
                        }
                        for tc in assistant_tool_calls
                    ]
                self.session_mgr.append(session_id, msg)
            elif isinstance(event, Done):
                if assistant_content:
                    self.session_mgr.append(
                        session_id,
                        {"role": "assistant", "content": assistant_content},
                    )
            yield event

    async def answer(self, session_id: str, tool_use_id: str, response: str):
        """Resume from an ask_user pause."""
        self.session_mgr.append(
            session_id,
            {"role": "tool", "tool_call_id": tool_use_id, "content": response},
        )

    async def get_context_info(
        self, session_id: str, context_window: int = 200_000
    ) -> ContextSnapshot:
        """Return a context usage snapshot including system prompt and tools."""
        history = self.session_mgr.load(session_id)
        system_prompt = self.prompt_mgr.load()
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        tools = self.tool_registry.to_llm_schema()
        return self.context_mgr.get_snapshot(messages, context_window, tools)
