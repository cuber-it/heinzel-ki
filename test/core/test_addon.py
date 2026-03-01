"""Tests für src/core/addon.py

Abdeckung:
  - AddOnState Enum
  - AddOn ABC — Instantiierung, Defaults, Hook-Signaturen
  - AddOnManager — attach, detach, dispatch, Dependency-Check
  - Compliance-Test: Alle Hook-Namen decken alle HookPoints ab
"""

import pytest

from src.core.addon import AddOn, AddOnManager, AddOnState
from src.core.exceptions import AddOnDependencyError, AddOnError, AddOnLoadError
from src.core.models import AddOnResult, HookPoint, PipelineContext


# =============================================================================
# Fixtures & Helpers
# =============================================================================


def make_ctx(**kwargs) -> PipelineContext:
    """Minimalen PipelineContext für Tests erzeugen."""
    defaults = dict(
        session_id="test-session",
        raw_input="hallo",
    )
    defaults.update(kwargs)
    return PipelineContext(**defaults)


class MinimalAddOn(AddOn):
    """Kleinstes valides AddOn — nur name gesetzt."""
    name = "minimal"


class PriorityAddOn(AddOn):
    """AddOn zum Testen von Priority-Dispatch."""
    name = "priority_addon"
    version = "1.0.0"

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag
        self.calls: list[str] = []

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        self.calls.append(self.tag)
        return AddOnResult(modified_ctx=ctx)


class MutatingAddOn(AddOn):
    """AddOn das den Context modifiziert."""
    name = "mutating"

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        new_ctx = ctx.model_copy(update={"raw_input": "mutated"})
        return AddOnResult(modified_ctx=new_ctx)


class HaltingAddOn(AddOn):
    """AddOn das die Dispatch-Chain abbricht."""
    name = "halting"

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        return AddOnResult(modified_ctx=ctx, halt=True)


class FailingAddOn(AddOn):
    """AddOn das in einem Hook eine Exception wirft."""
    name = "failing"

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        raise RuntimeError("boom")


class DependentAddOn(AddOn):
    """AddOn mit Abhängigkeit auf 'minimal'."""
    name = "dependent"
    dependencies = ["minimal"]


class FailingAttachAddOn(AddOn):
    """AddOn dessen on_attach() scheitert."""
    name = "failing_attach"

    async def on_attach(self, heinzel: object) -> None:
        raise RuntimeError("attach boom")


class UnavailableAddOn(AddOn):
    """AddOn das is_available() = False zurückgibt."""
    name = "unavailable"

    def is_available(self) -> bool:
        return False

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        # Darf nie aufgerufen werden
        raise AssertionError("should not be called")


FAKE_HEINZEL = object()


# =============================================================================
# AddOnState
# =============================================================================


class TestAddOnState:
    def test_alle_states_vorhanden(self):
        namen = {s.name for s in AddOnState}
        assert namen == {"UNLOADED", "ATTACHED", "DETACHED", "ERROR"}

    def test_values_sind_strings(self):
        for state in AddOnState:
            assert isinstance(state.value, str)


# =============================================================================
# AddOn ABC
# =============================================================================


class TestAddOnInstanziierung:
    def test_minimal_addon_erstellt(self):
        addon = MinimalAddOn()
        assert addon.name == "minimal"
        assert addon.version == "0.1.0"
        assert addon.dependencies == []
        assert addon.state == AddOnState.UNLOADED

    def test_kein_name_wirft_load_error(self):
        class NoName(AddOn):
            name = ""

        with pytest.raises(AddOnLoadError):
            NoName()

    def test_is_available_false_wenn_unloaded(self):
        addon = MinimalAddOn()
        assert not addon.is_available()

    def test_repr_enthaelt_name_und_state(self):
        addon = MinimalAddOn()
        r = repr(addon)
        assert "minimal" in r
        assert "unloaded" in r


