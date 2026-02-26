"""GetSkillTool - read a skill's SKILL.md."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class GetSkillTool(Tool):
    name = "get_skill"
    description = "Read the full SKILL.md content for a named skill."
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "Name of the skill to read"},
        },
        "required": ["skill_name"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        skill_name = input["skill_name"]
        skill_md = cwd / ".agents" / "skills" / skill_name / "SKILL.md"

        if not skill_md.is_file():
            return ToolResult(content=f"Skill not found: {skill_name}", is_error=True)

        try:
            content = skill_md.read_text(encoding="utf-8")
            return ToolResult(content=content)
        except Exception as e:
            return ToolResult(content=f"Error reading skill: {e}", is_error=True)
