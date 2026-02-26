"""Tool registry and default tool set."""

from __future__ import annotations

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

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


def create_default_registry() -> ToolRegistry:
    """Import and register all built-in tools."""
    registry = ToolRegistry()

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

    for tool_cls in [
        ReadTool, WriteTool, EditTool, GlobTool, GrepTool, BashTool,
        WebSearchTool, WebFetchTool, ListSkillsTool, GetSkillTool,
        AskUserTool, TodoWriteTool,
    ]:
        registry.register(tool_cls())

    return registry


__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "safe_resolve",
    "create_default_registry",
]
