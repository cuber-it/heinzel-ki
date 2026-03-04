"""Platzhalter-Models fuer spaetere Epics.

Diese Models haben bewusst minimale Felder. Details werden in den
zugehoerigen Epics ausgebaut:
  - Fact, Skill  -> HNZ-003
  - Goal         -> HNZ-011
  - ResourceBudget, StepPlan, Reflection, EvaluationResult -> HNZ-002+
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Fact(BaseModel, frozen=True):
    """Ein Fakt im Gedaechtnis des Heinzel.

    (Platzhalter, Details in HNZ-003)"""

    key: str
    value: str
    agent_id: str | None = None


class Skill(BaseModel, frozen=True):
    """Eine Faehigkeit/Anweisung. (Platzhalter, Details in HNZ-003)"""

    name: str
    version: str = "0.1"
    description: str = ""


class Goal(BaseModel, frozen=True):
    """Ein Ziel des Heinzel. (Platzhalter, Details in HNZ-011)"""

    id: str
    description: str
    status: str = "open"       # open | active | done | failed
    priority: int = 0
    parent_id: str | None = None


class ResourceBudget(BaseModel, frozen=True):
    """Ressourcen-Limits fuer einen Turn."""

    max_tokens: int = 100_000
    max_iterations: int = 10
    max_tool_calls: int = 20


class StepPlan(BaseModel, frozen=True):
    """Naechster Schritt im Reasoning-Loop.

    next_action steuert was der Heinzel als naechstes tut:
      - 'think'   : weiterer interner Denkschritt
      - 'tool'    : Tool-Call (tool_name + tool_args benoetigt)
      - 'respond' : direkte Antwort an den User (Default)

    prompt_addition wird dem naechsten LLM-Prompt vorangestellt.
    focus beschreibt das Hauptziel des naechsten Schritts (fuer Logging/Trace).
    steps bleibt fuer Rueckwaertskompatibilitaet erhalten.
    """

    next_action: Literal["think", "tool", "respond"] = "respond"
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    prompt_addition: str = ""
    focus: str = ""
    steps: tuple[str, ...] = ()


class Reflection(BaseModel, frozen=True):
    """Selbst-Reflexion nach einem Reasoning-Schritt.

    step_useful: war der letzte Schritt hilfreich?
    insight: Erkenntnis die in plan_next_step() einfliesst
    confidence: Zuversicht in die bisherige Richtung (0.0-1.0)
    suggest_adaptation: Strategie sollte adapt() aufrufen?
    compared_snapshot_ids: welche zwei Snapshots wurden verglichen
      (typisch: (history.snapshots[-2].id, history.current.id))

    text + snapshot_id bleiben fuer Rueckwaertskompatibilitaet erhalten.
    """

    step_useful: bool = True
    insight: str = ""
    confidence: float = 1.0
    suggest_adaptation: bool = False
    compared_snapshot_ids: tuple[str, str] = ("", "")
    text: str = ""
    snapshot_id: str = ""


class EvaluationResult(BaseModel, frozen=True):
    """Bewertung der eigenen Antwort."""

    score: float = 0.0   # 0.0 - 1.0
    reasoning: str = ""


class CompactionResult(BaseModel, frozen=True):
    """Ergebnis einer Compaction-Operation."""

    kept_turns: tuple = ()          # Behaltene Turns (Turn-Objekte)
    dropped_turns: tuple = ()       # Verworfene Turns
    summary: str | None = None      # Destillat der verworfenen Turns
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    critical_preserved: bool = True


class HandoverContext(BaseModel, frozen=True):
    """Uebergabe-Kontext beim Wechsel zu einer neuen Rolling Session."""

    from_session_id: str
    summary: str                        # Was war diese Session
    critical_turns: tuple = ()          # Niemals verlorene Turns
    facts_extracted: tuple[str, ...] = ()   # Destillierte Erkenntnisse
    goals_open: tuple[str, ...] = ()        # Unerledigte Ziele
