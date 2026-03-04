"""Tests fuer ReasoningStrategy Interface + PassthroughStrategy
+ StrategyRegistry.

HNZ-002-0009 — Compliance-Tests, Registry-Tests, Runner.set_strategy().

Struktur:
  - assert_strategy_compliance(): Compliance-Fixture fuer HNZ-003+ Strategien
  - DummyStrategy: minimale Test-Impl (nicht fuer Produktion)
  - TestPassthroughStrategy: Verhalten der Default-Impl
  - TestStrategyRegistry: Singleton-Verhalten
  - TestRunnerSetStrategy: Laufzeit-Wechsel via Runner
"""

from __future__ import annotations

import pytest

from core.runner import Runner, LLMProvider
from core.models import PipelineContext
from core.models.context import ContextHistory
from core.models.placeholders import Reflection, StepPlan
from core.reasoning import (
    PassthroughStrategy,
    ReasoningStrategy,
    StrategyFeedback,
    StrategyMetrics,
    StrategyRegistry,
    ToolResultAssessment,
)
from core.models.base import ToolResult


# ---------------------------------------------------------------------------
# Hilfsmittel
# ---------------------------------------------------------------------------


class MockProvider(LLMProvider):
    """Minimaler Provider fuer Runner-Tests."""

    def __init__(self, response: str = "ok") -> None:
        self._response = response

    async def chat(self, messages, system_prompt="", model="") -> str:
        return self._response

    async def stream(self, messages, system_prompt="", model=""):
        yield self._response


class DummyStrategy(ReasoningStrategy):
    """Minimale Strategy-Impl fuer Registry-Tests.

    Implementiert alle abstrakten Methoden mit sinnvollen Defaults.
    Nicht fuer Produktion geeignet — nur fuer Tests.
    """

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Test-Strategie fuer Registry- und Compliance-Tests."

    async def initialize(self, ctx, history) -> PipelineContext:
        return ctx

    async def should_continue(self, ctx, history) -> bool:
        return False

    async def plan_next_step(self, ctx, history) -> StepPlan:
        return StepPlan(next_action="respond")

    async def reflect(self, ctx, history) -> Reflection:
        return Reflection(step_useful=True, confidence=1.0)

    async def adapt(self, feedback: StrategyFeedback) -> None:
        pass

    async def metrics(self, ctx, history) -> StrategyMetrics:
        return StrategyMetrics(
            iterations=0,
            history_depth=len(history._snapshots),
        )

    async def on_tool_result(  # noqa: E501
        self, ctx, result, history
    ) -> ToolResultAssessment:
        return ToolResultAssessment(verdict="sufficient")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> PipelineContext:
    return PipelineContext(user_input="test")


@pytest.fixture
def history(ctx: PipelineContext) -> ContextHistory:
    h = ContextHistory()
    h.push(ctx)
    return h


@pytest.fixture
def strategy() -> PassthroughStrategy:
    return PassthroughStrategy()


@pytest.fixture
def make_runner():
    def _make(response: str = "ok") -> tuple[Runner, MockProvider]:
        provider = MockProvider(response)
        heinzel = Runner(provider=provider, name="test-reasoning")
        return heinzel, provider
    return _make


# ---------------------------------------------------------------------------
# Compliance-Fixture (wiederverwendbar fuer HNZ-003+ Strategien)
# ---------------------------------------------------------------------------


