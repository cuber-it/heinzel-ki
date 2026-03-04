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


# ---------------------------------------------------------------------------
# ChainOfThoughtStrategy — Denken vor Antworten
# ---------------------------------------------------------------------------


class ChainOfThoughtStrategy(ReasoningStrategy):
    """Zwei-Schritt-Strategie: erst denken, dann antworten.

    Schritt 1 (think): LLM wird gebeten laut zu denken — Analyse,
    Überlegungen, Zwischenschritte. Antwort landet in ctx.response.

    Schritt 2 (respond): LLM bekommt den Denkschritt als Kontext
    und formuliert die finale Antwort.

    Sichtbar unterschiedlich von Passthrough: zwei LLM-Calls pro Turn.
    Geeignet für komplexe Fragen, Planung, Erklärungen.
    """

    @property
    def name(self) -> str:
        return "chain_of_thought"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return (
            "Zwei LLM-Calls pro Turn: erst denken, dann antworten. "
            "Geeignet fuer komplexe Fragen, Planung, schrittweise Erklaerungen."
        )

    async def initialize(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> PipelineContext:
        """Kein Setup noetig — Schritt-Steuerung via loop_iteration."""
        return ctx

    async def should_continue(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> bool:
        """Weitermachen nach Schritt 1 (think) — stoppen nach Schritt 2 (respond)."""
        return ctx.loop_iteration == 0

    async def plan_next_step(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StepPlan:
        """Iteration 0 → denken, Iteration 1 → antworten."""
        if ctx.loop_iteration == 0:
            return StepPlan(
                next_action="think",
                focus="Analysiere die Anfrage sorgfaeltig. Denke laut: "
                      "Was wird gefragt? Welche Schritte sind noetig? "
                      "Welche Annahmen machst du?",
                prompt_addition=(
                    "\n\n[DENK-SCHRITT] Analysiere die Anfrage zuerst gruendlich "
                    "und denke laut. Zeige deinen Denkprozess vollstaendig. "
                    "Fasse am Ende deine Kernerkenntnisse zusammen."
                ),
            )
        # Iteration 1: bisherigen Denkschritt als Kontext nutzen
        thinking = ctx.response or ""
        return StepPlan(
            next_action="respond",
            focus="Formuliere die finale Antwort basierend auf dem Denkschritt.",
            prompt_addition=(
                f"\n\n[DENKSCHRITT-ERGEBNIS]\n{thinking}\n\n"
                "[AUFGABE] Formuliere jetzt die finale, praezise Antwort "
                "basierend auf diesem Denkschritt. Kein Denken mehr — "
                "direkte, klare Antwort."
            ),
        )

    async def reflect(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> Reflection:
        """Nach Schritt 1: war der Denkschritt hilfreich?"""
        if ctx.loop_iteration == 0:
            useful = bool(ctx.response and len(ctx.response) > 50)
            return Reflection(
                step_useful=useful,
                insight=f"Denkschritt: {len(ctx.response or '')} Zeichen",
                confidence=0.8 if useful else 0.4,
            )
        return Reflection(
            step_useful=True,
            insight="Finale Antwort formuliert.",
            confidence=0.9,
        )

    async def adapt(self, feedback: StrategyFeedback) -> None:
        pass  # HNZ-003+

    async def metrics(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StrategyMetrics:
        return StrategyMetrics(
            iterations=ctx.loop_iteration + 1,
            tool_calls=0,
            confidence=0.85,
        )

    async def on_tool_result(
        self,
        ctx: PipelineContext,
        result: ToolResult,
        history: ContextHistory,
    ) -> ToolResultAssessment:
        return ToolResultAssessment(status="sufficient")


StrategyRegistry.register(ChainOfThoughtStrategy())


# ---------------------------------------------------------------------------
# DeepReasoningStrategy — Iterativer Reasoning-Loop mit Selbstkritik
# ---------------------------------------------------------------------------


class DeepReasoningStrategy(ReasoningStrategy):
    """Iterativer Reasoning-Loop modelliert nach o1/Extended-Thinking-Ansatz.

    Wie moderne LLMs intern arbeiten:
      1. Problem-Dekomposition  — Was genau wird gefragt? Welche Dimensionen?
      2. Exploration            — Loesungsraum erkunden, Ansaetze abwaegen
      3. Reasoning (iterativ)   — Durcharbeiten mit akkumuliertem Trace
      4. Selbstkritik           — Eigenes Reasoning pruefen, Luecken finden
      5. Synthese               — Saubere finale Antwort auf Basis des Traces

    Kernprinzipien:
      - Zustandslos: Trace laeuft ueber ctx.metadata["hnz_rt_*"]
      - Konfidenz-basierter Fruehstop: bei Schwelle vor max_iterations
      - Jeder Schritt sieht den gesamten bisherigen Trace
      - Selbstkritik ist Pflicht vor der Antwort

    Konfiguration (im Konstruktor oder via Subklasse):
      max_iterations      : maximale Reasoning-Schritte vor Antwort (default 4)
      confidence_threshold: Fruehstop wenn Konfidenz >= Wert (default 0.85)

    Metadaten-Keys in ctx.metadata:
      hnz_rt_trace        : akkumulierter Reasoning-Text
      hnz_rt_phase        : aktuelle Phase (decompose/explore/reason/critique/synthesize)
      hnz_rt_confidence   : Konfidenz 0.0-1.0 nach letzter Reflexion
      hnz_rt_budget_used  : verbrauchte Reasoning-Schritte
    """

    _PHASES = ["decompose", "explore", "reason", "critique", "synthesize"]

    _PHASE_PROMPTS = {
        "decompose": (
            "\n\n[REASONING: PROBLEM-ANALYSE]\n"
            "Analysiere diese Anfrage grundlich bevor du antwortest:\n"
            "- Was genau wird gefragt? Was ist der Kern der Anfrage?\n"
            "- Welche Teilprobleme stecken darin?\n"
            "- Welche Annahmen werden gemacht? Welche sind moeglicherweise falsch?\n"
            "- Was ist der Kontext? Was fehlt noch?\n"
            "Denke laut und vollstaendig. Kein voreiliges Antworten."
        ),
        "explore": (
            "\n\n[REASONING: LOESUNGSRAUM]\n"
            "Bisheriger Reasoning-Trace:\n{trace}\n\n"
            "Erkunde jetzt den Loesungsraum:\n"
            "- Welche Ansaetze gibt es? Liste alle sinnvollen auf.\n"
            "- Was spricht fuer/gegen jeden Ansatz?\n"
            "- Welcher Ansatz ist am vielversprechendsten — und warum?\n"
            "- Welche Risiken oder Fallstricke gibt es?\n"
            "Sei gruendlich. Schreibe deinen Denkprozess vollstaendig auf."
        ),
        "reason": (
            "\n\n[REASONING: DURCHARBEITEN — Schritt {step}]\n"
            "Bisheriger Reasoning-Trace:\n{trace}\n\n"
            "Arbeite jetzt den vielversprechendsten Ansatz durch:\n"
            "- Vertiefe die Analyse wo noetig.\n"
            "- Loesung Schritt fuer Schritt entwickeln.\n"
            "- Auf Luecken oder Widerspruche im bisherigen Reasoning achten.\n"
            "- Neue Erkenntnisse explizit benennen.\n"
            "Schreibe jeden Schritt deines Denkens auf."
        ),
        "critique": (
            "\n\n[REASONING: SELBSTKRITIK]\n"
            "Bisheriger Reasoning-Trace:\n{trace}\n\n"
            "Pruefe dein bisheriges Reasoning kritisch:\n"
            "- Wo koennte das Reasoning fehlerhaft oder unvollstaendig sein?\n"
            "- Welche Gegenargumente oder Randfaelle wurden ignoriert?\n"
            "- Ist die Schlussfolgerung wirklich gut begruendet?\n"
            "- Was wuerde ein kritischer Experte bemaengeln?\n"
            "Sei ehrlich und streng. Gib am Ende eine Konfidenz 0-100 an."
        ),
        "synthesize": (
            "\n\n[REASONING: FINALE SYNTHESE]\n"
            "Vollstaendiger Reasoning-Trace:\n{trace}\n\n"
            "Formuliere jetzt die finale Antwort:\n"
            "- Basiere dich vollstaendig auf dem Reasoning-Trace.\n"
            "- Klar, praezise, vollstaendig — kein weiteres Denken mehr.\n"
            "- Erkenne Unsicherheiten explizit an wo sie bestehen.\n"
            "- Antworte direkt auf die urspruengliche Frage."
        ),
    }

    def __init__(
        self,
        max_iterations: int = 4,
        confidence_threshold: float = 0.85,
    ) -> None:
        self._max_iterations = max_iterations
        self._confidence_threshold = confidence_threshold

    @property
    def name(self) -> str:
        return "deep_reasoning"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return (
            f"Iterativer Reasoning-Loop (max {self._max_iterations} Schritte, "
            f"Konfidenz-Schwelle {self._confidence_threshold:.0%}). "
            "Problem-Analyse → Exploration → Reasoning → Selbstkritik → Synthese. "
            "Modelliert nach o1/Extended-Thinking-Ansatz."
        )

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _meta(self, ctx: PipelineContext) -> dict:
        """Reasoning-Metadaten aus ctx.metadata holen (nie None)."""
        return {
            "trace": ctx.metadata.get("hnz_rt_trace", ""),
            "phase": ctx.metadata.get("hnz_rt_phase", "decompose"),
            "confidence": float(ctx.metadata.get("hnz_rt_confidence", 0.0)),
            "budget_used": int(ctx.metadata.get("hnz_rt_budget_used", 0)),
        }

    def _update_meta(
        self,
        ctx: PipelineContext,
        trace: str | None = None,
        phase: str | None = None,
        confidence: float | None = None,
        budget_used: int | None = None,
    ) -> PipelineContext:
        """ctx.metadata mit neuen Reasoning-Werten aktualisieren."""
        m = self._meta(ctx)
        new_meta = {
            **ctx.metadata,
            "hnz_rt_trace": trace if trace is not None else m["trace"],
            "hnz_rt_phase": phase if phase is not None else m["phase"],
            "hnz_rt_confidence": confidence if confidence is not None else m["confidence"],
            "hnz_rt_budget_used": budget_used if budget_used is not None else m["budget_used"],
        }
        return ctx.evolve(metadata=new_meta)

    def _phase_for_iteration(self, iteration: int) -> str:
        """Welche Phase entspricht der aktuellen Iteration?"""
        if iteration == 0:
            return "decompose"
        if iteration == 1:
            return "explore"
        # Letzte Iteration vor max: Selbstkritik
        if iteration >= self._max_iterations - 1:
            return "critique"
        return "reason"

    def _extract_confidence(self, response: str) -> float:
        """Konfidenz aus Selbstkritik-Antwort extrahieren (heuristisch)."""
        import re
        # Suche nach "Konfidenz: 85" oder "confidence: 0.85" oder "85%" etc.
        patterns = [
            r"konfidenz[:\s]+(\d+(?:\.\d+)?)\s*%?",
            r"confidence[:\s]+(\d+(?:\.\d+)?)\s*%?",
            r"(\d{2,3})\s*%\s*(?:konfidenz|confidence|sicher)",
            r"(?:konfidenz|confidence)[^0-9]*(\d+(?:\.\d+)?)",
        ]
        for pat in patterns:
            m = re.search(pat, response.lower())
            if m:
                val = float(m.group(1))
                return min(val / 100.0, 1.0) if val > 1.0 else val
        # Fallback: Antwortlaenge als grober Indikator
        length = len(response)
        if length > 800:
            return 0.75
        if length > 400:
            return 0.6
        return 0.45

    # ------------------------------------------------------------------
    # ReasoningStrategy Interface
    # ------------------------------------------------------------------

    async def initialize(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> PipelineContext:
        """Reasoning-Metadaten initialisieren."""
        return self._update_meta(
            ctx,
            trace="",
            phase="decompose",
            confidence=0.0,
            budget_used=0,
        )

    async def should_continue(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> bool:
        """Weitermachen wenn Budget nicht erschoepft und Konfidenz unter Schwelle."""
        m = self._meta(ctx)
        # Synthese ist immer der letzte Schritt — danach stoppen
        if m["phase"] == "synthesize":
            return False
        # Budget erschoepft — erzwinge Synthese im naechsten Schritt
        if m["budget_used"] >= self._max_iterations:
            return True  # noch ein Schritt: Synthese
        # Fruehstop: genuegend Konfidenz nach Selbstkritik
        if m["phase"] == "critique" and m["confidence"] >= self._confidence_threshold:
            return True  # noch ein Schritt: Synthese
        return True

    async def plan_next_step(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StepPlan:
        """Naechste Phase bestimmen und Prompt aufbauen."""
        m = self._meta(ctx)
        budget = m["budget_used"]
        phase = m["phase"]
        trace = m["trace"]

        # Nach Critique mit hoher Konfidenz oder Budget erschoepft: Synthese
        if phase == "critique" or budget >= self._max_iterations:
            prompt = self._PHASE_PROMPTS["synthesize"].format(trace=trace)
            return StepPlan(
                next_action="respond",
                focus="Finale Antwort auf Basis des vollstaendigen Reasoning-Traces.",
                prompt_addition=prompt,
            )

        # Naechste Phase bestimmen
        next_phase = self._phase_for_iteration(budget)
        step = max(0, budget - 1)  # fuer "reason"-Nummerierung
        prompt_template = self._PHASE_PROMPTS.get(next_phase, self._PHASE_PROMPTS["reason"])
        prompt = prompt_template.format(trace=trace, step=step)

        return StepPlan(
            next_action="think",
            focus=f"Reasoning-Phase: {next_phase} (Schritt {budget + 1}/{self._max_iterations})",
            prompt_addition=prompt,
        )

    async def reflect(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> Reflection:
        """Reasoning-Schritt bewerten und Trace akkumulieren."""
        m = self._meta(ctx)
        response = ctx.response or ""
        phase = m["phase"]
        budget = m["budget_used"]

        # Trace akkumulieren
        phase_header = f"\n{'='*40}\n[{phase.upper()} — Schritt {budget + 1}]\n{'='*40}\n"
        new_trace = m["trace"] + phase_header + response

        # Konfidenz: aus Selbstkritik extrahieren, sonst schrittweise aufbauen
        if phase == "critique":
            confidence = self._extract_confidence(response)
        else:
            # Konfidenz steigt mit Budget-Verbrauch
            confidence = min(0.4 + (budget * 0.15), 0.82)

        # Naechste Phase bestimmen
        next_budget = budget + 1
        if phase == "critique" or next_budget >= self._max_iterations:
            next_phase = "synthesize"
        else:
            next_phase = self._phase_for_iteration(next_budget)

        ctx = self._update_meta(
            ctx,
            trace=new_trace,
            phase=next_phase,
            confidence=confidence,
            budget_used=next_budget,
        )

        useful = len(response) > 100
        return Reflection(
            step_useful=useful,
            insight=(
                f"Phase '{phase}' abgeschlossen. "
                f"Trace: {len(new_trace)} Zeichen. "
                f"Konfidenz: {confidence:.0%}. "
                f"Naechste Phase: {next_phase}."
            ),
            confidence=confidence,
            suggest_adaptation=not useful,
        )

    async def adapt(self, feedback: StrategyFeedback) -> None:
        pass  # HNZ-003+: Lernschnittstelle

    async def metrics(
        self,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> StrategyMetrics:
        m = self._meta(ctx)
        return StrategyMetrics(
            iterations=m["budget_used"],
            tool_calls=0,
            confidence=m["confidence"],
        )

    async def on_tool_result(
        self,
        ctx: PipelineContext,
        result: ToolResult,
        history: ContextHistory,
    ) -> ToolResultAssessment:
        return ToolResultAssessment(status="sufficient")


StrategyRegistry.register(DeepReasoningStrategy())
