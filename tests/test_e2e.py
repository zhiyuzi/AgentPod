"""End-to-end smoke tests using REAL API calls to doubao-seed-1-8-251228."""

import json
import os
import shutil
from pathlib import Path

import httpx
import pytest

from agentpod.db import Database
from agentpod.gateway.admission import AdmissionController
from agentpod.gateway.app import app, _runtimes


@pytest.fixture
async def e2e_setup(tmp_path):
    """Set up a complete e2e environment."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "users").mkdir()

    # Create template from example_cwd
    template_dir = data_dir / "template"
    src = Path(__file__).parent.parent / "example_cwd"
    shutil.copytree(src, template_dir)

    # Set env so any config loading picks up the right data_dir
    old_data_dir = os.environ.get("AGENTPOD_DATA_DIR")
    os.environ["AGENTPOD_DATA_DIR"] = str(data_dir)

    # Init DB and wire it into app.state (ASGITransport does not trigger lifespan)
    db = Database(str(data_dir / "registry.db"))
    db.init_db()

    app.state.db = db
    app.state.config = type("C", (), {"data_dir": str(data_dir), "max_concurrent": 20})()
    app.state.admission = AdmissionController(20)

    # Create user CWD
    user_cwd = data_dir / "users" / "e2euser"
    shutil.copytree(template_dir, user_cwd)
    (user_cwd / "sessions").mkdir(exist_ok=True)

    api_key = db.create_user("e2euser", str(user_cwd))

    # Clear runtime cache
    _runtimes.clear()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=120.0,
    ) as client:
        yield {
            "client": client,
            "api_key": api_key,
            "data_dir": data_dir,
            "db": db,
            "user_cwd": user_cwd,
        }

    db.close()
    _runtimes.clear()

    # Restore env
    if old_data_dir is None:
        os.environ.pop("AGENTPOD_DATA_DIR", None)
    else:
        os.environ["AGENTPOD_DATA_DIR"] = old_data_dir


def _parse_sse_events(text: str) -> list[dict]:
    """Parse SSE text into a list of {type, data} dicts."""
    events = []
    event_type = ""
    for line in text.split("\n"):
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            events.append({"type": event_type, "data": data})
    return events


async def test_e2e_smoke(e2e_setup):
    """Full e2e: query -> SSE events -> sessions -> usage."""
    ctx = e2e_setup
    client = ctx["client"]
    api_key = ctx["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    # 1. Health check
    resp = await client.get("/v1/health")
    assert resp.status_code == 200

    # 2. Query
    resp = await client.post(
        "/v1/query",
        json={"content": "你好，请用一句话介绍你自己"},
        headers=headers,
    )
    assert resp.status_code == 200

    # Parse SSE events
    events = _parse_sse_events(resp.text)

    # Verify events
    event_types = [e["type"] for e in events]
    assert "text_delta" in event_types
    assert "done" in event_types

    # Verify done event has usage
    done_event = [e for e in events if e["type"] == "done"][0]
    assert done_event["data"]["usage"]["input_tokens"] > 0
    assert done_event["data"]["cost"] > 0

    # 3. List sessions
    resp = await client.get("/v1/sessions", headers=headers)
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) > 0

    # 4. Get session details
    session_id = sessions[0]["session_id"]
    resp = await client.get(f"/v1/sessions/{session_id}", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) > 0


async def test_e2e_tool_call(e2e_setup):
    """E2E: Agent uses bash tool."""
    ctx = e2e_setup
    client = ctx["client"]
    api_key = ctx["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    # Override AGENTS.md to instruct tool use
    agents_md = ctx["user_cwd"] / "AGENTS.md"
    agents_md.write_text(
        "你是一个测试助手。当用户说'执行测试'时，"
        "你必须使用bash工具执行命令 `echo agentpod_test_ok`，然后报告结果。",
        encoding="utf-8",
    )

    # Clear runtime cache so new AGENTS.md is loaded
    _runtimes.clear()

    resp = await client.post(
        "/v1/query",
        json={"content": "执行测试"},
        headers=headers,
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    event_types = [e["type"] for e in events]
    assert "tool_start" in event_types
    assert "tool_end" in event_types
    assert "done" in event_types

    # Verify tool execution
    tool_end = [e for e in events if e["type"] == "tool_end"][0]
    assert "agentpod_test_ok" in tool_end["data"]["result"]


async def test_e2e_cwd_file_management(e2e_setup):
    """E2E: CWD file management API."""
    ctx = e2e_setup
    client = ctx["client"]
    api_key = ctx["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    # Update user config to allow writing to reports/
    ctx["db"].update_config("e2euser", '{"writable_paths": ["reports/"]}')

    # Create reports directory
    reports_dir = ctx["user_cwd"] / "reports" / "2026-02"
    reports_dir.mkdir(parents=True)
    (reports_dir / "report.md").write_text("# February Report\nTest content.")

    # GET directory
    resp = await client.get("/v1/cwd/reports/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "directory"

    # GET file
    resp = await client.get(
        "/v1/cwd/reports/2026-02/report.md", headers=headers
    )
    assert resp.status_code == 200
    assert "February Report" in resp.json()["content"]

    # PUT file (writable)
    resp = await client.put(
        "/v1/cwd/reports/2026-02/report.md",
        json={"content": "# Updated Report"},
        headers=headers,
    )
    assert resp.status_code == 200

    # PUT system protected -> 403
    resp = await client.put(
        "/v1/cwd/AGENTS.md",
        json={"content": "hacked"},
        headers=headers,
    )
    assert resp.status_code == 403

    # DELETE file
    resp = await client.delete(
        "/v1/cwd/reports/2026-02/report.md", headers=headers
    )
    assert resp.status_code == 200


async def test_e2e_multi_turn(e2e_setup):
    """E2E: Multi-turn conversation with context."""
    ctx = e2e_setup
    client = ctx["client"]
    api_key = ctx["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    # First query - establish context
    resp = await client.post(
        "/v1/query",
        json={"content": "请记住这个数字：42"},
        headers=headers,
    )
    assert resp.status_code == 200

    # Get session_id from sessions list
    resp = await client.get("/v1/sessions", headers=headers)
    sessions = resp.json()["sessions"]
    session_id = sessions[0]["session_id"]

    # Second query - reference previous context
    resp = await client.post(
        "/v1/query",
        json={
            "content": "我刚才让你记住的数字是什么？",
            "session_id": session_id,
        },
        headers=headers,
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)

    # Verify the model remembers 42
    text_content = "".join(
        e["data"]["content"] for e in events if e["type"] == "text_delta"
    )
    assert "42" in text_content
