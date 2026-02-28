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
        self._last_request_chars = 0  # char count of the most recent request

    def _count_chars(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        """Count total characters in messages + tools schema."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += len(part["text"])
            # tool_calls in assistant messages
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                total += len(func.get("name", ""))
                total += len(func.get("arguments", ""))
        if tools:
            total += len(json.dumps(tools, ensure_ascii=False))
        return total

    def estimate_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        total_chars = self._count_chars(messages, tools)
        # Stash for calibration when the API response comes back
        self._last_request_chars = total_chars
        return max(1, int(total_chars / self._calibration_factor))

    def update_from_response(self, usage: dict):
        """Calibrate _calibration_factor using real input_tokens from API response.

        Uses exponential moving average (EMA) with alpha=0.3 so recent
        observations weigh more, but the factor doesn't swing wildly.
        """
        real_input = usage.get("input_tokens", 0)
        if real_input <= 0 or self._last_request_chars <= 0:
            return
        observed_factor = self._last_request_chars / real_input
        self._sample_count += 1
        if self._sample_count == 1:
            # First sample: jump straight to observed value
            self._calibration_factor = observed_factor
        else:
            alpha = 0.3
            self._calibration_factor = (
                alpha * observed_factor + (1 - alpha) * self._calibration_factor
            )

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
