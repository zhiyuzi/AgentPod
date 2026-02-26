"""Tests for WriteTool."""

import pytest
from agentpod.tools.write import WriteTool


@pytest.fixture
def tool():
    return WriteTool()


async def test_write_new_file(tool, tmp_cwd):
    result = await tool.execute({"file_path": "new_file.txt", "content": "hello world"}, tmp_cwd)
    assert not result.is_error
    written = (tmp_cwd / "new_file.txt").read_text()
    assert written == "hello world"


async def test_write_creates_parent_dirs(tool, tmp_cwd):
    result = await tool.execute(
        {"file_path": "subdir/deep/file.txt", "content": "nested"},
        tmp_cwd,
    )
    assert not result.is_error
    written = (tmp_cwd / "subdir" / "deep" / "file.txt").read_text()
    assert written == "nested"


async def test_write_overwrite_existing(tool, tmp_cwd):
    result = await tool.execute({"file_path": "test.txt", "content": "overwritten"}, tmp_cwd)
    assert not result.is_error
    written = (tmp_cwd / "test.txt").read_text()
    assert written == "overwritten"


async def test_write_path_traversal(tool, tmp_cwd):
    result = await tool.execute(
        {"file_path": "../../evil.txt", "content": "bad"},
        tmp_cwd,
    )
    assert result.is_error
    assert "outside" in result.content.lower() or "permission" in result.content.lower()
