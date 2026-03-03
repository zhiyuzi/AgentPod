"""Tool registry and default tool set."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class ToolRegistry:
    def __init__(self, shared_dir: Path | None = None):
        self._tools: dict[str, Tool] = {}
        self._shared_dir = shared_dir

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_llm_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]


def create_default_registry(shared_dir: Path | None = None) -> ToolRegistry:
    """Import and register all built-in tools."""
    registry = ToolRegistry(shared_dir=shared_dir)

    from agentpod.tools.read import ReadTool
    from agentpod.tools.write import WriteTool
    from agentpod.tools.edit import EditTool
    from agentpod.tools.glob_tool import GlobTool
    from agentpod.tools.grep import GrepTool
    from agentpod.tools.bash import BashTool
    from agentpod.tools.web_search import WebSearchTool
    from agentpod.tools.web_fetch import WebFetchTool
    from agentpod.tools.list_skills import ListSkillsTool
    from agentpod.tools.get_skill import GetSkillTool
    from agentpod.tools.ask_user import AskUserTool
    from agentpod.tools.todo_write import TodoWriteTool

    # Tools that don't need shared_dir
    for tool_cls in [ReadTool, WriteTool, EditTool, GlobTool, GrepTool,
                     WebSearchTool, WebFetchTool, AskUserTool, TodoWriteTool]:
        registry.register(tool_cls())

    # Tools that accept shared_dir
    registry.register(BashTool(shared_dir=shared_dir))
    registry.register(ListSkillsTool(shared_dir=shared_dir))
    registry.register(GetSkillTool(shared_dir=shared_dir))

    return registry


__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "safe_resolve",
    "create_default_registry",
]
