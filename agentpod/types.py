"""Shared types for the AgentPod project."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Configuration ──────────────────────────────────────────────


@dataclass
class RuntimeOptions:
    model: str = "doubao-seed-1-8-251228"
    fallback_model: str | None = None
    max_turns: int = 100
    max_budget_usd: float | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    context_window: int = 200_000
    compress_threshold: float = 0.7
    effort: Literal["low", "medium", "high", "max"] = "high"
    env: dict[str, str] = field(default_factory=dict)


# ── Session ────────────────────────────────────────────────────


@dataclass
class SessionMeta:
    session_id: str
    created_at: str
    parent_session_id: str | None = None


# ── Context ────────────────────────────────────────────────────


@dataclass
class ContextSnapshot:
    estimated_tokens: int
    context_window: int
    usage_ratio: float
    message_count: int


# ── Runtime Events ─────────────────────────────────────────────


@dataclass
class RuntimeEvent:
    """Base class for all runtime events."""


@dataclass
class MessageStart(RuntimeEvent):
    session_id: str
    model: str


@dataclass
class ReasoningDelta(RuntimeEvent):
    content: str


@dataclass
class TextDelta(RuntimeEvent):
    content: str


@dataclass
class ToolCallStart(RuntimeEvent):
    """Model started generating a tool call (name known, args still streaming)."""
    tool: str


@dataclass
class ToolStart(RuntimeEvent):
    tool: str
    input: dict


@dataclass
class ToolEnd(RuntimeEvent):
    tool: str
    result: str
    is_error: bool = False


@dataclass
class TurnComplete(RuntimeEvent):
    turn: int


@dataclass
class UserInputRequired(RuntimeEvent):
    tool_use_id: str
    question: str
    options: list[str] | None = None


@dataclass
class TodoUpdate(RuntimeEvent):
    todos: list[dict]


@dataclass
class ContextSnapshotEvent(RuntimeEvent):
    snapshot: ContextSnapshot


@dataclass
class Done(RuntimeEvent):
    usage: dict
    cost: float


@dataclass
class Error(RuntimeEvent):
    message: str
    retryable: bool = False
