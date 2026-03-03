"""Tests fuer heinzel_core.compaction."""

from __future__ import annotations

import pytest

from core.compaction import (
    CompactionRegistry,
    RollingSessionRegistry,
    SummarizingCompactionStrategy,
    TruncationCompactionStrategy,
    NoopRollingSessionPolicy,
    _is_critical,
)
from core.models.placeholders import ResourceBudget
from core.session import Turn, Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_turn(raw_input: str = "Hallo", final_response: str = "Hi") -> Turn:
    return Turn(
        session_id="test-session",
        raw_input=raw_input,
        final_response=final_response,
    )


def make_session(turn_count: int = 5) -> Session:
    return Session(heinzel_id="h1", turn_count=turn_count)


BUDGET = ResourceBudget(max_tokens=100_000)


# ---------------------------------------------------------------------------
# _is_critical
# ---------------------------------------------------------------------------


class TestIsCritical:
    def test_critical_keyword_in_input(self):
        turn = make_turn(raw_input="merk dir: ich heisse Ulrich")
        assert _is_critical(turn) is True

    def test_critical_keyword_in_response(self):
        turn = make_turn(final_response="Entscheidung: wir nehmen Plan B")
        assert _is_critical(turn) is True

    def test_not_critical(self):
        turn = make_turn(raw_input="wie ist das Wetter?")
        assert _is_critical(turn) is False

    def test_case_insensitive(self):
        turn = make_turn(raw_input="MERK DIR das hier")
        assert _is_critical(turn) is True


# ---------------------------------------------------------------------------
# SummarizingCompactionStrategy
# ---------------------------------------------------------------------------


class TestSummarizingCompactionStrategy:
    @pytest.fixture
    def strategy(self):
        return SummarizingCompactionStrategy(recency_window=3)

    @pytest.mark.asyncio
    async def test_alle_im_recency_window_bleiben(self, strategy):
        turns = [make_turn(f"m{i}") for i in range(3)]
        result = await strategy.compact(turns, BUDGET)
        assert len(result.kept_turns) == 3
        assert len(result.dropped_turns) == 0

    @pytest.mark.asyncio
    async def test_aelteste_werden_summarized(self, strategy):
        turns = [make_turn(f"m{i}") for i in range(6)]
        result = await strategy.compact(turns, BUDGET)
        # letzte 3 im recency_window
        assert len(result.kept_turns) == 3
        assert result.kept_turns[-1].raw_input == "m5"
        # die 3 aeltesten werden zusammengefasst
        assert len(result.dropped_turns) == 3
        assert result.summary is not None
        assert "3 Turns" in result.summary

    @pytest.mark.asyncio
    async def test_kritische_turns_bleiben_immer(self, strategy):
        critical = make_turn(raw_input="merk dir: wichtige info")
        turns = [make_turn(f"m{i}") for i in range(5)] + [critical]
        result = await strategy.compact(turns, BUDGET)
        kept_inputs = [t.raw_input for t in result.kept_turns]
        assert "merk dir: wichtige info" in kept_inputs

    @pytest.mark.asyncio
    async def test_critical_preserved_flag(self, strategy):
        turns = [make_turn(f"m{i}") for i in range(6)]
        result = await strategy.compact(turns, BUDGET)
        assert result.critical_preserved is True

    @pytest.mark.asyncio
    async def test_tokens_saved_berechnung(self, strategy):
        turns = [make_turn("hallo welt", "ja") for _ in range(6)]
        result = await strategy.compact(turns, BUDGET)
        assert result.tokens_saved >= 0
        assert result.tokens_before >= result.tokens_after

    @pytest.mark.asyncio
    async def test_leere_liste(self, strategy):
        result = await strategy.compact([], BUDGET)
        assert result.kept_turns == ()
        assert result.dropped_turns == ()

    @pytest.mark.asyncio
    async def test_extract_critical(self, strategy):
        turns = [
            make_turn("normaler Turn"),
            make_turn(raw_input="merk dir: das ist wichtig"),
            make_turn("noch ein normaler"),
        ]
        critical = await strategy.extract_critical(turns)
        assert len(critical) == 1
        assert critical[0].raw_input == "merk dir: das ist wichtig"

    @pytest.mark.asyncio
    async def test_summarize(self, strategy):
        turns = [make_turn(f"frage {i}") for i in range(3)]
        summary = await strategy.summarize(turns)
        assert "3 Turns" in summary

    @pytest.mark.asyncio
    async def test_should_compact_unter_threshold(self):
        from unittest.mock import MagicMock
        strategy = SummarizingCompactionStrategy()
        history = MagicMock()
        ctx = MagicMock()
        ctx.token_usage = MagicMock()
        ctx.token_usage.total_tokens = 1000
        history.current = ctx
        budget = ResourceBudget(max_tokens=100_000)
        assert await strategy.should_compact(history, budget) is False

    @pytest.mark.asyncio
    async def test_should_compact_ueber_threshold(self):
        from unittest.mock import MagicMock
        strategy = SummarizingCompactionStrategy()
        history = MagicMock()
        ctx = MagicMock()
        ctx.token_usage = MagicMock()
        ctx.token_usage.total_tokens = 85_000
        history.current = ctx
        budget = ResourceBudget(max_tokens=100_000)
        assert await strategy.should_compact(history, budget) is True


