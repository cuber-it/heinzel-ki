"""heinzel_core.compaction.

Austauschbare Strategien fuer Context-Compaction und Rolling Sessions.

Verwendung (Default reicht fuer die meisten Faelle):
    from core.compaction import CompactionRegistry
    strategy = CompactionRegistry.get_default()

Custom-Addon registrieren::

    from core.compaction import CompactionRegistry
    CompactionRegistry.register(MyStrategy())
    CompactionRegistry.set_default("my_strategy")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .models.placeholders import (
    CompactionResult,
    HandoverContext,
    ResourceBudget,
)

if TYPE_CHECKING:
    from .models.context import ContextHistory
    from .session import Session, Turn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kritikalitaets-Keywords — Turns die diese Woerter enthalten bleiben immer
# ---------------------------------------------------------------------------

_CRITICAL_KEYWORDS: frozenset[str] = frozenset({
    "merk dir", "merke dir", "nicht vergessen", "wichtig:", "entscheidung:",
    "ziel:", "fakt:", "remember", "important", "never forget", "decision:",
})


# ---------------------------------------------------------------------------
# CompactionStrategy ABC
# ---------------------------------------------------------------------------


class CompactionStrategy(ABC):
    """Interface fuer Context-Compaction-Strategien.

    Eine Strategie entscheidet welche Turns verdichtet werden und wie.
    Alle Implementierungen muessen thread-safe und zustandslos sein —
    der Zustand liegt in Turns/History, nicht in der Strategie.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Eindeutiger Name der Strategie (fuer Registry-Lookup)."""

    @abstractmethod
    async def should_compact(
        self,
        history: ContextHistory,
        budget: ResourceBudget,
    ) -> bool:
        """Ist Compaction jetzt noetig?

        Typisches Kriterium: Anzahl Tokens ueberschreitet X% des Budgets.
        """

    @abstractmethod
    async def compact(
        self,
        turns: list[Turn],
        budget: ResourceBudget,
    ) -> CompactionResult:
        """Kern-Methode: verdichtet die Turn-Liste.

        Gibt CompactionResult zurueck — kept_turns + optional summary.
        Kritische Turns duerfen NIEMALS in dropped_turns landen.
        """

    @abstractmethod
    async def extract_critical(self, turns: list[Turn]) -> list[Turn]:
        """Welche Turns duerfen niemals weg?

        Kriterien: enthaelt Fakt, Entscheidung, Ziel, User-Praeferenz.
        Default-Implementierung prueft _CRITICAL_KEYWORDS.
        """

    @abstractmethod
    async def summarize(self, turns: list[Turn]) -> str:
        """Erstellt ein Destillat der uebergebenen Turns.

        In HNZ-002: einfache textuelle Zusammenfassung.
        In HNZ-003: LLM-basiert.
        """


# ---------------------------------------------------------------------------
# RollingSessionPolicy ABC
# ---------------------------------------------------------------------------


class RollingSessionPolicy(ABC):
    """Interface fuer Rolling-Session-Entscheidungen.

    Entscheidet wann eine Session zu gross wird und erzeugt den
    HandoverContext fuer die neue Session.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Eindeutiger Name der Policy (fuer Registry-Lookup)."""

    @abstractmethod
    def should_roll(self, session: Session, budget: ResourceBudget) -> bool:
        """Ist es Zeit fuer eine neue Session?

        Synchron — darf keinen IO machen.
        Typisch: turn_count > N oder tokens > X% des Budgets.
        """

    @abstractmethod
    async def create_handover(
        self,
        session: Session,
        compaction: CompactionResult,
    ) -> HandoverContext:
        """Erzeugt den Uebergabe-Context fuer die neue Session."""


# ---------------------------------------------------------------------------
# Hilfsfunktion: Kritikalitaet pruefen
# ---------------------------------------------------------------------------


def _is_critical(turn: Turn) -> bool:
    """True wenn der Turn kritische Keywords enthaelt."""
    text = " ".join([
        turn.raw_input,
        turn.final_response,
    ]).lower()
    return any(kw in text for kw in _CRITICAL_KEYWORDS)


# ---------------------------------------------------------------------------
# SummarizingCompactionStrategy — DEFAULT (Claude-Stil)
# ---------------------------------------------------------------------------


class SummarizingCompactionStrategy(CompactionStrategy):
    """Compaction nach Claude-Vorbild: recency window + summary.

    Behaelt die letzten ``recency_window`` Turns verbatim sowie alle
    kritischen Turns. Den Rest verdichtet sie zu einem Summary-String
    — kein Datenverlust, nur Komprimierung.
    """

    def __init__(self, recency_window: int = 10) -> None:
        self._recency_window = recency_window

    @property
    def name(self) -> str:
        return "summarizing"

    async def should_compact(
        self,
        history: ContextHistory,
        budget: ResourceBudget,
    ) -> bool:
        ctx = history.current
        used = ctx.token_usage.total_tokens if ctx.token_usage else 0
        threshold = int(budget.max_tokens * 0.80)
        return used >= threshold

    async def extract_critical(self, turns: list[Turn]) -> list[Turn]:
        return [t for t in turns if _is_critical(t)]

    async def summarize(self, turns: list[Turn]) -> str:
        if not turns:
            return ""
        n = len(turns)
        topics = []
        for t in turns:
            msg = t.raw_input or ""
            if msg:
                topics.append(msg[:60].strip())
        topic_str = "; ".join(topics[:5])
        suffix = f" (und {n - 5} weitere)" if n > 5 else ""

        return f"{n} Turns zusammengefasst: {topic_str}{suffix}"

    async def compact(
        self,
        turns: list[Turn],
        budget: ResourceBudget,
    ) -> CompactionResult:
        if not turns:
            return CompactionResult()

        critical = await self.extract_critical(turns)
        critical_ids = {id(t) for t in critical}

        # Letzte recency_window Turns immer verbatim behalten
        recent = turns[-self._recency_window:]
        recent_ids = {id(t) for t in recent}

        kept_ids = recent_ids | critical_ids
        kept = [t for t in turns if id(t) in kept_ids]
        dropped = [t for t in turns if id(t) not in {id(k) for k in kept}]

        summary = await self.summarize(dropped) if dropped else None

        # Naive Token-Schaetzung: 4 Zeichen ~ 1 Token
        def _est_tokens(ts: list[Turn]) -> int:
            return sum(
                len(t.raw_input) + len(t.final_response)
                for t in ts
            ) // 4

        tokens_before = _est_tokens(turns)
        tokens_after = _est_tokens(kept)

        return CompactionResult(
            kept_turns=tuple(kept),
            dropped_turns=tuple(dropped),
            summary=summary,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_before - tokens_after,
            critical_preserved=True,
        )


# ---------------------------------------------------------------------------
# TruncationCompactionStrategy — explizit verlustbehaftet
# ---------------------------------------------------------------------------


class TruncationCompactionStrategy(CompactionStrategy):
    """FIFO-Compaction: aelteste nicht-kritische Turns werden geloescht.

    WARNUNG: Diese Strategie ist verlustbehaftet — geloeschte Turns
    sind unwiederbringlich weg. Fuer Produktionseinsatz wird
    SummarizingCompactionStrategy empfohlen.
    """

    def __init__(self, keep_last: int = 20) -> None:
        self._keep_last = keep_last

    @property
    def name(self) -> str:
        return "truncation"

    async def should_compact(
        self,
        history: ContextHistory,
        budget: ResourceBudget,
    ) -> bool:
        ctx = history.current
        used = ctx.token_usage.total_tokens if ctx.token_usage else 0
        return used >= int(budget.max_tokens * 0.80)

    async def extract_critical(self, turns: list[Turn]) -> list[Turn]:
        return [t for t in turns if _is_critical(t)]

    async def summarize(self, turns: list[Turn]) -> str:
        return f"{len(turns)} turns omitted"

    async def compact(
        self,
        turns: list[Turn],
        budget: ResourceBudget,
    ) -> CompactionResult:
        if not turns:
            return CompactionResult()

        critical = await self.extract_critical(turns)
        critical_ids = {id(t) for t in critical}

        recent = turns[-self._keep_last:]
        recent_ids = {id(t) for t in recent}

        kept_ids = recent_ids | critical_ids
        kept = [t for t in turns if id(t) in kept_ids]
        dropped = [t for t in turns if id(t) not in {id(k) for k in kept}]

        logger.warning(
            "TruncationCompactionStrategy: %d turns verworfen"
            " (verlustbehaftet).",
            len(dropped),
        )

        def _est_tokens(ts: list[Turn]) -> int:
            return sum(
                len(t.raw_input) + len(t.final_response)
                for t in ts
            ) // 4

        tokens_before = _est_tokens(turns)
        tokens_after = _est_tokens(kept)

        return CompactionResult(
            kept_turns=tuple(kept),
            dropped_turns=tuple(dropped),
            summary=None,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_before - tokens_after,
            critical_preserved=True,
        )


# ---------------------------------------------------------------------------
# NoopRollingSessionPolicy — DEFAULT
# ---------------------------------------------------------------------------


class NoopRollingSessionPolicy(RollingSessionPolicy):
    """Rolling-Session-Policy die niemals rollt.

    Default fuer einfache Setups ohne automatischen Session-Wechsel.
    """

    @property
    def name(self) -> str:
        return "noop"

    def should_roll(self, session: Session, budget: ResourceBudget) -> bool:
        return False

    async def create_handover(
        self,
        session: Session,
        compaction: CompactionResult,
    ) -> HandoverContext:
        return HandoverContext(
            from_session_id=session.id,
            summary="",
        )


# ---------------------------------------------------------------------------
# CompactionRegistry
# ---------------------------------------------------------------------------


class CompactionRegistry:
    """Singleton-Registry fuer CompactionStrategy-Implementierungen.

    Custom-Strategie registrieren:
        CompactionRegistry.register(MyStrategy())
        CompactionRegistry.set_default("my_strategy")
    """

    _strategies: dict[str, CompactionStrategy] = {}
    _default: str = "summarizing"

    @classmethod
    def register(cls, strategy: CompactionStrategy) -> None:
        """Strategie registrieren."""
        cls._strategies[strategy.name] = strategy

    @classmethod
    def get(cls, name: str) -> CompactionStrategy | None:
        """Strategie per Name holen."""
        return cls._strategies.get(name)

    @classmethod
    def list_available(cls) -> list[str]:
        """Alle registrierten Strategien."""
        return list(cls._strategies.keys())

    @classmethod
    def set_default(cls, name: str) -> None:
        """Standard-Strategie setzen."""
        if name not in cls._strategies:
            raise KeyError(f"Strategie '{name}' nicht registriert.")
        cls._default = name

    @classmethod
    def get_default(cls) -> CompactionStrategy:
        """Standard-Strategie holen."""
        return cls._strategies[cls._default]


# ---------------------------------------------------------------------------
# RollingSessionRegistry
# ---------------------------------------------------------------------------


class RollingSessionRegistry:
    """Singleton-Registry fuer RollingSessionPolicy-Implementierungen."""

    _policies: dict[str, RollingSessionPolicy] = {}
    _default: str = "noop"

    @classmethod
    def register(cls, policy: RollingSessionPolicy) -> None:
        cls._policies[policy.name] = policy

    @classmethod
    def get(cls, name: str) -> RollingSessionPolicy | None:
        return cls._policies.get(name)

    @classmethod
    def list_available(cls) -> list[str]:
        return list(cls._policies.keys())

    @classmethod
    def set_default(cls, name: str) -> None:
        if name not in cls._policies:
            raise KeyError(f"Policy '{name}' nicht registriert.")
        cls._default = name

    @classmethod
    def get_default(cls) -> RollingSessionPolicy:
        return cls._policies[cls._default]


# ---------------------------------------------------------------------------
# Defaults beim Import registrieren
# ---------------------------------------------------------------------------

CompactionRegistry.register(SummarizingCompactionStrategy())
CompactionRegistry.register(TruncationCompactionStrategy())
RollingSessionRegistry.register(NoopRollingSessionPolicy())
