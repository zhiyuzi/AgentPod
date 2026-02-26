"""Tests for ListSkillsTool and GetSkillTool."""

import pytest
from agentpod.tools.list_skills import ListSkillsTool
from agentpod.tools.get_skill import GetSkillTool


@pytest.fixture
def list_tool():
    return ListSkillsTool()


@pytest.fixture
def get_tool():
    return GetSkillTool()


async def test_list_skills(list_tool, tmp_cwd):
    result = await list_tool.execute({}, tmp_cwd)
    assert not result.is_error
    assert "hello" in result.content
    assert "Hello skill" in result.content


async def test_list_skills_no_dir(list_tool, tmp_path):
    result = await list_tool.execute({}, tmp_path)
    assert "No skills directory" in result.content


async def test_get_skill(get_tool, tmp_cwd):
    result = await get_tool.execute({"skill_name": "hello"}, tmp_cwd)
    assert not result.is_error
    assert "Hello skill - a test skill" in result.content
    assert "Details here." in result.content


async def test_get_skill_not_found(get_tool, tmp_cwd):
    result = await get_tool.execute({"skill_name": "nonexistent"}, tmp_cwd)
    assert result.is_error
    assert "not found" in result.content.lower()
