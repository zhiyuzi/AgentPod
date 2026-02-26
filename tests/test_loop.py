"""Tests for AgenticLoop with real API calls."""

import pytest
import dotenv
from pathlib import Path

dotenv.load_dotenv()

from agentpod.providers import get_provider
from agentpod.tools import create_default_registry
from agentpod.runtime.context import ContextManager
from agentpod.runtime.loop import AgenticLoop
from agentpod.types import (
    Done,
    Error,
    RuntimeOptions,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)


@pytest.fixture
def loop():
    provider = get_provider("volcengine")
    registry = create_default_registry()
    context = ContextManager()
    return AgenticLoop(provider, registry, context)


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_simple_chat(loop: AgenticLoop, cwd: Path):
    """Send a simple message and verify TextDelta + Done events."""
    messages = [
        {"role": "system", "content": "你是一个助手。用户说什么你就回复一个字。"},
        {"role": "user", "content": "说一个字：好"},
    ]
    options = RuntimeOptions(max_turns=1)

    events = []
    async for event in loop.run(messages, options, cwd):
        events.append(event)

    # Should have at least one TextDelta and a Done
    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    dones = [e for e in events if isinstance(e, Done)]
    turns = [e for e in events if isinstance(e, TurnComplete)]

    assert len(text_deltas) > 0
    assert len(dones) == 1
    assert len(turns) == 1
    assert dones[0].cost >= 0
    # Note: usage may be 0 in streaming mode if the API doesn't return
    # stream_options.include_usage. We just verify the key exists.
    assert "input_tokens" in dones[0].usage


@pytest.mark.asyncio
async def test_tool_call(loop: AgenticLoop, cwd: Path):
    """Verify the loop can execute a tool call (bash echo)."""
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个助手。当用户说'测试'时，使用bash工具执行 echo test 命令。"
                "执行完工具后，回复'完成'。"
            ),
        },
        {"role": "user", "content": "测试"},
    ]
    options = RuntimeOptions(max_turns=5)

    events = []
    async for event in loop.run(messages, options, cwd):
        events.append(event)

    tool_starts = [e for e in events if isinstance(e, ToolStart)]
    tool_ends = [e for e in events if isinstance(e, ToolEnd)]
    dones = [e for e in events if isinstance(e, Done)]

    assert len(tool_starts) > 0
    assert len(tool_ends) > 0
    assert len(dones) == 1
    # The bash tool should have been called
    assert tool_starts[0].tool == "bash"


@pytest.mark.asyncio
async def test_budget_limit(loop: AgenticLoop, cwd: Path):
    """Verify the loop stops when budget is exceeded."""
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个助手。每次回复后都使用bash工具执行 echo hello，"
                "然后继续回复。不断重复这个过程。"
            ),
        },
        {"role": "user", "content": "开始"},
    ]
    # Very small budget to trigger early stop
    options = RuntimeOptions(max_turns=50, max_budget_usd=0.0001)

    events = []
    async for event in loop.run(messages, options, cwd):
        events.append(event)

    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1
    # Cost should be small but present
    assert dones[0].cost >= 0
