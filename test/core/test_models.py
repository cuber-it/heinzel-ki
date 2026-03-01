"""Tests für src/core/models/ und src/core/exceptions.py"""

import pytest
from pydantic import ValidationError

from src.core.models import (
    HookPoint,
    Message,
    TokenUsage,
    ToolCall,
    ToolResult,
    MemoryResult,
    ThinkingStep,
    AddOnResult,
    Fact,
    Skill,
    Goal,
    ResourceBudget,
    StepPlan,
    Reflection,
    EvaluationResult,
    PipelineContext,
    ContextDiff,
    ContextHistory,
)
from src.core.exceptions import (
    HeinzelError,
    ProviderError,
    DatabaseError,
    ConfigError,
    SessionError,
    StrategyError,
    AddOnError,
    AddOnDependencyError,
    AddOnLoadError,
    CircuitOpenError,
)


# ─── HookPoint ────────────────────────────────────────────────────────────────

class TestHookPoint:
    def test_all_23_values_present(self):
        expected = {
            "ON_INPUT", "ON_INPUT_PARSED", "ON_MEMORY_QUERY", "ON_MEMORY_HIT",
            "ON_MEMORY_MISS", "ON_CONTEXT_BUILD", "ON_CONTEXT_READY",
            "ON_LLM_REQUEST", "ON_STREAM_CHUNK", "ON_THINKING_STEP", "ON_LLM_RESPONSE",
            "ON_TOOL_REQUEST", "ON_TOOL_RESULT", "ON_TOOL_ERROR",
            "ON_LOOP_ITERATION", "ON_LOOP_END",
            "ON_OUTPUT", "ON_OUTPUT_SENT",
            "ON_STORE", "ON_STORED",
            "ON_SESSION_START", "ON_SESSION_END",
            "ON_ERROR",
        }
        assert {h.name for h in HookPoint} == expected

    def test_count(self):
        assert len(HookPoint) == 23

    def test_is_string_enum(self):
        assert isinstance(HookPoint.ON_INPUT, str)


# ─── PipelineContext ───────────────────────────────────────────────────────────

class TestPipelineContext:
    def test_default_instantiation(self):
        ctx = PipelineContext()
        assert ctx.raw_input == ""
        assert ctx.loop_iteration == 0
        assert ctx.previous is None
        assert ctx.snapshot_id != ""

    def test_frozen_raises_on_mutation(self):
        ctx = PipelineContext(raw_input="test")
        with pytest.raises((ValidationError, TypeError)):
            ctx.raw_input = "geändert"

    def test_evolve_returns_new_snapshot(self):
        ctx = PipelineContext(raw_input="Hallo", session_id="s1")
        ctx2 = ctx.evolve(phase=HookPoint.ON_INPUT_PARSED, parsed_input="Hallo")
        assert ctx2 is not ctx
        assert ctx2.parsed_input == "Hallo"

    def test_evolve_sets_previous(self):
        ctx = PipelineContext()
        ctx2 = ctx.evolve(phase=HookPoint.ON_INPUT_PARSED)
        assert ctx2.previous is ctx

    def test_evolve_generates_new_snapshot_id(self):
        ctx = PipelineContext()
        ctx2 = ctx.evolve(phase=HookPoint.ON_LLM_REQUEST)
        assert ctx2.snapshot_id != ctx.snapshot_id

    def test_evolve_updates_timestamp(self):
        ctx = PipelineContext()
        ctx2 = ctx.evolve(phase=HookPoint.ON_LLM_REQUEST)
        assert ctx2.timestamp >= ctx.timestamp

    def test_evolve_preserves_unchanged_fields(self):
        ctx = PipelineContext(raw_input="original", session_id="s42", heinzel_id="h1")
        ctx2 = ctx.evolve(phase=HookPoint.ON_INPUT_PARSED, parsed_input="parsed")
        assert ctx2.raw_input == "original"
        assert ctx2.session_id == "s42"

    def test_evolve_chain(self):
        ctx = PipelineContext(raw_input="test")
        ctx2 = ctx.evolve(phase=HookPoint.ON_INPUT_PARSED)
        ctx3 = ctx2.evolve(phase=HookPoint.ON_LLM_REQUEST)
        assert ctx3.previous is ctx2
        assert ctx3.previous.previous is ctx

    def test_snapshot_id_is_unique(self):
        ids = {PipelineContext().snapshot_id for _ in range(100)}
        assert len(ids) == 100


# ─── ContextHistory ───────────────────────────────────────────────────────────

