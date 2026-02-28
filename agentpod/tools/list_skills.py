"""ListSkillsTool - discover available skills."""

from __future__ import annotations

from pathlib import Path

from agentpod.skills import discover_skills
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
        skills = discover_skills(skills_dir)

        if not skills:
            if not skills_dir.is_dir():
                return ToolResult(content="No skills directory found.")
            return ToolResult(content="No skills found.")

        entries = [f"- {s['name']}: {s['description']}" for s in skills]
        return ToolResult(content="\n".join(entries))
