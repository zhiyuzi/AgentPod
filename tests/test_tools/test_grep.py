"""Tests for GrepTool."""

import pytest
from agentpod.tools.grep import GrepTool


@pytest.fixture
def tool():
    return GrepTool()


async def test_grep_regex_search(tool, tmp_cwd):
    result = await tool.execute({"pattern": r"line \d+"}, tmp_cwd)
    assert not result.is_error
    assert "line 1" in result.content
    assert "test.txt" in result.content


async def test_grep_context_lines(tool, tmp_cwd):
    result = await tool.execute(
        {"pattern": "line 3", "path": "test.txt", "context": 1},
        tmp_cwd,
    )
    assert not result.is_error
    # Should include context lines (line 2 and line 4)
    assert "line 2" in result.content
    assert "line 4" in result.content


async def test_grep_no_match(tool, tmp_cwd):
    result = await tool.execute({"pattern": "zzz_no_match"}, tmp_cwd)
    assert "No matches" in result.content


async def test_grep_include_filter(tool, tmp_cwd):
    result = await tool.execute({"pattern": "line", "include": "*.txt"}, tmp_cwd)
    assert not result.is_error
    assert "test.txt" in result.content
