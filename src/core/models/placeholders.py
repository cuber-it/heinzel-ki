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
