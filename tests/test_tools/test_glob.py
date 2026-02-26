"""Tests for GlobTool."""

import pytest
from agentpod.tools.glob_tool import GlobTool


@pytest.fixture
def tool():
    return GlobTool()


async def test_glob_md_files(tool, tmp_cwd):
    result = await tool.execute({"pattern": "*.md"}, tmp_cwd)
    assert not result.is_error
    assert "AGENTS.md" in result.content


async def test_glob_recursive(tool, tmp_cwd):
    # Create a .py file to match
    (tmp_cwd / "src").mkdir()
    (tmp_cwd / "src" / "app.py").write_text("pass")
    result = await tool.execute({"pattern": "**/*.py"}, tmp_cwd)
    assert not result.is_error
    assert "app.py" in result.content


async def test_glob_no_match(tool, tmp_cwd):
    result = await tool.execute({"pattern": "*.xyz"}, tmp_cwd)
    assert "No files matched" in result.content
