"""Integration tests for AgentRuntime with real API calls."""

import shutil
import pytest
import dotenv
from pathlib import Path

dotenv.load_dotenv()

from agentpod.runtime.runtime import AgentRuntime
from agentpod.types import Done, RuntimeOptions, TextDelta


@pytest.fixture
def runtime_cwd(tmp_path: Path) -> Path:
    """Copy example_cwd to a temp directory for isolation."""
    src = Path(__file__).resolve().parent.parent / "example_cwd"
    dst = tmp_path / "cwd"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def runtime(runtime_cwd: Path) -> AgentRuntime:
    return AgentRuntime(runtime_cwd)


@pytest.mark.asyncio
async def test_simple_query(runtime: AgentRuntime):
    """Create a session, send a query, verify TextDelta + Done."""
    sid = await runtime.create_session()

    events = []
    async for event in runtime.query("你好", session_id=sid):
        events.append(event)

    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    dones = [e for e in events if isinstance(e, Done)]

    assert len(text_deltas) > 0
    assert len(dones) == 1

    # Verify the assistant message was persisted
    messages = runtime.session_mgr.load(sid)
    assert len(messages) >= 2  # user + assistant
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"


@pytest.mark.asyncio
async def test_session_continuity(runtime: AgentRuntime):
    """Two queries on the same session should maintain context."""
    sid = await runtime.create_session()

    # First query
    async for _ in runtime.query("我的名字是小明", session_id=sid):
        pass

    # Second query on same session
    events = []
    async for event in runtime.query("我叫什么名字？", session_id=sid):
        events.append(event)

    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    full_text = "".join(e.content for e in text_deltas)
    # The model should remember the name from the first query
    assert "小明" in full_text

    # Session should have 4 messages: user1, assistant1, user2, assistant2
    messages = runtime.session_mgr.load(sid)
    assert len(messages) >= 4


@pytest.mark.asyncio
async def test_list_sessions(runtime: AgentRuntime):
    """Verify created sessions appear in the list."""
    sid1 = await runtime.create_session()
    sid2 = await runtime.create_session()

    sessions = await runtime.list_sessions()
    session_ids = [s.session_id for s in sessions]
    assert sid1 in session_ids
    assert sid2 in session_ids
