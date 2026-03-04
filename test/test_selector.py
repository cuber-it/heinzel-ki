"""Tests für StrategySelector und FeedbackStore."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.selector import HeuristicSelector, HybridSelector, SelectorConfig
from core.feedback_store import NoopFeedbackStore, SelectionEvent, SqliteFeedbackStore
from core.models.context import PipelineContext


def _ctx(text: str) -> PipelineContext:
    return PipelineContext(raw_input=text, session_id="test-session")


# =============================================================================
# HeuristicSelector
# =============================================================================

class TestHeuristicSelector:

    def setup_method(self):
        self.sel = HeuristicSelector()

    def test_simpel_kurz(self):
        assert self.sel.classify("hallo") == "simple"

    def test_simpel_pattern(self):
        assert self.sel.classify("kannst du mir helfen mit diesem problem hier") == "simple"

    def test_komplex_keyword(self):
        assert self.sel.classify("analysiere die Architektur und erkläre die Vor- und Nachteile") == "complex"

    def test_komplex_lang(self):
        langer_text = "x" * 310
        assert self.sel.classify(langer_text) == "complex"

    def test_unklar_medium(self):
        assert self.sel.classify("wie installiere ich flutter unter linux auf meinem rechner") is None

    @pytest.mark.asyncio
    async def test_select_gibt_strategy_name(self):
        strategy = await self.sel.select(_ctx("hallo"))
        assert strategy == "passthrough"

    @pytest.mark.asyncio
    async def test_select_komplex(self):
        strategy = await self.sel.select(_ctx("analysiere die performance meiner pipeline"))
        assert strategy == "deep_reasoning"

    @pytest.mark.asyncio
    async def test_select_unklar_fallback(self):
        strategy = await self.sel.select(_ctx("wie installiere ich flutter unter linux auf meinem rechner"))
        assert strategy == "chain_of_thought"  # fallback


# =============================================================================
# HybridSelector
# =============================================================================

class TestHybridSelector:

    @pytest.mark.asyncio
    async def test_heuristik_kein_llm_call(self):
        feedback = NoopFeedbackStore()
        sel = HybridSelector(feedback_store=feedback)
        mock_provider = AsyncMock()

        result = await sel.select(_ctx("hallo"), mock_provider)

        assert result == "passthrough"
        mock_provider.chat.assert_not_called()  # kein LLM-Call
        assert len(feedback.events) == 1
        assert feedback.events[0].heuristic_result == "simple"

    @pytest.mark.asyncio
    async def test_llm_fallback_bei_unklar(self):
        feedback = NoopFeedbackStore()
        sel = HybridSelector(feedback_store=feedback)
        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(return_value="medium")
        mock_provider.default_model = "claude-test"

        result = await sel.select(_ctx("wie installiere ich flutter unter linux auf meinem rechner"), mock_provider)

        assert result == "chain_of_thought"
        mock_provider.chat.assert_called_once()
        assert feedback.events[0].llm_result == "medium"

    @pytest.mark.asyncio
    async def test_llm_fallback_bei_fehler(self):
        sel = HybridSelector()
        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(side_effect=Exception("Timeout"))
        mock_provider.default_model = "claude-test"

        result = await sel.select(_ctx("wie installiere ich das eigentlich auf meinem rechner"), mock_provider)
        assert result == "chain_of_thought"  # fallback_strategy

    @pytest.mark.asyncio
    async def test_kein_provider_fallback(self):
        sel = HybridSelector()
        result = await sel.select(_ctx("wie installiere ich das eigentlich auf meinem rechner"), provider=None)
        assert result == "chain_of_thought"


# =============================================================================
# FeedbackStore
# =============================================================================

class TestNoopFeedbackStore:

    @pytest.mark.asyncio
    async def test_log_speichert(self):
        store = NoopFeedbackStore()
        await store.log(SelectionEvent(
            input_preview="test",
            final_strategy="passthrough",
            heuristic_result="simple",
        ))
        assert len(store.events) == 1

    @pytest.mark.asyncio
    async def test_stats_leer(self):
        store = NoopFeedbackStore()
        assert await store.get_stats() == []


class TestSqliteFeedbackStore:

    @pytest.mark.asyncio
    async def test_log_und_stats(self, tmp_path):
        store = SqliteFeedbackStore(db_path=tmp_path / "test.db")
        await store.log(SelectionEvent(
            input_preview="hallo",
            final_strategy="passthrough",
            heuristic_result="simple",
            session_id="s1",
        ))
        await store.log(SelectionEvent(
            input_preview="analysiere",
            final_strategy="deep_reasoning",
            heuristic_result="complex",
            session_id="s1",
        ))
        stats = await store.get_stats()
        strategies = {s["final_strategy"] for s in stats}
        assert "passthrough" in strategies
        assert "deep_reasoning" in strategies

    @pytest.mark.asyncio
    async def test_override_wird_geloggt(self, tmp_path):
        store = SqliteFeedbackStore(db_path=tmp_path / "test.db")
        await store.log(SelectionEvent(
            input_preview="test",
            final_strategy="chain_of_thought",
            session_id="s2",
        ))
        await store.log_override("s2", "deep_reasoning")
        stats = await store.get_stats()
        overridden = sum(s["overridden"] for s in stats)
        assert overridden == 1
