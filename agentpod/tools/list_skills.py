"""ListSkillsTool - discover available skills."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class ListSkillsTool(Tool):
    name = "list_skills"
    description = "List all available skills in the project."
    input_schema = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        skills_dir = cwd / ".agents" / "skills"
        if not skills_dir.is_dir():
            return ToolResult(content="No skills directory found.")

        entries: list[str] = []
        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            first_line = skill_md.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            entries.append(f"- {child.name}: {first_line}")

        if not entries:
            return ToolResult(content="No skills found.")

        return ToolResult(content="\n".join(entries))
