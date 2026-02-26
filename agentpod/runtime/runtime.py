"""High-level AgentRuntime facade that wires everything together."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from agentpod.providers.base import ModelProvider
from agentpod.tools import create_default_registry
from agentpod.types import (
    ContextSnapshot,
    Done,
    RuntimeEvent,
    RuntimeOptions,
    SessionMeta,
    TextDelta,
)

from agentpod.runtime.context import ContextManager
from agentpod.runtime.loop import AgenticLoop
from agentpod.runtime.prompt import PromptManager
from agentpod.runtime.session import SessionManager


class AgentRuntime:
    def __init__(self, cwd: Path):
        self.cwd = Path(cwd)
        self.session_mgr = SessionManager(self.cwd / "sessions")
        self.tool_registry = create_default_registry()
        self.prompt_mgr = PromptManager(self.cwd)
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
        prompt: str,
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
        messages.append({"role": "user", "content": prompt})

        # Persist user message
        self.session_mgr.append(session_id, {"role": "user", "content": prompt})

        # Run loop
        provider = self._get_provider()
        loop = AgenticLoop(provider, self.tool_registry, self.context_mgr)

        assistant_content = ""
        async for event in loop.run(messages, options, self.cwd):
            if isinstance(event, TextDelta):
                assistant_content += event.content
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

    async def get_context_info(self, session_id: str) -> ContextSnapshot:
        messages = self.session_mgr.load(session_id)
        return self.context_mgr.get_snapshot(messages, 200000)