class TestAddOnHookDefaults:
    """Alle Hook-Methoden müssen vorhanden sein und AddOnResult zurückgeben."""

    @pytest.fixture
    def addon(self):
        return MinimalAddOn()

    @pytest.fixture
    def ctx(self):
        return make_ctx()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("hook_name", [
        "on_input", "on_input_parsed",
        "on_memory_query", "on_memory_hit", "on_memory_miss",
        "on_context_build", "on_context_ready",
        "on_llm_request", "on_stream_chunk", "on_thinking_step", "on_llm_response",
        "on_tool_request", "on_tool_result", "on_tool_error",
        "on_loop_iteration", "on_loop_end",
        "on_output", "on_output_sent",
        "on_store", "on_stored",
        "on_session_start", "on_session_end",
        "on_error",
    ])
    async def test_hook_gibt_addon_result_zurueck(self, addon, ctx, hook_name):
        hook = getattr(addon, hook_name)
        result = await hook(ctx)
        assert isinstance(result, AddOnResult)
        assert result.modified_ctx is ctx  # No-Op: unveränderter Context

    @pytest.mark.asyncio
    @pytest.mark.parametrize("hook_name", [
        "on_input", "on_input_parsed",
        "on_memory_query", "on_memory_hit", "on_memory_miss",
        "on_context_build", "on_context_ready",
        "on_llm_request", "on_stream_chunk", "on_thinking_step", "on_llm_response",
        "on_tool_request", "on_tool_result", "on_tool_error",
        "on_loop_iteration", "on_loop_end",
        "on_output", "on_output_sent",
        "on_store", "on_stored",
        "on_session_start", "on_session_end",
        "on_error",
    ])
    async def test_hook_halt_ist_false_by_default(self, addon, ctx, hook_name):
        hook = getattr(addon, hook_name)
        result = await hook(ctx)
        assert result.halt is False


# =============================================================================
# Compliance: Hook-Namen decken alle HookPoints ab
# =============================================================================


class TestCompliance:
    """Stellt sicher dass AddOn für jeden HookPoint eine Hook-Methode hat."""

    def test_jeder_hookpoint_hat_methode(self):
        missing = []
        for hp in HookPoint:
            method_name = hp.value  # z.B. "on_input"
            if not hasattr(AddOn, method_name):
                missing.append(method_name)
        assert missing == [], f"Fehlende Hook-Methoden: {missing}"

    def test_anzahl_hooks_entspricht_hookpoints(self):
        hook_methoden = [
            name for name in dir(AddOn)
            if name.startswith("on_") and callable(getattr(AddOn, name)) and name not in ("on_attach", "on_detach")
        ]
        assert len(hook_methoden) == len(HookPoint)


# =============================================================================
# AddOnManager — attach / detach
# =============================================================================


