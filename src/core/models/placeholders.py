"""Platzhalter-Models für spätere Epics.

Diese Models haben bewusst minimale Felder. Details werden in den
zugehörigen Epics ausgebaut:
  - Fact, Skill  → HNZ-003
  - Goal         → HNZ-011
  - ResourceBudget, StepPlan, Reflection, EvaluationResult → HNZ-002+
"""

from pydantic import BaseModel


class Fact(BaseModel, frozen=True):
    """Ein Fakt im Gedächtnis des Heinzel. (Platzhalter, Details in HNZ-003)"""

    key: str
    value: str
    heinzel_id: str | None = None


class Skill(BaseModel, frozen=True):
    """Eine Fähigkeit/Anweisung. (Platzhalter, Details in HNZ-003)"""

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
    """Ressourcen-Limits für einen Turn."""

    max_tokens: int = 100_000
    max_iterations: int = 10
    max_tool_calls: int = 20


class StepPlan(BaseModel, frozen=True):
    """Geplante Schritte für einen Turn."""

    steps: tuple[str, ...] = ()


class Reflection(BaseModel, frozen=True):
    """Selbst-Reflexion nach einem Turn."""

    text: str
    snapshot_id: str


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
    """Übergabe-Kontext beim Wechsel zu einer neuen Rolling Session."""

    from_session_id: str
    summary: str                        # Was war diese Session
    critical_turns: tuple = ()          # Niemals verlorene Turns
    facts_extracted: tuple[str, ...] = ()   # Destillierte Erkenntnisse
    goals_open: tuple[str, ...] = ()        # Unerledigte Ziele
