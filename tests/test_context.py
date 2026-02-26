"""Tests for ContextManager."""

import pytest
import dotenv

dotenv.load_dotenv()

from agentpod.runtime.context import ContextManager
from agentpod.providers import get_provider


@pytest.fixture
def ctx() -> ContextManager:
    return ContextManager()


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
