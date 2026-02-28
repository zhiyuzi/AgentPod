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
    assert "A test skill that outputs hello." in result.content


async def test_list_skills_no_dir(list_tool, tmp_path):
    result = await list_tool.execute({}, tmp_path)
    assert "No skills directory" in result.content


async def test_list_skills_skips_missing_name(list_tool, tmp_path):
    """Skill without 'name' in frontmatter should be skipped."""
    skills_dir = tmp_path / ".agents" / "skills" / "bad"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\ndescription: Missing name field\n---\n\nBody.\n"
    )
    result = await list_tool.execute({}, tmp_path)
    assert "No skills found" in result.content


async def test_list_skills_skips_name_mismatch(list_tool, tmp_path):
    """Skill with name != directory name should be skipped."""
    skills_dir = tmp_path / ".agents" / "skills" / "myskill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: Name mismatch\n---\n\nBody.\n"
    )
    result = await list_tool.execute({}, tmp_path)
    assert "No skills found" in result.content


async def test_list_skills_skips_missing_description(list_tool, tmp_path):
    """Skill without 'description' in frontmatter should be skipped."""
    skills_dir = tmp_path / ".agents" / "skills" / "nodesc"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: nodesc\n---\n\nBody.\n"
    )
    result = await list_tool.execute({}, tmp_path)
    assert "No skills found" in result.content


async def test_list_skills_skips_no_frontmatter(list_tool, tmp_path):
    """Skill without frontmatter should be skipped (strict mode)."""
    skills_dir = tmp_path / ".agents" / "skills" / "legacy"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Legacy Skill\n\nDoes legacy things.\n")
    result = await list_tool.execute({}, tmp_path)
    assert "No skills found" in result.content


async def test_get_skill(get_tool, tmp_cwd):
    result = await get_tool.execute({"skill_name": "hello"}, tmp_cwd)
    assert not result.is_error
    # Should return body (instructions), not frontmatter
    assert "scripts/run.sh" in result.content
    # Should NOT contain raw frontmatter delimiters
    assert "---" not in result.content
    # Should contain skill name header
    assert "hello" in result.content


async def test_get_skill_not_found(get_tool, tmp_cwd):
    result = await get_tool.execute({"skill_name": "nonexistent"}, tmp_cwd)
    assert result.is_error
    assert "not found" in result.content.lower()


async def test_get_skill_missing_name(get_tool, tmp_path):
    """get_skill should reject skill with missing name."""
    skills_dir = tmp_path / ".agents" / "skills" / "bad"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\ndescription: No name\n---\n\nBody.\n"
    )
    result = await get_tool.execute({"skill_name": "bad"}, tmp_path)
    assert result.is_error
    assert "missing" in result.content.lower()


async def test_get_skill_name_mismatch(get_tool, tmp_path):
    """get_skill should reject skill with name != directory name."""
    skills_dir = tmp_path / ".agents" / "skills" / "myskill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: other\ndescription: Mismatch\n---\n\nBody.\n"
    )
    result = await get_tool.execute({"skill_name": "myskill"}, tmp_path)
    assert result.is_error
    assert "does not match" in result.content.lower()
