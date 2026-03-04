"""Tests for multi-provider routing and per-model pricing lookup."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from agentpod.providers.base import (
    CachePricing,
    ChatResponse,
    ModelInfo,
    ModelProvider,
    PricingRule,
    calculate_cost,
)
from agentpod.providers import ProviderRegistry


# ---------------------------------------------------------------------------
# Fake providers for testing (no real API calls)
# ---------------------------------------------------------------------------

class _FakeProviderA(ModelProvider):
    """Pretends to be volcengine with doubao models."""

    def __init__(self):
        # Skip base __init__ (needs ProviderConfig); set attrs directly
        self.client = None

    def _create_client(self) -> Any:
        return None

    async def chat(self, messages, model=None, tools=None, stream=False, **kw):
        pass

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="doubao-seed-1-8",
                name="Doubao Seed 1.8",
                context_window=256000,
                pricing_rules=[
                    PricingRule(input_price_per_1m=0.8, output_price_per_1m=8.0),
                ],
            ),
            ModelInfo(
                id="doubao-lite",
                name="Doubao Lite",
                context_window=128000,
                pricing_rules=[
                    PricingRule(input_price_per_1m=0.0, output_price_per_1m=0.0),
                ],
            ),
        ]


class _FakeProviderB(ModelProvider):
    """Pretends to be zhipu with glm models."""

    def __init__(self):
        self.client = None

    def _create_client(self) -> Any:
        return None

    async def chat(self, messages, model=None, tools=None, stream=False, **kw):
        pass

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="glm-5",
                name="GLM-5",
                context_window=200000,
                pricing_rules=[
                    PricingRule(input_up_to=32000, input_price_per_1m=4.0, output_price_per_1m=18.0),
                    PricingRule(input_price_per_1m=6.0, output_price_per_1m=22.0),
                ],
                cache_pricing=CachePricing(hit_price_per_1m=1.0),
            ),
            ModelInfo(
                id="glm-4-flash",
                name="GLM-4 Flash",
                context_window=128000,
                pricing_rules=[
                    PricingRule(input_price_per_1m=0.0, output_price_per_1m=0.0),
                ],
            ),
        ]


def _make_registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register("volcengine", _FakeProviderA())
    reg.register("zhipu", _FakeProviderB())
    return reg


# ---------------------------------------------------------------------------
# 1. Provider routing: get_provider_for_model
# ---------------------------------------------------------------------------

class TestProviderRouting:
    def test_route_to_volcengine(self):
        reg = _make_registry()
        provider = reg.get_provider_for_model("doubao-seed-1-8")
        assert isinstance(provider, _FakeProviderA)

    def test_route_to_zhipu(self):
        reg = _make_registry()
        provider = reg.get_provider_for_model("glm-5")
        assert isinstance(provider, _FakeProviderB)

    def test_route_glm_flash(self):
        reg = _make_registry()
        provider = reg.get_provider_for_model("glm-4-flash")
        assert isinstance(provider, _FakeProviderB)

    def test_route_doubao_lite(self):
        reg = _make_registry()
        provider = reg.get_provider_for_model("doubao-lite")
        assert isinstance(provider, _FakeProviderA)

    def test_unknown_model_raises(self):
        reg = _make_registry()
        with pytest.raises(KeyError, match="No provider found for model 'gpt-4'"):
            reg.get_provider_for_model("gpt-4")

    def test_error_lists_available_models(self):
        reg = _make_registry()
        with pytest.raises(KeyError, match="doubao-seed-1-8") as exc_info:
            reg.get_provider_for_model("nonexistent")
        msg = str(exc_info.value)
        assert "glm-5" in msg
        assert "glm-4-flash" in msg

    def test_empty_registry_raises(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="\\(none\\)"):
            reg.get_provider_for_model("anything")


# ---------------------------------------------------------------------------
# 2. get_model_info: per-model lookup
# ---------------------------------------------------------------------------

class TestGetModelInfo:
    def test_found(self):
        provider = _FakeProviderB()
        info = provider.get_model_info("glm-5")
        assert info is not None
        assert info.id == "glm-5"
        assert info.context_window == 200000

    def test_not_found(self):
        provider = _FakeProviderB()
        assert provider.get_model_info("nonexistent") is None

    def test_correct_pricing_returned(self):
        provider = _FakeProviderB()
        info = provider.get_model_info("glm-4-flash")
        assert info is not None
        assert info.pricing_rules[0].input_price_per_1m == 0.0

    def test_cache_pricing(self):
        provider = _FakeProviderB()
        info = provider.get_model_info("glm-5")
        assert info.cache_pricing is not None
        assert info.cache_pricing.hit_price_per_1m == 1.0


# ---------------------------------------------------------------------------
# 3. Cost calculation uses correct model (not list_models()[0])
# ---------------------------------------------------------------------------

class TestCostWithCorrectModel:
    """Verify that using get_model_info picks the right pricing,
    not always the first model's pricing."""

    def test_glm5_cost_uses_glm5_pricing(self):
        """glm-5 with 10k input should use tier 1 (4.0/18.0), not glm-4-flash (0/0)."""
        provider = _FakeProviderB()
        model_info = provider.get_model_info("glm-5")
        usage = {"input_tokens": 10_000, "output_tokens": 500, "cached_tokens": 0}
        cost = calculate_cost(usage, model_info)
        expected = (10_000 * 4.0 + 500 * 18.0) / 1_000_000
        assert abs(cost - expected) < 1e-9
        assert cost > 0  # Must not be zero

    def test_flash_cost_is_zero(self):
        """glm-4-flash is free — cost must be 0."""
        provider = _FakeProviderB()
        model_info = provider.get_model_info("glm-4-flash")
        usage = {"input_tokens": 10_000, "output_tokens": 500, "cached_tokens": 0}
        cost = calculate_cost(usage, model_info)
        assert cost == 0.0

    def test_wrong_model_gives_wrong_cost(self):
        """Demonstrate the old bug: if we always use list_models()[0],
        glm-4-flash queries would be charged at glm-5 prices."""
        provider = _FakeProviderB()
        first_model = provider.list_models()[0]  # glm-5
        flash_model = provider.get_model_info("glm-4-flash")
        usage = {"input_tokens": 10_000, "output_tokens": 500, "cached_tokens": 0}
        wrong_cost = calculate_cost(usage, first_model)
        right_cost = calculate_cost(usage, flash_model)
        assert wrong_cost > 0
        assert right_cost == 0.0
        assert wrong_cost != right_cost  # The bug would make these equal

    def test_glm5_tier2_large_input(self):
        """glm-5 with 50k input -> tier 2 (6.0/22.0)."""
        provider = _FakeProviderB()
        model_info = provider.get_model_info("glm-5")
        usage = {"input_tokens": 50_000, "output_tokens": 1_000, "cached_tokens": 0}
        cost = calculate_cost(usage, model_info)
        expected = (50_000 * 6.0 + 1_000 * 22.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_glm5_cache_pricing(self):
        """glm-5 with cached tokens: cache hit replaces input price."""
        provider = _FakeProviderB()
        model_info = provider.get_model_info("glm-5")
        usage = {"input_tokens": 10_000, "output_tokens": 100, "cached_tokens": 5_000}
        cost = calculate_cost(usage, model_info)
        # tier 1: input=4.0, output=18.0, cache_hit=1.0
        # cost = (10k * 4.0 + 100 * 18.0) / 1M + (5k * 1.0 - 5k * 4.0) / 1M
        base = (10_000 * 4.0 + 100 * 18.0) / 1_000_000
        cache_adj = (5_000 * 1.0 - 5_000 * 4.0) / 1_000_000
        expected = base + cache_adj
        assert abs(cost - expected) < 1e-9
