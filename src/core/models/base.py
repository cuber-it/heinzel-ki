"""Basis-Models für die Heinzel-Pipeline.

Alle Models sind immutabel (frozen=True). Inhalte die Tuples verlangen
werden als tuple übergeben — Listen sind in frozen Models nicht erlaubt.
"""

from typing import Any

from pydantic import BaseModel


class Message(BaseModel, frozen=True):
    """Eine Nachricht im Gesprächsverlauf."""

    role: str
    content: str | list = ""


class TokenUsage(BaseModel, frozen=True):
    """Token-Verbrauch eines LLM-Calls."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ToolCall(BaseModel, frozen=True):
    """Ein Tool-Aufruf durch das LLM."""

    call_id: str
    tool_name: str
    args: dict[str, Any] = {}


class ToolResult(BaseModel, frozen=True):
    """Ergebnis eines Tool-Aufrufs."""

    call_id: str
    result: Any = None
    error: str | None = None
    duration_ms: int = 0


class MemoryResult(BaseModel, frozen=True):
    """Ergebnis einer Gedächtnisabfrage."""

    source: str
    content: str
    relevance: float = 1.0  # 0.0 - 1.0
    metadata: dict[str, Any] = {}


class ThinkingStep(BaseModel, frozen=True):
    """Ein Reasoning-Schritt des Heinzel."""

    iteration: int
    thought: str
    action: str
    observation: str
    snapshot_id: str


class AddOnResult(BaseModel, frozen=True):
    """Rückgabe eines AddOn-Hooks."""

    modified_ctx: Any  # PipelineContext — Any wegen Forward-Reference
    halt: bool = False
    ack: bool = True
    error: str | None = None
