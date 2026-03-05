"""Edge Agent tools — local capabilities exposed to the cloud Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EdgeTool:
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def execute(self, input: dict) -> str:
        raise NotImplementedError


class CreateFileTool(EdgeTool):
    """Create a file on the local machine."""

    def __init__(self):
        super().__init__(
            name="create_file",
            description="在用户本地创建文件",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        )

    def execute(self, input: dict) -> str:
        path = Path(input["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(input["content"], encoding="utf-8")
        return f"Created {path}"


# All available tools
TOOLS: list[EdgeTool] = [CreateFileTool()]
