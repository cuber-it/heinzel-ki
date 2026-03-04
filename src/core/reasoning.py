"""heinzel_core.reasoning.

Austauschbare Strategien fuer den Reasoning-Loop des Heinzel.

Der Core (Runner) stellt Pipeline + ContextHistory.
Die ReasoningStrategy entscheidet WIE auf eine Anfrage geantwortet wird:
direkt (PassthroughStrategy), mit Tool-Loop, mit Reflection etc.

Verwendung (Default reicht fuer die meisten Faelle):
    from core.reasoning import StrategyRegistry
    strategy = StrategyRegistry.get_default()

Custom-Strategie via AddOn registrieren::

    from core.reasoning import StrategyRegistry
    StrategyRegistry.register(MyStrategy())
    StrategyRegistry.set_default("my_strategy")

Compliance-Pruefung fuer neue Strategien:
    Siehe test/test_reasoning.py::assert_strategy_compliance()
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from .models.base import ToolResult
from .models.placeholders import Reflection, StepPlan

if TYPE_CHECKING:
    from .models.context import ContextHistory, PipelineContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Begleitende Models
# ---------------------------------------------------------------------------


class StrategyFeedback(BaseModel, frozen=True):
    """Feedback nach einer abgeschlossenen Session.

    Dient als Eingabe fuer adapt() — Langzeit-Lernschnittstelle.
    In HNZ-002 wird adapt() als No-Op implementiert.
    reasoning_trace enthaelt die Schrittfolge als lesbare Strings
    (aus ContextHistory.to_reasoning_trace()).
    """

    session_id: str
    success: bool
    iterations_used: int
    user_rating: float = 0.0            # 0.0-1.0, 0.0 = kein Rating
    outcome_summary: str = ""
    reasoning_trace: tuple[str, ...] = ()


class StrategyMetrics(BaseModel, frozen=True):
    """Leistungsmetriken einer Strategie fuer den aktuellen Turn.

    efficiency_score: 0.0 = keine Effizienz, 1.0 = optimal
    history_depth: Anzahl Snapshots in der ContextHistory
    """

    iterations: int = 0
    total_tokens: int = 0
    efficiency_score: float = 0.0       # 0.0-1.0
    tool_calls: int = 0
    reflections_count: int = 0
    history_depth: int = 0


class ToolResultAssessment(BaseModel, frozen=True):
    """Bewertung eines Tool-Ergebnisses im Kontext der gesamten History.

    verdict:
      - 'sufficient'       : Tool-Ergebnis reicht, weiter im Loop
      - 'needs_retry'      : gleiches Tool nochmal (z.B. anderer Input)
      - 'try_alternative'  : alternative_tool versuchen
      - 'abort'            : Tool-Loop abbrechen, direkt antworten
    """

    verdict: Literal["sufficient", "needs_retry", "try_alternative", "abort"]
    reason: str = ""
    alternative_tool: str | None = None


# ---------------------------------------------------------------------------
# ReasoningStrategy ABC
# ---------------------------------------------------------------------------


class ReasoningStrategy(ABC):
    """Interface fuer austauschbare Reasoning-Strategien.

    Eine Strategie steuert den Reasoning-Loop vollstaendig.
    Sie hat immer Zugriff auf die ContextHistory — kann also
    zurueckblicken, Snapshots vergleichen (history.diff()) und
    den gesamten Denkpfad lesen (history.to_reasoning_trace()).

    Alle Methoden ausser adapt() erhalten ctx + history.
    Alle Implementierungen muessen thread-safe und zustandslos sein —
    der Zustand liegt in PipelineContext/ContextHistory,
    nicht in der Strategie.

    Compliance-Pruefung: test/test_reasoning.py::assert_strategy_compliance()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Eindeutiger Name der Strategie (fuer Registry-Lookup)."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantische Versionsnummer (z.B. '1.0.0')."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Wann ist diese Strategie geeignet? (fuer Logging/Auswahl)."""

    @abstractmethod
    async def initialize(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> PipelineContext:
        """Einmalig vor dem Loop.

        Kann ctx via ctx.evolve(**changes) anreichern — z.B. Ziele setzen,
        Budget berechnen, Kontext aus history laden.
        Gibt immer einen (ggf. unveraenderten) PipelineContext zurueck.
        """

    @abstractmethod
    async def should_continue(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> bool:
        """Darf der Reasoning-Loop weiterlaufen?

        Typische Abbruchkriterien:
          - ctx.budget.max_iterations erreicht
          - ctx.goals alle erledigt
          - history.diff() zeigt keinen Fortschritt mehr
          - ctx.loop_done gesetzt (via evolve)

        Setzt ctx.loop_done via evolve() wenn fertig — gibt False zurueck.
        """

    @abstractmethod
    async def plan_next_step(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StepPlan:
        """Was im naechsten Schritt tun?

        Kann history.to_reasoning_trace() nutzen um bisherigen
        Denkpfad als Kontext zu verwenden.
        Gibt StepPlan zurueck — next_action bestimmt den Pfad:
          'think' -> weiterer interner Schritt
          'tool'  -> Tool-Call (tool_name + tool_args benoetigt)
          'respond' -> direkte Antwort
        """

    @abstractmethod
    async def reflect(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> Reflection:
        """Nach jedem Schritt: war er nuetzlich?

        Vergleicht typischerweise history.snapshots[-2] mit dem aktuellen
        ctx via history.diff() um Fortschritt zu messen.
        Reflection.insight fliesst in den naechsten plan_next_step() ein.
        Reflection.suggest_adaptation == True signalisiert adapt()-Bedarf.
        """

    @abstractmethod
    async def adapt(self, feedback: StrategyFeedback) -> None:
        """Langzeit-Lernschnittstelle.

        Wird nach einer abgeschlossenen Session aufgerufen.
        In HNZ-002: No-Op fuer alle Strategien.
        In HNZ-003+: kann interne Parameter anpassen (z.B. recency_window).
        """

    @abstractmethod
    async def metrics(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StrategyMetrics:
        """Aktuelle Leistungsmetriken fuer den laufenden Turn.

        Nuetzlich fuer Monitoring, Logging und Strategie-Auswahl.
        """

    @abstractmethod
    async def on_tool_result(
        self,
        ctx: PipelineContext,
        result: ToolResult,
        history: ContextHistory,
    ) -> ToolResultAssessment:
        """Bewertet ein Tool-Ergebnis im Kontext der gesamten History.

        Kann z.B. history.diff() nutzen um zu pruefen ob das Tool
        tatsaechlich neuen Zustand gebracht hat.
        """


# ---------------------------------------------------------------------------
# PassthroughStrategy — DEFAULT
# ---------------------------------------------------------------------------


class PassthroughStrategy(ReasoningStrategy):
    """Kein Reasoning-Loop. Genau ein Durchlauf, direkter LLM-Call.

    Entspricht dem Verhalten von MVP-001:
    User-Input -> LLM -> Antwort. Kein Denken, kein Tool-Loop,
    keine Reflection. Alle history-Parameter werden akzeptiert
    aber ignoriert.

    PassthroughStrategy ist der Default fuer einfache Heinzels
    und der Rueckfall wenn keine andere Strategie passt.
    """

    @property
    def name(self) -> str:
        return "passthrough"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return (
            "Kein Reasoning-Loop. Direkt antworten. "
            "Default fuer einfache Heinzels und MVP-001-Verhalten."
        )

    async def initialize(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> PipelineContext:
        """Keine Initialisierung noetig — ctx unveraendert zurueck."""
        return ctx

    async def should_continue(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> bool:
        """Kein Reasoning-Loop — immer False.

        Die operative Ebene (ctx.loop_done via Provider/AddOn) ist davon
        unabhaengig und wird in der Pipeline separat geprueft.
        """
        return False

    async def plan_next_step(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StepPlan:
        """Immer: direkt antworten."""
        return StepPlan(next_action="respond")

    async def reflect(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> Reflection:
        """Kein Schritt zum Reflektieren — immer positiv."""
        return Reflection(
            step_useful=True,
            insight="",
            confidence=1.0,
        )

    async def adapt(self, feedback: StrategyFeedback) -> None:
        """No-Op in HNZ-002."""

    async def metrics(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StrategyMetrics:
        """Minimale Metriken: immer 1 Iteration."""
        return StrategyMetrics(
            iterations=1,
            history_depth=len(history._snapshots),
        )

    async def on_tool_result(
        self,
        ctx: PipelineContext,
        result: ToolResult,
        history: ContextHistory,
    ) -> ToolResultAssessment:
        """Passthrough: Tool-Ergebnis immer als ausreichend bewerten."""
        return ToolResultAssessment(verdict="sufficient")


# ---------------------------------------------------------------------------
# StrategyRegistry
# ---------------------------------------------------------------------------


class StrategyRegistry:
    """Singleton-Registry fuer ReasoningStrategy-Implementierungen.

    Custom-Strategie registrieren:
        StrategyRegistry.register(MyStrategy())
        StrategyRegistry.set_default("my_strategy")

    Laufzeit-Wechsel via Runner:
        runner.set_strategy("my_strategy")
        heinzel.set_strategy(MyStrategy())  # registriert + setzt
    """

    _strategies: dict[str, ReasoningStrategy] = {}
    _default: str = "passthrough"

    @classmethod
    def register(cls, strategy: ReasoningStrategy) -> None:
        """Strategie registrieren (ueberschreibt ggf. bestehende)."""
        cls._strategies[strategy.name] = strategy
        logger.debug("StrategyRegistry: '%s' registriert.", strategy.name)

    @classmethod
    def get(cls, name: str) -> ReasoningStrategy | None:
        """Strategie per Name holen. None wenn nicht registriert."""
        return cls._strategies.get(name)

    @classmethod
    def list_available(cls) -> list[str]:
        """Alle registrierten Strategien."""
        return list(cls._strategies.keys())

    @classmethod
    def set_default(cls, name: str) -> None:
        """Standard-Strategie setzen.

        Raises:
            KeyError: wenn name nicht registriert ist.
        """
        if name not in cls._strategies:
            raise KeyError(
                f"Strategie '{name}' nicht registriert. "
                f"Verfuegbar: {list(cls._strategies.keys())}"
            )
        cls._default = name
        logger.debug("StrategyRegistry: Default auf '%s' gesetzt.", name)

    @classmethod
    def get_default(cls) -> ReasoningStrategy:
        """Standard-Strategie holen."""
        return cls._strategies[cls._default]


# ---------------------------------------------------------------------------
# Default beim Import registrieren
# ---------------------------------------------------------------------------

StrategyRegistry.register(PassthroughStrategy())
