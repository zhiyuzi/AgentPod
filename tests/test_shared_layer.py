"""Tests for shared layer (Phase 1): discover_skills multi-dir, PromptManager fallback,
ListSkillsTool and GetSkillTool shared support."""

from __future__ import annotations

import pytest
from pathlib import Path

from agentpod.skills import discover_skills
from agentpod.runtime.prompt import PromptManager
from agentpod.tools.list_skills import ListSkillsTool
from agentpod.tools.get_skill import GetSkillTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_skill(base_dir: Path, name: str, description: str, body: str = "## Instructions\n\nDo stuff.\n") -> Path:
    """Create a valid skill directory under base_dir/.agents/skills/{name}/SKILL.md"""
    skill_dir = base_dir / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return skill_dir


# ---------------------------------------------------------------------------
# 1. discover_skills multi-dir merge: user overrides shared
# ---------------------------------------------------------------------------

def test_discover_skills_multi_dir_user_overrides_shared(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    make_skill(shared, "alpha", "Shared alpha description")
    make_skill(shared, "beta", "Shared beta description")
    make_skill(user, "alpha", "User alpha description")  # overrides shared

    shared_skills_dir = shared / ".agents" / "skills"
    user_skills_dir = user / ".agents" / "skills"

    skills = discover_skills(shared_skills_dir, user_skills_dir)
    by_name = {s["name"]: s for s in skills}

    assert "alpha" in by_name
    assert "beta" in by_name
    # user alpha overrides shared alpha
    assert by_name["alpha"]["description"] == "User alpha description"
    assert by_name["beta"]["description"] == "Shared beta description"


# ---------------------------------------------------------------------------
# 2. discover_skills source field
# ---------------------------------------------------------------------------

def test_discover_skills_source_field(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    make_skill(shared, "shared_only", "Only in shared")
    make_skill(user, "user_only", "Only in user")
    make_skill(shared, "both", "Shared both")
    make_skill(user, "both", "User both")

    shared_skills_dir = shared / ".agents" / "skills"
    user_skills_dir = user / ".agents" / "skills"

    skills = discover_skills(shared_skills_dir, user_skills_dir)
    by_name = {s["name"]: s for s in skills}

    assert by_name["shared_only"]["source"] == "shared"
    assert by_name["user_only"]["source"] == "user"
    # "both" is overridden by user, so source = "user"
    assert by_name["both"]["source"] == "user"


# ---------------------------------------------------------------------------
# 3. discover_skills single dir backward compat (source = "user")
# ---------------------------------------------------------------------------

def test_discover_skills_single_dir_backward_compat(tmp_path):
    make_skill(tmp_path, "myskill", "A skill")
    skills_dir = tmp_path / ".agents" / "skills"
    skills = discover_skills(skills_dir)
    assert len(skills) == 1
    assert skills[0]["name"] == "myskill"
    assert skills[0]["source"] == "user"


# ---------------------------------------------------------------------------
# 4. PromptManager AGENTS.md fallback: user has it -> use user
# ---------------------------------------------------------------------------

def test_prompt_manager_agents_md_user_wins(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()
    (shared / "AGENTS.md").write_text("# Shared AGENTS\n", encoding="utf-8")
    (user / "AGENTS.md").write_text("# User AGENTS\n", encoding="utf-8")

    pm = PromptManager(cwd=user, shared_dir=shared)
    content = pm.load()
    assert "# User AGENTS" in content
    assert "# Shared AGENTS" not in content


# ---------------------------------------------------------------------------
# 5. PromptManager AGENTS.md fallback: user missing -> use shared
# ---------------------------------------------------------------------------

def test_prompt_manager_agents_md_fallback_to_shared(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()
    (shared / "AGENTS.md").write_text("# Shared AGENTS\n", encoding="utf-8")
    # no AGENTS.md in user

    pm = PromptManager(cwd=user, shared_dir=shared)
    content = pm.load()
    assert "# Shared AGENTS" in content


# ---------------------------------------------------------------------------
# 6. PromptManager AGENTS.md: both missing -> FileNotFoundError
# ---------------------------------------------------------------------------

def test_prompt_manager_agents_md_both_missing(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()

    pm = PromptManager(cwd=user, shared_dir=shared)
    with pytest.raises(FileNotFoundError):
        pm.load()


# ---------------------------------------------------------------------------
# 7. PromptManager skills two-layer merge
# ---------------------------------------------------------------------------

def test_prompt_manager_skills_two_layer_merge(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()
    (user / "AGENTS.md").write_text("# User\n", encoding="utf-8")

    make_skill(shared, "shared_skill", "A shared skill")
    make_skill(user, "user_skill", "A user skill")

    pm = PromptManager(cwd=user, shared_dir=shared)
    content = pm.load()

    assert "shared_skill" in content
    assert "user_skill" in content
    assert "（shared）" in content


# ---------------------------------------------------------------------------
# 8. ListSkillsTool shared + user merge, output contains shared marker
# ---------------------------------------------------------------------------

async def test_list_skills_tool_shared_and_user(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()

    make_skill(shared, "shared_skill", "A shared skill")
    make_skill(user, "user_skill", "A user skill")

    tool = ListSkillsTool(shared_dir=shared)
    result = await tool.execute({}, user)

    assert not result.is_error
    assert "shared_skill" in result.content
    assert "user_skill" in result.content
    assert "（shared）" in result.content
    # user_skill should NOT have shared marker
    lines = result.content.splitlines()
    user_line = next(l for l in lines if "user_skill" in l)
    assert "（shared）" not in user_line


# ---------------------------------------------------------------------------
# 9. GetSkillTool user priority, fallback to shared
# ---------------------------------------------------------------------------

async def test_get_skill_tool_user_priority(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()

    make_skill(shared, "myskill", "Shared version", body="## Shared instructions\n")
    make_skill(user, "myskill", "User version", body="## User instructions\n")

    tool = GetSkillTool(shared_dir=shared)
    result = await tool.execute({"skill_name": "myskill"}, user)

    assert not result.is_error
    assert "User instructions" in result.content
    assert "Shared instructions" not in result.content


async def test_get_skill_tool_fallback_to_shared(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()

    make_skill(shared, "sharedonly", "Only in shared", body="## Shared only instructions\n")

    tool = GetSkillTool(shared_dir=shared)
    result = await tool.execute({"skill_name": "sharedonly"}, user)

    assert not result.is_error
    assert "Shared only instructions" in result.content


# ---------------------------------------------------------------------------
# 10. GetSkillTool shared skill rel_dir uses virtual path
# ---------------------------------------------------------------------------

async def test_get_skill_tool_shared_rel_dir(tmp_path):
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    shared.mkdir()
    user.mkdir()

    make_skill(shared, "sharedskill", "A shared skill", body="## Instructions\n")

    tool = GetSkillTool(shared_dir=shared)
    result = await tool.execute({"skill_name": "sharedskill"}, user)

    assert not result.is_error
    # rel_dir should be the virtual bind-mount path, not the real shared path
    # Use os.sep-agnostic check: look for the path components
    assert "sharedskill" in result.content
    assert ".agents" in result.content
    assert "skills" in result.content
    # Should NOT contain the real shared path
    assert str(shared) not in result.content