class TestContextHistory:
    def _make_history(self):
        history = ContextHistory()
        ctx1 = PipelineContext(raw_input="Hallo", phase=HookPoint.ON_INPUT)
        ctx2 = ctx1.evolve(phase=HookPoint.ON_INPUT_PARSED, parsed_input="Hallo")
        ctx3 = ctx2.evolve(phase=HookPoint.ON_LLM_REQUEST, model="claude")
        history.push(ctx1)
        history.push(ctx2)
        history.push(ctx3)
        return history, ctx1, ctx2, ctx3

    def test_current_returns_last(self):
        history, _, _, ctx3 = self._make_history()
        assert history.current is ctx3

    def test_initial_returns_first(self):
        history, ctx1, _, _ = self._make_history()
        assert history.initial is ctx1

    def test_empty_history_raises(self):
        history = ContextHistory()
        with pytest.raises(RuntimeError):
            _ = history.current
        with pytest.raises(RuntimeError):
            _ = history.initial

    def test_at_phase_finds_snapshot(self):
        history, _, ctx2, _ = self._make_history()
        assert history.at_phase(HookPoint.ON_INPUT_PARSED) is ctx2

    def test_at_phase_returns_none_if_missing(self):
        history, _, _, _ = self._make_history()
        assert history.at_phase(HookPoint.ON_ERROR) is None

    def test_between_returns_correct_subset(self):
        history, ctx1, ctx2, ctx3 = self._make_history()
        result = history.between(HookPoint.ON_INPUT, HookPoint.ON_INPUT_PARSED)
        assert ctx1 in result
        assert ctx2 in result
        assert ctx3 not in result

    def test_to_reasoning_trace_not_empty(self):
        history, _, _, _ = self._make_history()
        trace = history.to_reasoning_trace()
        assert len(trace) == 3
        assert all(isinstance(line, str) for line in trace)

    def test_to_reasoning_trace_contains_input(self):
        history, _, _, _ = self._make_history()
        trace = history.to_reasoning_trace()
        assert any("Hallo" in line for line in trace)


# ─── ContextDiff ──────────────────────────────────────────────────────────────

class TestContextDiff:
    def test_diff_detects_changed_fields(self):
        history = ContextHistory()
        ctx1 = PipelineContext(raw_input="alt")
        ctx2 = ctx1.evolve(phase=HookPoint.ON_INPUT_PARSED, raw_input="neu")
        history.push(ctx1)
        history.push(ctx2)

        diff = history.diff(ctx1, ctx2)
        assert "raw_input" in diff.changed_fields
        assert diff.changed_fields["raw_input"] == ("alt", "neu")

    def test_diff_snapshot_ids(self):
        history = ContextHistory()
        ctx1 = PipelineContext()
        ctx2 = ctx1.evolve(phase=HookPoint.ON_INPUT_PARSED)
        history.push(ctx1)
        history.push(ctx2)

        diff = history.diff(ctx1, ctx2)
        assert diff.snapshot_a_id == ctx1.snapshot_id
        assert diff.snapshot_b_id == ctx2.snapshot_id


# ─── Exceptions ───────────────────────────────────────────────────────────────

class TestExceptions:
    def test_all_importable(self):
        for cls in [HeinzelError, ProviderError, DatabaseError, ConfigError,
                    SessionError, StrategyError, AddOnError, AddOnDependencyError,
                    AddOnLoadError, CircuitOpenError]:
            assert issubclass(cls, Exception)

    def test_all_have_str(self):
        exceptions = [
            HeinzelError("msg"),
            ProviderError("msg", status_code=500),
            DatabaseError("msg", query="SELECT 1"),
            ConfigError("msg", missing_key="provider.url"),
            SessionError("msg", session_id="s1"),
            StrategyError("msg", strategy_name="cot"),
            AddOnError("msg", addon_name="web", hook_point="ON_INPUT"),
            AddOnDependencyError("msg", addon_name="web"),
            AddOnLoadError("msg", addon_name="web"),
            CircuitOpenError("msg", addon_name="memory", failure_count=3),
        ]
        for e in exceptions:
            assert isinstance(str(e), str)
            assert len(str(e)) > 0

    def test_hierarchy(self):
        assert issubclass(AddOnError, HeinzelError)
        assert issubclass(AddOnDependencyError, AddOnError)
        assert issubclass(AddOnLoadError, AddOnError)
        assert issubclass(CircuitOpenError, AddOnError)
        assert issubclass(ProviderError, HeinzelError)
        assert issubclass(DatabaseError, HeinzelError)
        assert issubclass(ConfigError, HeinzelError)
        assert issubclass(SessionError, HeinzelError)
        assert issubclass(StrategyError, HeinzelError)

    def test_circuit_open_has_failure_count(self):
        e = CircuitOpenError("open", addon_name="x", failure_count=7)
        assert e.failure_count == 7
        assert "7" in str(e)

    def test_provider_error_has_status_code(self):
        e = ProviderError("rate limit", status_code=429)
        assert e.status_code == 429
        assert "429" in str(e)


# ─── Zirkuläre Imports ────────────────────────────────────────────────────────

class TestNoCircularImports:
    def test_import_models(self):
        import src.core.models  # noqa

    def test_import_exceptions(self):
        import src.core.exceptions  # noqa

    def test_import_core(self):
        import src.core  # noqa
