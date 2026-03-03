"""GetSkillTool - read a skill's SKILL.md body (instructions)."""

from __future__ import annotations

from pathlib import Path

from agentpod.skills import load_frontmatter_and_body
from agentpod.tools.base import Tool, ToolResult, safe_resolve


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

    def __init__(self, shared_dir: Path | None = None):
        self.shared_dir = shared_dir

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        skill_name = input["skill_name"]

        # Try user CWD first
        try:
            skill_dir = safe_resolve(
                str(Path(".agents") / "skills" / skill_name), cwd
            )
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)

        skill_md = skill_dir / "SKILL.md"
        is_shared = False

        # Fallback to shared dir if not found in user CWD
        if not skill_md.is_file() and self.shared_dir:
            shared_skill_dir = self.shared_dir / ".agents" / "skills" / skill_name
            shared_skill_md = shared_skill_dir / "SKILL.md"
            if shared_skill_md.is_file():
                skill_dir = shared_skill_dir
                skill_md = shared_skill_md
                is_shared = True

        if not skill_md.is_file():
            return ToolResult(content=f"Skill not found: {skill_name}", is_error=True)

        try:
            meta, body = load_frontmatter_and_body(skill_md)

            # 校验 frontmatter
            name = meta.get("name")
            if not name:
                return ToolResult(
                    content=f"Skill '{skill_name}': missing required 'name' in frontmatter",
                    is_error=True,
                )
            name = str(name)
            if name != skill_dir.name:
                return ToolResult(
                    content=f"Skill '{skill_name}': frontmatter name '{name}' does not match directory name",
                    is_error=True,
                )
            if not meta.get("description"):
                return ToolResult(
                    content=f"Skill '{skill_name}': missing required 'description' in frontmatter",
                    is_error=True,
                )

            # 返回 body 部分 + skill 目录路径（相对于 CWD）
            # shared skill 使用虚拟路径（bind-mount 到 .agents/skills/{name}）
            if is_shared:
                rel_dir = str(Path(".agents") / "skills" / name)
            else:
                rel_dir = str(skill_dir.relative_to(cwd))
            header = f"# Skill: {name}\n\nSkill directory: `{rel_dir}`\n"
            if body.strip():
                result = header + body
            else:
                result = header + "(no instructions)\n"
            return ToolResult(content=result)
        except Exception as e:
            return ToolResult(content=f"Error reading skill: {e}", is_error=True)
