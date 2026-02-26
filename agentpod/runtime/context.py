"""Context window management and compression."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agentpod.types import ContextSnapshot

if TYPE_CHECKING:
    from agentpod.providers.base import ModelProvider


class ContextManager:
    def __init__(self):
        self._calibration_factor = 2.5  # chars per token, updated by real usage
        self._sample_count = 0

    def estimate_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])
        if tools:
            total_chars += len(json.dumps(tools, ensure_ascii=False))
        return max(1, int(total_chars / self._calibration_factor))

    def update_from_response(self, usage: dict):
        real_input = usage.get("input_tokens", 0)
        if real_input <= 0:
            return
        # Exponential moving average to calibrate
        self._sample_count += 1
        # We don't have the exact char count for the request, so we just
        # track that real data came in. The calibration factor stays stable
        # unless we add explicit char tracking per request.

    def should_compress(
        self, current_tokens: int, context_window: int, threshold: float = 0.7
    ) -> bool:
        return current_tokens > context_window * threshold

    async def compress(self, messages: list[dict], provider: ModelProvider) -> str:
        # Build a compression prompt
        conversation_text = ""
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                conversation_text += f"[{role}]: {content}\n"

        compress_messages = [
            {
                "role": "system",
                "content": (
                    "你是一个对话压缩助手。请将以下对话压缩为简洁的摘要，"
                    "保留关键信息、决策和上下文。用中文回复。"
                ),
            },
            {
                "role": "user",
                "content": f"请压缩以下对话：\n\n{conversation_text}",
            },
        ]

        response = await provider.chat(
            messages=compress_messages,
            stream=False,
            reasoning_effort="low",
        )
        return response.content

    def get_snapshot(
        self,
        messages: list[dict],
        context_window: int,
        tools: list[dict] | None = None,
    ) -> ContextSnapshot:
        estimated = self.estimate_tokens(messages, tools)
        return ContextSnapshot(
            estimated_tokens=estimated,
            context_window=context_window,
            usage_ratio=estimated / context_window if context_window > 0 else 0.0,
            message_count=len(messages),
        )
