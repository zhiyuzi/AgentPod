"""Tests for ReadTool."""

import pytest
from agentpod.tools.read import ReadTool


@pytest.fixture
def tool():
    return ReadTool()


async def test_read_existing_file(tool, tmp_cwd):
    result = await tool.execute({"file_path": "test.txt"}, tmp_cwd)
    assert not result.is_error
    assert "line 1" in result.content
    assert "line 5" in result.content
    # Verify line numbers are present
    assert "1\t" in result.content


async def test_read_with_offset_and_limit(tool, tmp_cwd):
    result = await tool.execute({"file_path": "test.txt", "offset": 2, "limit": 2}, tmp_cwd)
    assert not result.is_error
    assert "line 2" in result.content
    assert "line 3" in result.content
    assert "line 1" not in result.content
    assert "line 4" not in result.content


async def test_read_nonexistent_file(tool, tmp_cwd):
    result = await tool.execute({"file_path": "does_not_exist.txt"}, tmp_cwd)
    assert result.is_error
    assert "not found" in result.content.lower()


async def test_read_path_traversal(tool, tmp_cwd):
    result = await tool.execute({"file_path": "../../etc/passwd"}, tmp_cwd)
    assert result.is_error
    assert "outside" in result.content.lower() or "permission" in result.content.lower()
