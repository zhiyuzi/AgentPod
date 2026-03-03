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

    def __init__(self, shared_dir: Path | None = None):
        self.shared_dir = shared_dir

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        user_skills_dir = cwd / ".agents" / "skills"
        dirs = []
        if self.shared_dir:
            dirs.append(self.shared_dir / ".agents" / "skills")
        dirs.append(user_skills_dir)

        skills = discover_skills(*dirs)

        if not skills:
            if not user_skills_dir.is_dir() and not self.shared_dir:
                return ToolResult(content="No skills directory found.")
            return ToolResult(content="No skills found.")

        entries = []
        for s in skills:
            suffix = "（shared）" if s.get("source") == "shared" else ""
            entries.append(f"- {s['name']}: {s['description']}{suffix}")
        return ToolResult(content="\n".join(entries))