async def assert_strategy_compliance(
    strategy: ReasoningStrategy,
    ctx: PipelineContext,
    history: ContextHistory,
) -> None:
    """Prueft Protokoll-Compliance einer ReasoningStrategy-Implementierung.

    Jede Strategie die in HNZ-003+ gebaut wird, kann dieses Fixture
    nutzen um sicherzustellen dass sie das Interface korrekt implementiert.

    Beispiel in HNZ-003:
        @pytest.mark.asyncio
        async def test_mystrategy_compliance(ctx, history):
            await assert_strategy_compliance(MyStrategy(), ctx, history)
    """
    # Properties muessen nicht-leere Strings sein
    assert isinstance(strategy.name, str) and strategy.name, \
        "name muss nicht-leerer str sein"
    assert isinstance(strategy.version, str) and strategy.version, \
        "version muss nicht-leerer str sein"
    assert isinstance(strategy.description, str), \
        "description muss str sein"

    # initialize gibt PipelineContext zurueck
    new_ctx = await strategy.initialize(ctx, history)
    assert isinstance(new_ctx, PipelineContext), \
        "initialize() muss PipelineContext zurueckgeben"

    # should_continue gibt bool zurueck
    result = await strategy.should_continue(ctx, history)
    assert isinstance(result, bool), \
        "should_continue() muss bool zurueckgeben"

    # plan_next_step gibt gueltigen StepPlan zurueck
    plan = await strategy.plan_next_step(ctx, history)
    assert isinstance(plan, StepPlan), \
        "plan_next_step() muss StepPlan zurueckgeben"
    assert plan.next_action in ("think", "tool", "respond"), \
        f"next_action ungueltig: '{plan.next_action}'"

    # reflect gibt (Reflection, ctx) zurueck mit gueltiger confidence
    ref, _ctx = await strategy.reflect(ctx, history)
    assert isinstance(ref, Reflection), \
        "reflect() muss Reflection zurueckgeben"
    assert 0.0 <= ref.confidence <= 1.0, \
        f"Reflection.confidence muss 0.0-1.0 sein, nicht {ref.confidence}"

    # metrics gibt StrategyMetrics zurueck
    m = await strategy.metrics(ctx, history)
    assert isinstance(m, StrategyMetrics), \
        "metrics() muss StrategyMetrics zurueckgeben"

    # on_tool_result gibt ToolResultAssessment zurueck
    tool_result = ToolResult(call_id="t1")
    assessment = await strategy.on_tool_result(ctx, tool_result, history)
    assert isinstance(assessment, ToolResultAssessment), \
        "on_tool_result() muss ToolResultAssessment zurueckgeben"
    assert assessment.verdict in (
        "sufficient", "needs_retry", "try_alternative", "abort"
    ), f"ToolResultAssessment.verdict ungueltig: '{assessment.verdict}'"


# ---------------------------------------------------------------------------
# TestPassthroughStrategy
# ---------------------------------------------------------------------------


class TestPassthroughStrategy:

    def test_passthrough_name(self, strategy):
        assert strategy.name == "passthrough"

    def test_passthrough_version(self, strategy):
        assert strategy.version == "1.0.0"

    def test_passthrough_description_is_str(self, strategy):
        assert isinstance(strategy.description, str)
        assert strategy.description  # nicht leer

    @pytest.mark.asyncio
    async def test_passthrough_should_continue_is_false(  # noqa: E501
        self, strategy, ctx, history
    ):
        """PassthroughStrategy: kein Reasoning-Loop, immer False.

        ctx.loop_done ist operative Ebene — wird in der Pipeline
        separat geprueft, nicht von der Strategy.
        """
        assert await strategy.should_continue(ctx, history) is False
        assert await strategy.should_continue(ctx.evolve(loop_done=True), history) is False

    @pytest.mark.asyncio
    async def test_passthrough_plan_next_step_returns_respond(  # noqa: E501
        self, strategy, ctx, history
    ):
        plan = await strategy.plan_next_step(ctx, history)
        assert isinstance(plan, StepPlan)
        assert plan.next_action == "respond"

    @pytest.mark.asyncio
    async def test_passthrough_reflect_returns_useful(  # noqa: E501
        self, strategy, ctx, history
    ):
        ref = await strategy.reflect(ctx, history)
        assert isinstance(ref, Reflection)
        assert ref.step_useful is True
        assert ref.confidence == 1.0

    @pytest.mark.asyncio
    async def test_passthrough_adapt_is_noop(self, strategy):
        fb = StrategyFeedback(session_id="s1", success=True, iterations_used=1)
        # Kein Fehler, kein Rueckgabewert
        result = await strategy.adapt(fb)
        assert result is None

    @pytest.mark.asyncio
    async def test_passthrough_metrics_iterations_one(  # noqa: E501
        self, strategy, ctx, history
    ):
        m = await strategy.metrics(ctx, history)
        assert isinstance(m, StrategyMetrics)
        assert m.iterations == 1

    @pytest.mark.asyncio
    async def test_passthrough_metrics_history_depth(  # noqa: E501
        self, strategy, ctx, history
    ):
        m = await strategy.metrics(ctx, history)
        assert m.history_depth >= 1

    @pytest.mark.asyncio
    async def test_passthrough_on_tool_result_sufficient(  # noqa: E501
        self, strategy, ctx, history
    ):
        result = ToolResult(call_id="t1")
        assessment = await strategy.on_tool_result(ctx, result, history)
        assert isinstance(assessment, ToolResultAssessment)
        assert assessment.verdict == "sufficient"

    @pytest.mark.asyncio
    async def test_passthrough_initialize_returns_same_ctx(  # noqa: E501
        self, strategy, ctx, history
    ):
        new_ctx = await strategy.initialize(ctx, history)
        # PassthroughStrategy veraendert den ctx nicht
        assert new_ctx is ctx

    @pytest.mark.asyncio
    async def test_passthrough_compliance(self, strategy, ctx, history):
        """Compliance-Pruefung via assert_strategy_compliance."""
        await assert_strategy_compliance(strategy, ctx, history)


