from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class PricingRule:
    input_up_to: int | None = None
    output_up_to: int | None = None
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0


@dataclass
class CachePricing:
    hit_price_per_1m: float = 0.0
    write_price_per_1m: float | None = None
    storage_price_per_1m_per_hour: float | None = None


@dataclass
class ModelInfo:
    id: str
    name: str
    context_window: int
    pricing_rules: list[PricingRule] = field(default_factory=list)
    cache_pricing: CachePricing | None = None


@dataclass
class ChatResponse:
    content: str
    stop_reason: str  # "end_turn" or "tool_use"
    usage: dict  # {"input_tokens": int, "output_tokens": int, "cached_tokens": int}
    model: str
    tool_calls: list[dict] | None = None  # for tool_use responses


class ModelProvider(ABC):
    def __init__(self, config):
        from agentpod.config import ProviderConfig

        self.config: ProviderConfig = config
        self.client = self._create_client()

    @abstractmethod
    def _create_client(self) -> Any: ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> ChatResponse | AsyncIterator[dict]: ...

    async def count_tokens(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
    ) -> int:
        return self._estimate_tokens(messages, tools)

    @abstractmethod
    def list_models(self) -> list[ModelInfo]: ...

    def _estimate_tokens(self, messages, tools=None) -> int:
        # Rough estimation: Chinese ~1.5 char/token, English ~4 char/token
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
            import json

            total_chars += len(json.dumps(tools, ensure_ascii=False))
        # Assume mixed content, ~2.5 chars per token average
        return max(1, int(total_chars / 2.5))


def calculate_cost(usage: dict, model_info: ModelInfo) -> float:
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cached_tokens = usage.get("cached_tokens", 0)

    # Match pricing rule
    input_price = 0.0
    output_price = 0.0
    for rule in model_info.pricing_rules:
        input_match = rule.input_up_to is None or input_tokens <= rule.input_up_to
        output_match = rule.output_up_to is None or output_tokens <= rule.output_up_to
        if input_match and output_match:
            input_price = rule.input_price_per_1m
            output_price = rule.output_price_per_1m
            break

    cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    # Cache pricing
    if cached_tokens > 0 and model_info.cache_pricing:
        cost += cached_tokens * model_info.cache_pricing.hit_price_per_1m / 1_000_000
        # Subtract cached tokens from input cost (they were already counted)
        cost -= cached_tokens * input_price / 1_000_000

    return cost
