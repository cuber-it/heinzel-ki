"""StrategySelector — automatische Strategy-Auswahl.

Architektur:
    StrategySelector (ABC)
        HeuristicSelector  — nur YAML-Regeln, kein LLM-Call
        HybridSelector     — Heuristik + LLM-Fallback (Produktion)

Injectable in Runner. Austausch = eine Zeile.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .models.context import PipelineContext
    from .provider import LLMProvider

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config" / "selector_config.yaml"


# =============================================================================
# Config
# =============================================================================

class SelectorConfig:
    """YAML-Config mit mtime-basiertem Cache (hot-reload bei Dateiänderung)."""

    def __init__(self, path: Path = _DEFAULT_CONFIG) -> None:
        self._path = path
        self._cache: dict = {}
        self._cached_mtime: float = 0.0

    def load(self) -> dict:
        try:
            mtime = self._path.stat().st_mtime
            if mtime != self._cached_mtime:
                self._cache = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
                self._cached_mtime = mtime
            return self._cache
        except FileNotFoundError:
            return self._cache or {}
        except Exception as e:
            logger.warning("selector_config.yaml nicht lesbar: %s — Defaults", e)
            return self._cache or {}

    @property
    def simple_max_len(self) -> int:
        return int(self.load().get("simple_max_len", 40))

    @property
    def complex_min_len(self) -> int:
        return int(self.load().get("complex_min_len", 300))

    @property
    def simple_patterns(self) -> list[str]:
        return self.load().get("simple_patterns", [
            "kannst du", "bist du", "was ist", "wer ist",
            "hallo", "hi ", "hey ", "danke", "ok", "ja", "nein",
        ])

    @property
    def complex_keywords(self) -> list[str]:
        return self.load().get("complex_keywords", [
            "analysiere", "vergleiche", "warum", "erkläre im detail",
            "optimiere", "architektur", "strategie", "konzept",
            "bewerte", "diskutiere", "pros und cons",
        ])

    @property
    def strategy_map(self) -> dict[str, str]:
        return self.load().get("strategy_map", {
            "simple": "passthrough",
            "medium": "chain_of_thought",
            "complex": "deep_reasoning",
        })

    @property
    def fallback_strategy(self) -> str:
        return self.load().get("fallback_strategy", "chain_of_thought")


# =============================================================================
# ABC
# =============================================================================

class StrategySelector(ABC):
    """Wählt die passende Strategy für einen gegebenen Input."""

    @abstractmethod
    async def select(
        self,
        ctx: "PipelineContext",
        provider: "LLMProvider | None" = None,
    ) -> str:
        """Strategy-Name zurückgeben ('passthrough', 'chain_of_thought', 'deep_reasoning')."""


# =============================================================================
# HeuristicSelector
# =============================================================================

class HeuristicSelector(StrategySelector):
    """Nur regelbasiert, kein LLM-Call. Gut für Tests und als Baseline."""

    def __init__(self, config: SelectorConfig | None = None) -> None:
        self._cfg = config or SelectorConfig()

    def classify(self, text: str) -> str | None:
        """Klassifiziert text als 'simple'/'complex' oder None wenn unklar."""
        t = text.strip().lower()

        # Eindeutig simpel
        if len(t) <= self._cfg.simple_max_len:
            return "simple"
        if any(p in t for p in self._cfg.simple_patterns):
            return "simple"

        # Eindeutig komplex
        if len(t) >= self._cfg.complex_min_len:
            return "complex"
        if any(k in t for k in self._cfg.complex_keywords):
            return "complex"

        return None  # unklar

    async def select(
        self,
        ctx: "PipelineContext",
        provider: "LLMProvider | None" = None,
    ) -> str:
        level = self.classify(ctx.raw_input)
        strategy = self._cfg.strategy_map.get(level or "medium", self._cfg.fallback_strategy)
        logger.debug("HeuristicSelector: '%s' → %s (level=%s)", ctx.raw_input[:40], strategy, level)
        return strategy


# =============================================================================
# HybridSelector
# =============================================================================

_CLASSIFY_SYSTEM = (
    "Classify the complexity of the user's request. "
    "Reply with exactly one word: simple, medium, or complex. "
    "simple = yes/no, greetings, trivial facts. "
    "medium = explanation, how-to, moderate analysis. "
    "complex = deep analysis, architecture, comparison, optimization, strategy."
)

_VALID_LEVELS = {"simple", "medium", "complex"}


class HybridSelector(StrategySelector):
    """Heuristik zuerst — LLM-Fallback wenn unklar. Produktion-Default."""

    def __init__(
        self,
        config: SelectorConfig | None = None,
        feedback_store: "FeedbackStore | None" = None,
    ) -> None:
        self._cfg = config or SelectorConfig()
        self._heuristic = HeuristicSelector(self._cfg)
        self._feedback = feedback_store

    async def select(
        self,
        ctx: "PipelineContext",
        provider: "LLMProvider | None" = None,
    ) -> str:
        from .feedback_store import SelectionEvent
        import time

        text = ctx.raw_input
        heuristic_result = self._heuristic.classify(text)

        if heuristic_result is not None:
            # Heuristik eindeutig
            strategy = self._cfg.strategy_map.get(heuristic_result, self._cfg.fallback_strategy)
            logger.debug("HybridSelector (heuristic): '%s' → %s", text[:40], strategy)
            if self._feedback:
                await self._feedback.log(SelectionEvent(
                    input_preview=text[:80],
                    heuristic_result=heuristic_result,
                    llm_result=None,
                    final_strategy=strategy,
                    session_id=ctx.session_id,
                ))
            return strategy

        # LLM-Fallback
        llm_result = None
        if provider is not None:
            try:
                response = await provider.chat(
                    messages=[{"role": "user", "content": text}],
                    system_prompt=_CLASSIFY_SYSTEM,
                    model=getattr(provider, "current_model", ""),
                )
                word = re.search(r"\b(simple|medium|complex)\b", response.lower())
                if word:
                    llm_result = word.group(1)
            except Exception as e:
                logger.warning("HybridSelector LLM-Fallback fehlgeschlagen: %s", e)

        level = llm_result or "medium"
        strategy = self._cfg.strategy_map.get(level, self._cfg.fallback_strategy)
        logger.debug(
            "HybridSelector (llm=%s): '%s' → %s",
            llm_result, text[:40], strategy,
        )

        if self._feedback:
            await self._feedback.log(SelectionEvent(
                input_preview=text[:80],
                heuristic_result=None,
                llm_result=llm_result,
                final_strategy=strategy,
                session_id=ctx.session_id,
            ))

        return strategy


# Re-export für einfachen Import
__all__ = [
    "StrategySelector",
    "HeuristicSelector",
    "HybridSelector",
    "SelectorConfig",
]
