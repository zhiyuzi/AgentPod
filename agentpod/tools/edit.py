"""EditTool - find and replace text in files."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class EditTool(Tool):
    name = "edit"
    description = "Replace occurrences of old_string with new_string in a file."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit"},
            "old_string": {"type": "string", "description": "Text to find"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        try:
            resolved = safe_resolve(input["file_path"], cwd)
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)

        if not resolved.is_file():
            return ToolResult(content=f"File not found: {input['file_path']}", is_error=True)

        try:
            text = resolved.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        old_string = input["old_string"]
        new_string = input["new_string"]
        replace_all = input.get("replace_all", False)

        count = text.count(old_string)
        if count == 0:
            return ToolResult(content=f"old_string not found in {input['file_path']}", is_error=True)

        if not replace_all and count > 1:
            return ToolResult(
                content=f"old_string found {count} times in {input['file_path']}. Use replace_all=true or provide a more unique string.",
                is_error=True,
            )

        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)

        resolved.write_text(new_text, encoding="utf-8")
        replacements = count if replace_all else 1
        return ToolResult(content=f"Replaced {replacements} occurrence(s) in {input['file_path']}")