class TestAddOnManagerAttach:
    @pytest.mark.asyncio
    async def test_attach_setzt_state_auf_attached(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        assert addon.state == AddOnState.ATTACHED

    @pytest.mark.asyncio
    async def test_attach_setzt_is_available_true(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        assert addon.is_available()

    @pytest.mark.asyncio
    async def test_doppeltes_attach_wirft_addon_error(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        with pytest.raises(AddOnError):
            await manager.attach(addon, FAKE_HEINZEL)

    @pytest.mark.asyncio
    async def test_get_findet_addon_nach_name(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        assert manager.get("minimal") is addon

    @pytest.mark.asyncio
    async def test_get_gibt_none_fuer_unbekannten_namen(self):
        manager = AddOnManager()
        assert manager.get("gibts_nicht") is None

    @pytest.mark.asyncio
    async def test_failing_attach_setzt_state_error(self):
        manager = AddOnManager()
        addon = FailingAttachAddOn()
        with pytest.raises(AddOnLoadError):
            await manager.attach(addon, FAKE_HEINZEL)
        assert addon.state == AddOnState.ERROR

    @pytest.mark.asyncio
    async def test_attach_on_attach_lifecycle_wird_aufgerufen(self):
        manager = AddOnManager()
        called = []

        class TrackingAddOn(AddOn):
            name = "tracking"
            async def on_attach(self, heinzel):
                called.append("attached")

        addon = TrackingAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        assert called == ["attached"]


class TestAddOnManagerDependencies:
    @pytest.mark.asyncio
    async def test_dependency_fehlt_wirft_dependency_error(self):
        manager = AddOnManager()
        addon = DependentAddOn()
        with pytest.raises(AddOnDependencyError):
            await manager.attach(addon, FAKE_HEINZEL)

    @pytest.mark.asyncio
    async def test_dependency_vorhanden_attach_erfolgreich(self):
        manager = AddOnManager()
        dep = MinimalAddOn()
        await manager.attach(dep, FAKE_HEINZEL)
        addon = DependentAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        assert addon.state == AddOnState.ATTACHED


class TestAddOnManagerDetach:
    @pytest.mark.asyncio
    async def test_detach_setzt_state_auf_detached(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        await manager.detach("minimal")
        assert addon.state == AddOnState.DETACHED

    @pytest.mark.asyncio
    async def test_detach_entfernt_addon_aus_liste(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        await manager.detach("minimal")
        assert manager.get("minimal") is None

    @pytest.mark.asyncio
    async def test_detach_unbekanntes_addon_wirft_error(self):
        manager = AddOnManager()
        with pytest.raises(AddOnError):
            await manager.detach("gibts_nicht")

    @pytest.mark.asyncio
    async def test_detach_all_entfernt_alle(self):
        manager = AddOnManager()
        a1 = MinimalAddOn()
        a2 = MutatingAddOn()
        await manager.attach(a1, FAKE_HEINZEL)
        await manager.attach(a2, FAKE_HEINZEL)
        await manager.detach_all()
        assert manager.addons == []

    @pytest.mark.asyncio
    async def test_on_detach_lifecycle_wird_aufgerufen(self):
        called = []

        class TrackingAddOn(AddOn):
            name = "tracking"
            async def on_detach(self, heinzel):
                called.append("detached")

        manager = AddOnManager()
        addon = TrackingAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        await manager.detach("tracking")
        assert called == ["detached"]


# =============================================================================
# AddOnManager — Dispatch
# =============================================================================


class TestAddOnManagerDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_gibt_unveraenderten_ctx_zurueck_bei_no_op(self):
        manager = AddOnManager()
        addon = MinimalAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        ctx = make_ctx()
        result_ctx = await manager.dispatch("on_input", ctx)
        assert result_ctx.raw_input == "hallo"

    @pytest.mark.asyncio
    async def test_dispatch_mutiert_context(self):
        manager = AddOnManager()
        addon = MutatingAddOn()
        await manager.attach(addon, FAKE_HEINZEL)
        ctx = make_ctx()
        result_ctx = await manager.dispatch("on_input", ctx)
        assert result_ctx.raw_input == "mutated"

    @pytest.mark.asyncio
    async def test_dispatch_halt_unterbricht_chain(self):
        manager = AddOnManager()
        halter = HaltingAddOn()
        tracker = PriorityAddOn(tag="after_halt")

        await manager.attach(halter, FAKE_HEINZEL, priority=10)
        await manager.attach(tracker, FAKE_HEINZEL, priority=20)

        ctx = make_ctx()
        await manager.dispatch("on_input", ctx)
        assert tracker.calls == []  # Wurde nie aufgerufen

    @pytest.mark.asyncio
    async def test_dispatch_fehler_in_addon_chain_laeuft_weiter(self):
        """Fehler in einem AddOn dürfen andere nicht stoppen."""
        manager = AddOnManager()
        failer = FailingAddOn()
        tracker = PriorityAddOn(tag="after_fail")

        await manager.attach(failer, FAKE_HEINZEL, priority=10)
        await manager.attach(tracker, FAKE_HEINZEL, priority=20)

        ctx = make_ctx()
        await manager.dispatch("on_input", ctx)
        assert tracker.calls == ["after_fail"]

    @pytest.mark.asyncio
    async def test_dispatch_unavailable_addon_wird_uebersprungen(self):
        manager = AddOnManager()
        unavailable = UnavailableAddOn()
        # Direkt state setzen (attach setzt ATTACHED, wir wollen ATTACHED aber is_available=False)
        await manager.attach(unavailable, FAKE_HEINZEL)
        ctx = make_ctx()
        # Sollte keine Exception werfen
        result_ctx = await manager.dispatch("on_input", ctx)
        assert result_ctx is ctx  # unveraendert

    @pytest.mark.asyncio
    async def test_dispatch_prioritaet_bestimmt_reihenfolge(self):
        call_order = []

        class OrderAddOn(AddOn):
            def __init__(self, tag):
                super().__init__()
                self._tag = tag

            async def on_input(self, ctx: PipelineContext, history=None):
                call_order.append(self._tag)
                return AddOnResult(modified_ctx=ctx)

        class A(OrderAddOn):
            name = "a"
        class B(OrderAddOn):
            name = "b"
        class C(OrderAddOn):
            name = "c"

        manager = AddOnManager()
        await manager.attach(C("c"), FAKE_HEINZEL, priority=30)
        await manager.attach(A("a"), FAKE_HEINZEL, priority=10)
        await manager.attach(B("b"), FAKE_HEINZEL, priority=20)

        await manager.dispatch("on_input", make_ctx())
        assert call_order == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_dispatch_kein_addon_gibt_ctx_unveraendert_zurueck(self):
        manager = AddOnManager()
        ctx = make_ctx()
        result_ctx = await manager.dispatch("on_input", ctx)
        assert result_ctx is ctx
