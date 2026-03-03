"""PipelineContext, ContextDiff und ContextHistory.

Kern-Designentscheidung: PipelineContext ist immutabel. Jeder Pipeline-Schritt
erzeugt über evolve() einen neuen Snapshot. Die vollständige Sequenz ist die
Denkgeschichte des Heinzel für einen Turn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .types import HookPoint
from .base import Message, TokenUsage, ToolCall, ToolResult, MemoryResult, ThinkingStep
from .placeholders import (
    Fact, Skill, Goal, ResourceBudget, StepPlan, Reflection, EvaluationResult
)


class PipelineContext(BaseModel, frozen=True):
    """
    Immutabler Zustandsträger für einen Pipeline-Durchlauf.

    Wird nie direkt mutiert — evolve() ist der einzige Weg
    einen neuen Snapshot zu erzeugen.
    """

    # --- Input-Schicht ---
    raw_input: str = ""
    parsed_input: str = ""
    is_command: bool = False
    user_strategy_hint: str | None = None

    # --- Wissens-Schicht ---
    memory_results: tuple[MemoryResult, ...] = ()
    short_circuit: bool = False
    messages: tuple[Message, ...] = ()
    system_prompt: str = ""
    model: str = ""
    provider: str = ""
    facts: tuple[Fact, ...] = ()
    skills: tuple[Skill, ...] = ()

    # --- Zustands-Schicht ---
    loop_iteration: int = 0
    loop_done: bool = False
    goals: tuple[Goal, ...] = ()
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    step_plan: StepPlan | None = None
    reflection: Reflection | None = None

    # --- Produktions-Schicht ---
    stream_buffer: str = ""
    thinking_steps: tuple[ThinkingStep, ...] = ()
    tool_requests: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    evaluation: EvaluationResult | None = None

    # --- Output-Schicht ---
    response: str = ""

    # --- Meta ---
    session_id: str = ""
    heinzel_id: str = ""
    working_memory_turns: int = 0
    memory_tokens_used: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    phase: HookPoint = HookPoint.ON_INPUT
    metadata: dict[str, Any] = Field(default_factory=dict)

    # --- Navigation ---
    previous: PipelineContext | None = None
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))

    def evolve(self, **changes: Any) -> PipelineContext:
        """
        Erzeugt einen neuen Snapshot mit geänderten Feldern.

        previous wird automatisch auf self gesetzt.
        snapshot_id und timestamp werden neu generiert.
        phase muss in changes angegeben werden.
        """
        data = self.model_dump(exclude={"previous", "snapshot_id", "timestamp"})
        data.update(changes)
        data["previous"] = self
        data["snapshot_id"] = str(uuid4())
        data["timestamp"] = datetime.now(timezone.utc)
        return PipelineContext(**data)


class ContextDiff(BaseModel, frozen=True):
    """Unterschied zwischen zwei Snapshots."""

    snapshot_a_id: str
    snapshot_b_id: str
    added_fields: dict[str, Any] = Field(default_factory=dict)
    changed_fields: dict[str, tuple[Any, Any]] = Field(default_factory=dict)
    phases_between: list[HookPoint] = Field(default_factory=list)


class ContextHistory:
    """
    Verwaltet die vollständige Snapshot-Sequenz eines Turns.

    Nicht frozen — ist der veränderliche Container für immutable Snapshots.
    """

    def __init__(self) -> None:
        self._snapshots: list[PipelineContext] = []

    def push(self, ctx: PipelineContext) -> None:
        """Neuen Snapshot anhängen."""
        self._snapshots.append(ctx)

    @property
    def current(self) -> PipelineContext:
        """Aktuellster Snapshot."""
        if not self._snapshots:
            raise RuntimeError("ContextHistory ist leer")
        return self._snapshots[-1]

    @property
    def initial(self) -> PipelineContext:
        """Erster Snapshot (raw input)."""
        if not self._snapshots:
            raise RuntimeError("ContextHistory ist leer")
        return self._snapshots[0]

    def at_phase(self, hook: HookPoint) -> PipelineContext | None:
        """Letzten Snapshot einer bestimmten Phase zurückgeben."""
        for snap in reversed(self._snapshots):
            if snap.phase == hook:
                return snap
        return None

    def between(self, phase_a: HookPoint, phase_b: HookPoint) -> list[PipelineContext]:
        """Alle Snapshots zwischen zwei Phasen (beide inklusiv)."""
        result = []
        capturing = False
        for snap in self._snapshots:
            if snap.phase == phase_a:
                capturing = True
            if capturing:
                result.append(snap)
            if capturing and snap.phase == phase_b:
                break
        return result

    def diff(self, snap_a: PipelineContext, snap_b: PipelineContext) -> ContextDiff:
        """Vergleicht zwei Snapshots feldweise."""
        skip = {"previous", "snapshot_id", "timestamp"}
        fields_a = snap_a.model_dump(exclude=skip)
        fields_b = snap_b.model_dump(exclude=skip)

        added: dict[str, Any] = {}
        changed: dict[str, tuple[Any, Any]] = {}

        for key, val_b in fields_b.items():
            if key not in fields_a:
                added[key] = val_b
            elif fields_a[key] != val_b:
                changed[key] = (fields_a[key], val_b)

        # Phasen zwischen den beiden Snapshots sammeln
        capturing = False
        phases: list[HookPoint] = []
        for snap in self._snapshots:
            if snap.snapshot_id == snap_a.snapshot_id:
                capturing = True
            if capturing:
                phases.append(snap.phase)
            if snap.snapshot_id == snap_b.snapshot_id:
                break

        return ContextDiff(
            snapshot_a_id=snap_a.snapshot_id,
            snapshot_b_id=snap_b.snapshot_id,
            added_fields=added,
            changed_fields=changed,
            phases_between=phases,
        )

    def to_reasoning_trace(self) -> list[str]:
        """Menschenlesbare Denkgeschichte des Turns."""
        lines = []
        for snap in self._snapshots:
            phase = snap.phase.value.upper()
            if snap.raw_input and snap.phase == HookPoint.ON_INPUT:
                lines.append(f"[{phase}] Input erhalten: \"{snap.raw_input[:80]}\"")
            elif snap.phase == HookPoint.ON_MEMORY_HIT and snap.memory_results:
                lines.append(f"[{phase}] {len(snap.memory_results)} Memory-Treffer geladen")
            elif snap.phase == HookPoint.ON_MEMORY_MISS:
                lines.append(f"[{phase}] Kein passendes Memory gefunden")
            elif snap.phase == HookPoint.ON_LLM_REQUEST:
                lines.append(f"[{phase}] LLM-Anfrage gesendet (Modell: {snap.model or 'unbekannt'})")
            elif snap.phase == HookPoint.ON_TOOL_REQUEST and snap.tool_requests:
                tools = ", ".join(t.tool_name for t in snap.tool_requests)
                lines.append(f"[{phase}] Tool aufgerufen: {tools}")
            elif snap.phase == HookPoint.ON_TOOL_RESULT and snap.tool_results:
                lines.append(f"[{phase}] Tool-Ergebnis erhalten")
            elif snap.phase == HookPoint.ON_LOOP_ITERATION:
                lines.append(f"[{phase}] Loop-Iteration {snap.loop_iteration}")
            elif snap.phase == HookPoint.ON_OUTPUT and snap.response:
                lines.append(f"[{phase}] Antwort: \"{snap.response[:80]}\"")
            elif snap.phase == HookPoint.ON_ERROR:
                lines.append(f"[{phase}] Fehler aufgetreten")
            else:
                lines.append(f"[{phase}]")
        return lines
