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

    def _estimate_chars_to_tokens(self, chars: int) -> int:
        """Convert character count to token estimate using calibration factor."""
        if chars <= 0:
            return 0
        return max(1, int(chars / self._calibration_factor))

    def get_snapshot(
        self,
        messages: list[dict],
        context_window: int,
        tools: list[dict] | None = None,
        reserved_output_tokens: int = 8192,
    ) -> ContextSnapshot:
        """Build a detailed per-component context breakdown.

        Expects messages[0] to be the system prompt (role=system).
        """
        # System prompt tokens (first message if role=system)
        system_chars = 0
        conversation_msgs = messages
        if messages and messages[0].get("role") == "system":
            system_content = messages[0].get("content", "")
            system_chars = len(system_content) if isinstance(system_content, str) else 0
            conversation_msgs = messages[1:]

        system_prompt_tokens = self._estimate_chars_to_tokens(system_chars)

        # Tools tokens
        tools_chars = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
        tools_tokens = self._estimate_chars_to_tokens(tools_chars)

        # Messages tokens (everything except system prompt, no tools)
        messages_chars = self._count_chars(conversation_msgs)
        messages_tokens = self._estimate_chars_to_tokens(messages_chars)

        # Stash total chars for calibration
        self._last_request_chars = system_chars + tools_chars + messages_chars

        used_tokens = system_prompt_tokens + tools_tokens + messages_tokens + reserved_output_tokens
        available_tokens = max(0, context_window - used_tokens)
        usage_ratio = used_tokens / context_window if context_window > 0 else 0.0

        return ContextSnapshot(
            context_window=context_window,
            system_prompt_tokens=system_prompt_tokens,
            tools_tokens=tools_tokens,
            messages_tokens=messages_tokens,
            reserved_output_tokens=reserved_output_tokens,
            used_tokens=used_tokens,
            available_tokens=available_tokens,
            usage_ratio=round(usage_ratio, 6),
            message_count=len(conversation_msgs),
        )
