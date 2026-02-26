"""Tests for gateway/cwd.py – CWD file management routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_get_directory(client, test_user, tmp_cwd):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = await client.get("/v1/cwd/subdir", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "directory"
    names = [e["name"] for e in data["entries"]]
    assert "nested.txt" in names


@pytest.mark.asyncio
async def test_get_file(client, test_user, tmp_cwd):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = await client.get("/v1/cwd/hello.txt", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "file"
    assert data["content"] == "hello world"


@pytest.mark.asyncio
async def test_put_system_protected(client, test_user):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = await client.put(
        "/v1/cwd/.agents/secret",
        headers=headers,
        content=json.dumps({"content": "bad"}),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_non_writable(client, test_user):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    # "random/file" is not in writable_paths ["docs/", "src/"]
    resp = await client.put(
        "/v1/cwd/random/file.txt",
        headers=headers,
        content=json.dumps({"content": "data"}),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_writable(client, test_user, tmp_cwd):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = await client.put(
        "/v1/cwd/docs/notes.txt",
        headers=headers,
        content=json.dumps({"content": "my notes"}),
    )
    assert resp.status_code == 200
    assert (tmp_cwd / "docs" / "notes.txt").read_text(encoding="utf-8") == "my notes"


@pytest.mark.asyncio
async def test_delete_file(client, test_user, tmp_cwd):
    _, api_key = test_user
    headers = {"Authorization": f"Bearer {api_key}"}

    # Create a file in a writable path first
    target = tmp_cwd / "docs" / "to_delete.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("bye", encoding="utf-8")

    resp = await client.request(
        "DELETE", "/v1/cwd/docs/to_delete.txt", headers=headers
    )
    assert resp.status_code == 200
    assert not target.exists()


@pytest.mark.asyncio
async def test_path_traversal(test_user, tmp_cwd):
    """safe_resolve rejects paths that escape the CWD boundary."""
    from agentpod.tools.base import safe_resolve

    with pytest.raises(PermissionError):
        safe_resolve("../../etc/passwd", tmp_cwd)