# ---------------------------------------------------------------------------
# TruncationCompactionStrategy
# ---------------------------------------------------------------------------


class TestTruncationCompactionStrategy:
    @pytest.fixture
    def strategy(self):
        return TruncationCompactionStrategy(keep_last=3)

    @pytest.mark.asyncio
    async def test_aelteste_fallen_weg(self, strategy):
        turns = [make_turn(f"m{i}") for i in range(6)]
        result = await strategy.compact(turns, BUDGET)
        assert len(result.kept_turns) == 3
        assert result.kept_turns[-1].raw_input == "m5"

    @pytest.mark.asyncio
    async def test_kritische_bleiben(self, strategy):
        critical = make_turn(raw_input="wichtig: niemals loeschen")
        turns = [make_turn(f"m{i}") for i in range(5)] + [critical]
        result = await strategy.compact(turns, BUDGET)
        kept_inputs = [t.raw_input for t in result.kept_turns]
        assert "wichtig: niemals loeschen" in kept_inputs

    @pytest.mark.asyncio
    async def test_kein_summary(self, strategy):
        turns = [make_turn(f"m{i}") for i in range(6)]
        result = await strategy.compact(turns, BUDGET)
        assert result.summary is None

    @pytest.mark.asyncio
    async def test_leere_liste(self, strategy):
        result = await strategy.compact([], BUDGET)
        assert result.kept_turns == ()


# ---------------------------------------------------------------------------
# NoopRollingSessionPolicy
# ---------------------------------------------------------------------------


class TestNoopRollingSessionPolicy:
    def test_should_roll_always_false(self):
        policy = NoopRollingSessionPolicy()
        session = make_session()
        assert policy.should_roll(session, BUDGET) is False

    @pytest.mark.asyncio
    async def test_create_handover_leer(self):
        policy = NoopRollingSessionPolicy()
        session = make_session()
        from core.compaction import SummarizingCompactionStrategy
        result = await SummarizingCompactionStrategy().compact([], BUDGET)
        handover = await policy.create_handover(session, result)
        assert handover.from_session_id == session.id
        assert handover.summary == ""
        assert handover.critical_turns == ()

    def test_name(self):
        assert NoopRollingSessionPolicy().name == "noop"


# ---------------------------------------------------------------------------
# CompactionRegistry
# ---------------------------------------------------------------------------


class TestCompactionRegistry:
    def test_default_ist_summarizing(self):
        assert CompactionRegistry.get_default().name == "summarizing"

    def test_list_available(self):
        available = CompactionRegistry.list_available()
        assert "summarizing" in available
        assert "truncation" in available

    def test_get_by_name(self):
        s = CompactionRegistry.get("summarizing")
        assert s is not None
        assert s.name == "summarizing"

    def test_get_unknown_returns_none(self):
        assert CompactionRegistry.get("nonexistent") is None

    def test_register_und_get(self):
        class MyStrategy(SummarizingCompactionStrategy):
            @property
            def name(self) -> str:
                return "my_test_strategy"

        CompactionRegistry.register(MyStrategy())
        assert CompactionRegistry.get("my_test_strategy") is not None

    def test_set_default(self):
        CompactionRegistry.set_default("truncation")
        try:
            assert CompactionRegistry.get_default().name == "truncation"
        finally:
            CompactionRegistry.set_default("summarizing")

    def test_set_default_unbekannt_raises(self):
        with pytest.raises(KeyError):
            CompactionRegistry.set_default("not_registered")

    def test_singleton_gleiche_instanz(self):
        r1 = CompactionRegistry.get("summarizing")
        r2 = CompactionRegistry.get("summarizing")
        assert r1 is r2


# ---------------------------------------------------------------------------
# RollingSessionRegistry
# ---------------------------------------------------------------------------


class TestRollingSessionRegistry:
    def test_default_ist_noop(self):
        assert RollingSessionRegistry.get_default().name == "noop"

    def test_register_custom_policy(self):
        class MyPolicy(NoopRollingSessionPolicy):
            @property
            def name(self) -> str:
                return "my_test_policy"

        RollingSessionRegistry.register(MyPolicy())
        assert RollingSessionRegistry.get("my_test_policy") is not None

    def test_set_default_unbekannt_raises(self):
        with pytest.raises(KeyError):
            RollingSessionRegistry.set_default("not_registered")


# ---------------------------------------------------------------------------
# Custom-Addon-Pattern
# ---------------------------------------------------------------------------


class TestCustomAddonPattern:
    """Zeigt wie ein Addon eine eigene Strategie einhaengt."""

    @pytest.mark.asyncio
    async def test_custom_strategy_via_registry(self):
        class AllesWegStrategy(TruncationCompactionStrategy):
            """Behaelt nur den allerletzten Turn."""
            @property
            def name(self) -> str:
                return "alles_weg"

            def __init__(self):
                super().__init__(keep_last=1)

        # Addon registriert und setzt als Default
        CompactionRegistry.register(AllesWegStrategy())
        CompactionRegistry.set_default("alles_weg")
        try:
            turns = [make_turn(f"m{i}") for i in range(5)]
            result = await CompactionRegistry.get_default().compact(
                turns, BUDGET
            )
            assert len(result.kept_turns) == 1
            assert result.kept_turns[0].raw_input == "m4"
        finally:
            CompactionRegistry.set_default("summarizing")
