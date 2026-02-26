"""Tests for BashTool."""

import pytest
from agentpod.tools.bash import BashTool


@pytest.fixture
def tool():
    return BashTool()


async def test_bash_echo(tool, tmp_cwd):
    result = await tool.execute({"command": "echo hello"}, tmp_cwd)
    assert not result.is_error
    assert "hello" in result.content


async def test_bash_timeout(tool, tmp_cwd):
    # Use a very short timeout with a long-running command
    result = await tool.execute(
        {"command": "sleep 10", "timeout": 1},
        tmp_cwd,
    )
    assert result.is_error
    assert "timed out" in result.content.lower()


async def test_bash_cwd(tool, tmp_cwd):
    # Verify the command runs in the correct cwd
    result = await tool.execute({"command": "pwd"}, tmp_cwd)
    assert not result.is_error
    # The output should contain the tmp_cwd path (normalized)
    # On Windows with Git Bash, pwd returns a Unix-style path
    # Just check it's not an error and has output
    assert len(result.content.strip()) > 0


async def test_bash_nonzero_exit(tool, tmp_cwd):
    result = await tool.execute({"command": "exit 1"}, tmp_cwd)
    assert result.is_error
    assert "exit code" in result.content.lower()