# ---------------------------------------------------------------------------
# TestStrategyRegistry
# ---------------------------------------------------------------------------


class TestStrategyRegistry:

    def setup_method(self):
        """Sicherstellen dass passthrough der Default ist vor jedem Test."""
        StrategyRegistry.set_default("passthrough")
        # Dummy entfernen falls von vorherigem Test registriert
        StrategyRegistry._strategies.pop("dummy", None)

    def teardown_method(self):
        """Aufraumen: Default zurueck auf passthrough, dummy entfernen."""
        StrategyRegistry._strategies.pop("dummy", None)
        StrategyRegistry.set_default("passthrough")

    def test_passthrough_is_registered_at_import(self):
        assert StrategyRegistry.get("passthrough") is not None

    def test_passthrough_is_default(self):
        s = StrategyRegistry.get_default()
        assert s.name == "passthrough"

    def test_register_and_get(self):
        StrategyRegistry.register(DummyStrategy())
        s = StrategyRegistry.get("dummy")
        assert s is not None
        assert s.name == "dummy"

    def test_list_available_contains_passthrough(self):
        assert "passthrough" in StrategyRegistry.list_available()

    def test_list_available_returns_list(self):
        result = StrategyRegistry.list_available()
        assert isinstance(result, list)

    def test_set_default_and_get_default(self):
        StrategyRegistry.register(DummyStrategy())
        StrategyRegistry.set_default("dummy")
        assert StrategyRegistry.get_default().name == "dummy"
        # zurueck auf passthrough
        StrategyRegistry.set_default("passthrough")
        assert StrategyRegistry.get_default().name == "passthrough"

    def test_set_default_unknown_raises_key_error(self):
        with pytest.raises(KeyError):
            StrategyRegistry.set_default("gibts_nicht_xyz")

    def test_get_unknown_returns_none(self):
        assert StrategyRegistry.get("unknown_xyz_abc") is None

    def test_register_overwrites_existing(self):
        """Nochmaliges Registrieren unter gleichem Namen ueberschreibt."""
        s1 = DummyStrategy()
        s2 = DummyStrategy()
        StrategyRegistry.register(s1)
        StrategyRegistry.register(s2)
        assert StrategyRegistry.get("dummy") is s2


# ---------------------------------------------------------------------------
# TestRunnerSetStrategy
# ---------------------------------------------------------------------------


class TestRunnerSetStrategy:

    def setup_method(self):
        StrategyRegistry.set_default("passthrough")
        StrategyRegistry._strategies.pop("dummy", None)

    def teardown_method(self):
        StrategyRegistry._strategies.pop("dummy", None)
        StrategyRegistry.set_default("passthrough")

    def test_default_strategy_is_passthrough(self, make_runner):
        heinzel, _ = make_runner()
        assert heinzel.reasoning_strategy.name == "passthrough"

    def test_set_strategy_by_name(self, make_runner):
        heinzel, _ = make_runner()
        StrategyRegistry.register(DummyStrategy())
        heinzel.set_strategy("dummy")
        assert heinzel.reasoning_strategy.name == "dummy"

    def test_set_strategy_by_object(self, make_runner):
        heinzel, _ = make_runner()
        heinzel.set_strategy(DummyStrategy())
        assert heinzel.reasoning_strategy.name == "dummy"

    def test_set_strategy_by_object_registers_in_registry(self, make_runner):
        heinzel, _ = make_runner()
        heinzel.set_strategy(DummyStrategy())
        # Strategie muss jetzt in der Registry sein
        assert StrategyRegistry.get("dummy") is not None

    def test_set_strategy_unknown_name_raises_key_error(self, make_runner):
        heinzel, _ = make_runner()
        with pytest.raises(KeyError):
            heinzel.set_strategy("nicht_registriert_xyz")

    def test_set_strategy_back_to_passthrough(self, make_runner):
        heinzel, _ = make_runner()
        StrategyRegistry.register(DummyStrategy())
        heinzel.set_strategy("dummy")
        heinzel.set_strategy("passthrough")
        assert heinzel.reasoning_strategy.name == "passthrough"

    def test_two_heinzel_independent_strategies(self, make_runner):
        """Zwei Heinzel-Instanzen haben unabhaengige Strategien."""
        h1, _ = make_runner()
        h2, _ = make_runner()
        StrategyRegistry.register(DummyStrategy())
        h1.set_strategy("dummy")
        # h2 unveraendert
        assert h2.reasoning_strategy.name == "passthrough"

    @pytest.mark.asyncio
    async def test_dummy_strategy_compliance(self, ctx, history):
        """DummyStrategy erfuellt das Compliance-Interface."""
        await assert_strategy_compliance(DummyStrategy(), ctx, history)
