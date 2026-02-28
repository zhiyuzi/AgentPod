"""Tests for ContextManager."""

import pytest
import dotenv

dotenv.load_dotenv()

from agentpod.runtime.context import ContextManager
from agentpod.providers import get_provider


@pytest.fixture
def ctx() -> ContextManager:
    return ContextManager()


# ── Token estimation ──────────────────────────────────────────


def test_estimate_tokens_english(ctx: ContextManager):
    messages = [{"role": "user", "content": "Hello world, this is a test message."}]
    tokens = ctx.estimate_tokens(messages)
    # ~35 chars / 2.5 = ~14 tokens
    assert 5 < tokens < 50


def test_estimate_tokens_chinese(ctx: ContextManager):
    messages = [{"role": "user", "content": "你好世界，这是一个测试消息。"}]
    tokens = ctx.estimate_tokens(messages)
    # ~12 chars / 2.5 = ~5 tokens
    assert 2 < tokens < 30


def test_estimate_tokens_with_tools(ctx: ContextManager):
    messages = [{"role": "user", "content": "test"}]
    tools = [{"type": "function", "function": {"name": "bash", "description": "Run a command", "parameters": {}}}]
    tokens_without = ctx.estimate_tokens(messages)
    tokens_with = ctx.estimate_tokens(messages, tools)
    assert tokens_with > tokens_without


def test_estimate_tokens_includes_tool_calls(ctx: ContextManager):
    """tool_calls in assistant messages should be counted."""
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command": "ls -la /tmp"}',
                    },
                }
            ],
        },
    ]
    tokens = ctx.estimate_tokens(messages)
    # Should include chars from tool_calls arguments
    assert tokens > ctx.estimate_tokens([{"role": "user", "content": "hi"}])


# ── Compression threshold ────────────────────────────────────


def test_should_compress_below_threshold(ctx: ContextManager):
    # 100 tokens, 200 window, 0.7 threshold -> 100 < 140 -> False
    assert ctx.should_compress(100, 200, 0.7) is False


def test_should_compress_above_threshold(ctx: ContextManager):
    # 150 tokens, 200 window, 0.7 threshold -> 150 > 140 -> True
    assert ctx.should_compress(150, 200, 0.7) is True


def test_should_compress_at_boundary(ctx: ContextManager):
    # Exactly at threshold: 140 tokens, 200 window, 0.7 -> 140 == 140 -> False
    assert ctx.should_compress(140, 200, 0.7) is False
    # Just above: 141 > 140 -> True
    assert ctx.should_compress(141, 200, 0.7) is True


# ── Calibration (update_from_response) ───────────────────────


def test_calibration_first_sample(ctx: ContextManager):
    """First real API response should set calibration factor directly."""
    messages = [{"role": "user", "content": "a" * 1000}]
    ctx.estimate_tokens(messages)  # stashes _last_request_chars = 1000
    ctx.update_from_response({"input_tokens": 500})
    # observed_factor = 1000 / 500 = 2.0
    assert ctx._calibration_factor == pytest.approx(2.0)


def test_calibration_ema(ctx: ContextManager):
    """Subsequent samples use EMA with alpha=0.3."""
    messages = [{"role": "user", "content": "a" * 1000}]

    # First sample: factor -> 2.0
    ctx.estimate_tokens(messages)
    ctx.update_from_response({"input_tokens": 500})
    assert ctx._calibration_factor == pytest.approx(2.0)

    # Second sample: observed = 1000/400 = 2.5, EMA = 0.3*2.5 + 0.7*2.0 = 2.15
    ctx.estimate_tokens(messages)
    ctx.update_from_response({"input_tokens": 400})
    assert ctx._calibration_factor == pytest.approx(2.15)


def test_calibration_skips_zero_tokens(ctx: ContextManager):
    """Zero or negative input_tokens should not change the factor."""
    original = ctx._calibration_factor
    ctx.estimate_tokens([{"role": "user", "content": "hello"}])
    ctx.update_from_response({"input_tokens": 0})
    assert ctx._calibration_factor == original

    ctx.update_from_response({"input_tokens": -1})
    assert ctx._calibration_factor == original


def test_calibration_skips_zero_chars(ctx: ContextManager):
    """If _last_request_chars is 0, skip calibration."""
    original = ctx._calibration_factor
    ctx._last_request_chars = 0
    ctx.update_from_response({"input_tokens": 100})
    assert ctx._calibration_factor == original


def test_calibration_improves_estimation(ctx: ContextManager):
    """After calibration, estimates should be closer to real values."""
    messages = [{"role": "user", "content": "x" * 2000}]

    # Before calibration: 2000 / 2.5 = 800
    est_before = ctx.estimate_tokens(messages)

    # Simulate API says it was actually 1000 tokens -> factor = 2.0
    ctx.update_from_response({"input_tokens": 1000})

    # After calibration: 2000 / 2.0 = 1000
    est_after = ctx.estimate_tokens(messages)
    assert abs(est_after - 1000) < abs(est_before - 1000)


# ── get_snapshot ─────────────────────────────────────────────


def test_get_snapshot(ctx: ContextManager):
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    snap = ctx.get_snapshot(messages, 200000)
    assert snap.estimated_tokens > 0
    assert snap.context_window == 200000
    assert 0 < snap.usage_ratio < 1
    assert snap.message_count == 2


def test_get_snapshot_with_tools(ctx: ContextManager):
    messages = [{"role": "user", "content": "test"}]
    tools = [{"type": "function", "function": {"name": "bash", "description": "Run", "parameters": {}}}]
    snap_no_tools = ctx.get_snapshot(messages, 100000)
    snap_with_tools = ctx.get_snapshot(messages, 100000, tools)
    assert snap_with_tools.estimated_tokens > snap_no_tools.estimated_tokens


def test_get_snapshot_custom_context_window(ctx: ContextManager):
    messages = [{"role": "user", "content": "a" * 500}]
    snap = ctx.get_snapshot(messages, 128000)
    assert snap.context_window == 128000
    snap2 = ctx.get_snapshot(messages, 64000)
    assert snap2.context_window == 64000
    # Same tokens, different window -> different ratio
    assert snap2.usage_ratio > snap.usage_ratio


# ── Real API compression ─────────────────────────────────────


@pytest.mark.asyncio
async def test_compress_real_api(ctx: ContextManager):
    """Test compression with a real API call to doubao-seed."""
    provider = get_provider("volcengine")
    messages = [
        {"role": "system", "content": "你是一个助手。"},
        {"role": "user", "content": "今天天气怎么样？"},
        {"role": "assistant", "content": "今天天气晴朗，温度适宜。"},
        {"role": "user", "content": "明天呢？"},
        {"role": "assistant", "content": "明天可能会下雨，建议带伞。"},
    ]
    summary = await ctx.compress(messages, provider)
    assert isinstance(summary, str)
    assert len(summary) > 0
