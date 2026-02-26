"""Tests for calculate_cost() across all pricing tiers."""

from agentpod.providers.base import (
    CachePricing,
    ModelInfo,
    PricingRule,
    calculate_cost,
)

# Shared model info matching doubao-seed-1-8-251228 pricing
_MODEL = ModelInfo(
    id="doubao-seed-1-8-251228",
    name="Doubao Seed 1.8",
    context_window=256000,
    pricing_rules=[
        PricingRule(input_up_to=32000, output_up_to=200, input_price_per_1m=0.8, output_price_per_1m=2.0),
        PricingRule(input_up_to=32000, output_up_to=None, input_price_per_1m=0.8, output_price_per_1m=8.0),
        PricingRule(input_up_to=128000, output_up_to=None, input_price_per_1m=1.2, output_price_per_1m=16.0),
        PricingRule(input_up_to=None, output_up_to=None, input_price_per_1m=2.4, output_price_per_1m=24.0),
    ],
    cache_pricing=CachePricing(hit_price_per_1m=0.16),
)


def test_tier1_small_input_small_output():
    """30k input + 100 output -> tier 1 (0.8 / 2.0)"""
    usage = {"input_tokens": 30_000, "output_tokens": 100, "cached_tokens": 0}
    cost = calculate_cost(usage, _MODEL)
    expected = (30_000 * 0.8 + 100 * 2.0) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_tier2_small_input_large_output():
    """30k input + 500 output -> tier 2 (0.8 / 8.0)"""
    usage = {"input_tokens": 30_000, "output_tokens": 500, "cached_tokens": 0}
    cost = calculate_cost(usage, _MODEL)
    expected = (30_000 * 0.8 + 500 * 8.0) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_tier3_medium_input():
    """50k input + 1000 output -> tier 3 (1.2 / 16.0)"""
    usage = {"input_tokens": 50_000, "output_tokens": 1_000, "cached_tokens": 0}
    cost = calculate_cost(usage, _MODEL)
    expected = (50_000 * 1.2 + 1_000 * 16.0) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_tier4_large_input():
    """200k input + 1000 output -> tier 4 (2.4 / 24.0)"""
    usage = {"input_tokens": 200_000, "output_tokens": 1_000, "cached_tokens": 0}
    cost = calculate_cost(usage, _MODEL)
    expected = (200_000 * 2.4 + 1_000 * 24.0) / 1_000_000
    assert abs(cost - expected) < 1e-9
