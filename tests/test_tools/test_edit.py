"""Tests for EditTool."""

import pytest
from agentpod.tools.edit import EditTool


@pytest.fixture
def tool():
    return EditTool()


async def test_edit_replace(tool, tmp_cwd):
    result = await tool.execute(
        {"file_path": "test.txt", "old_string": "line 3", "new_string": "LINE THREE"},
        tmp_cwd,
    )
    assert not result.is_error
    text = (tmp_cwd / "test.txt").read_text()
    assert "LINE THREE" in text
    assert "line 3" not in text


async def test_edit_old_string_not_found(tool, tmp_cwd):
    result = await tool.execute(
        {"file_path": "test.txt", "old_string": "nonexistent", "new_string": "x"},
        tmp_cwd,
    )
    assert result.is_error
    assert "not found" in result.content.lower()


async def test_edit_replace_all(tool, tmp_cwd):
    # Write a file with repeated content
    (tmp_cwd / "repeat.txt").write_text("aaa bbb aaa bbb aaa")
    result = await tool.execute(
        {
            "file_path": "repeat.txt",
            "old_string": "aaa",
            "new_string": "ccc",
            "replace_all": True,
        },
        tmp_cwd,
    )
    assert not result.is_error
    text = (tmp_cwd / "repeat.txt").read_text()
    assert text == "ccc bbb ccc bbb ccc"


async def test_edit_ambiguous_without_replace_all(tool, tmp_cwd):
    (tmp_cwd / "repeat.txt").write_text("aaa bbb aaa")
    result = await tool.execute(
        {"file_path": "repeat.txt", "old_string": "aaa", "new_string": "ccc"},
        tmp_cwd,
    )
    assert result.is_error
    assert "2 times" in result.content or "replace_all" in result.content
