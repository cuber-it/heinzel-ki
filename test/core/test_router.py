"""Tests für src/core/router.py

Abdeckung:
    - register(): Happy Path, Doppelt-Register, Dependency-Check, leere hooks
    - unregister(): Happy Path, unbekanntes AddOn
    - get() / list_registered() / is_registered()
    - dispatch(): Reihenfolge (priority + reg_order), halt-Flag, Context-Kette
    - Fehler-Isolation: Exception → AddOnError in results → ON_ERROR dispatcht
    - Concurrency: parallele dispatch()-Aufrufe
"""

import asyncio

import pytest

from src.core.addon import AddOn, AddOnState
from src.core.exceptions import AddOnDependencyError, AddOnError
from src.core.models import AddOnResult, HookPoint, PipelineContext
from src.core.router import AddOnRouter


# =============================================================================
# Fixtures & Helpers
# =============================================================================


def make_ctx(**kwargs) -> PipelineContext:
    defaults = dict(session_id="test-session", raw_input="hallo")
    defaults.update(kwargs)
    return PipelineContext(**defaults)


class MinimalAddOn(AddOn):
    """Kleinstes valides AddOn."""
    name = "minimal"


class TrackingAddOn(AddOn):
    """Zeichnet Hook-Aufrufe auf."""

    def __init__(self, tag: str, deps: list[str] | None = None) -> None:
        self.__class__ = type(tag, (TrackingAddOn,), {"name": tag, "dependencies": deps or []})
        super().__init__()
        self.calls: list[str] = []

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        self.calls.append(f"{self.name}:on_input")
        return AddOnResult(modified_ctx=ctx)

    async def on_output(self, ctx: PipelineContext, history=None) -> AddOnResult:
        self.calls.append(f"{self.name}:on_output")
        return AddOnResult(modified_ctx=ctx)

    async def on_error(self, ctx: PipelineContext, history=None) -> AddOnResult:
        self.calls.append(f"{self.name}:on_error")
        return AddOnResult(modified_ctx=ctx)


class MutatingAddOn(AddOn):
    """Mutiert raw_input."""
    name = "mutating"

    def __init__(self, suffix: str) -> None:
        super().__init__()
        self.suffix = suffix

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        new_ctx = ctx.model_copy(update={"raw_input": ctx.raw_input + self.suffix})
        return AddOnResult(modified_ctx=new_ctx)


class HaltingAddOn(AddOn):
    """Setzt halt=True."""
    name = "halting"

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        return AddOnResult(modified_ctx=ctx, halt=True)


class BrokenAddOn(AddOn):
    """Wirft immer eine Exception."""
    name = "broken"

    async def on_input(self, ctx: PipelineContext, history=None) -> AddOnResult:
        raise RuntimeError("Simulierter Fehler")


def make_tracking(tag: str, deps: list[str] | None = None) -> TrackingAddOn:
    """Factory für TrackingAddOns mit eindeutigem Namen."""
    addon = object.__new__(TrackingAddOn)
    addon.__class__ = type(tag, (TrackingAddOn,), {
        "name": tag,
        "version": "0.1.0",
        "dependencies": deps or [],
    })
    TrackingAddOn.__init__(addon, tag, deps)
    return addon


# =============================================================================
# register() Tests
# =============================================================================


def test_register_happy_path():
    router = AddOnRouter()
    addon = MinimalAddOn()
    router.register(addon, hooks=[HookPoint.ON_INPUT])
    assert router.is_registered("minimal")
    assert router.get("minimal") is addon


def test_register_duplicate_raises():
    router = AddOnRouter()
    router.register(MinimalAddOn(), hooks=[HookPoint.ON_INPUT])
    with pytest.raises(AddOnError, match="bereits registriert"):
        router.register(MinimalAddOn(), hooks=[HookPoint.ON_INPUT])


def test_register_empty_hooks_raises():
    router = AddOnRouter()
    with pytest.raises(ValueError, match="hooks darf nicht leer"):
        router.register(MinimalAddOn(), hooks=[])


def test_register_dependency_missing_raises():
    class NeedsBase(AddOn):
        name = "needs_base"
        dependencies = ["base_addon"]

    router = AddOnRouter()
    with pytest.raises(AddOnDependencyError, match="nicht registriert"):
        router.register(NeedsBase(), hooks=[HookPoint.ON_INPUT])


def test_register_dependency_satisfied():
    class BaseAddon(AddOn):
        name = "base_addon"

    class NeedsBase(AddOn):
        name = "needs_base"
        dependencies = ["base_addon"]

    router = AddOnRouter()
    router.register(BaseAddon(), hooks=[HookPoint.ON_INPUT])
    router.register(NeedsBase(), hooks=[HookPoint.ON_INPUT])  # kein Fehler
    assert router.is_registered("needs_base")


# =============================================================================
# unregister() Tests
# =============================================================================


def test_unregister_happy_path():
    router = AddOnRouter()
    router.register(MinimalAddOn(), hooks=[HookPoint.ON_INPUT])
    router.unregister("minimal")
    assert not router.is_registered("minimal")
    assert router.get("minimal") is None


def test_unregister_unknown_raises():
    router = AddOnRouter()
    with pytest.raises(AddOnError, match="nicht registriert"):
        router.unregister("ghost")


def test_unregister_then_reregister():
    router = AddOnRouter()
    router.register(MinimalAddOn(), hooks=[HookPoint.ON_INPUT])
    router.unregister("minimal")
    router.register(MinimalAddOn(), hooks=[HookPoint.ON_OUTPUT])  # kein Fehler
    assert router.is_registered("minimal")


# =============================================================================
# get() / list_registered() / is_registered()
# =============================================================================


def test_get_unknown_returns_none():
    router = AddOnRouter()
    assert router.get("ghost") is None


def test_list_registered_order():
    router = AddOnRouter()
    a = make_tracking("alpha")
    b = make_tracking("beta")
    c = make_tracking("gamma")
    router.register(a, hooks=[HookPoint.ON_INPUT], priority=10)
    router.register(b, hooks=[HookPoint.ON_INPUT], priority=5)
    router.register(c, hooks=[HookPoint.ON_INPUT], priority=5)
    names = list(router.list_registered().keys())
    # b und c haben priority=5 (vor alpha=10), b kommt vor c (reg_order)
    assert names == ["beta", "gamma", "alpha"]


def test_list_registered_hooks():
    router = AddOnRouter()
    addon = MinimalAddOn()
    router.register(addon, hooks=[HookPoint.ON_INPUT, HookPoint.ON_OUTPUT])
    hooks = router.list_registered()["minimal"]
    assert HookPoint.ON_INPUT in hooks
    assert HookPoint.ON_OUTPUT in hooks


# =============================================================================
# dispatch() — Basis
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_no_registered_returns_empty():
    router = AddOnRouter()
    ctx = make_ctx()
    results = await router.dispatch(HookPoint.ON_INPUT, ctx)
    assert results == []


@pytest.mark.asyncio
async def test_dispatch_calls_registered_addon():
    router = AddOnRouter()
    a = make_tracking("alpha")
    router.register(a, hooks=[HookPoint.ON_INPUT])
    ctx = make_ctx()
    results = await router.dispatch(HookPoint.ON_INPUT, ctx)
    assert len(results) == 1
    assert "alpha:on_input" in a.calls


@pytest.mark.asyncio
async def test_dispatch_skips_addon_not_registered_for_hook():
    router = AddOnRouter()
    a = make_tracking("alpha")
    router.register(a, hooks=[HookPoint.ON_OUTPUT])  # NUR on_output
    ctx = make_ctx()
    results = await router.dispatch(HookPoint.ON_INPUT, ctx)
    assert results == []
    assert a.calls == []


@pytest.mark.asyncio
async def test_dispatch_context_chaining():
    """Jedes AddOn bekommt den mutierten Context des Vorgängers."""
    router = AddOnRouter()
    a = MutatingAddOn("_a")
    b = MutatingAddOn("_b")
    # muss verschiedene Namen haben
    a.__class__ = type("mutating_a", (MutatingAddOn,), {"name": "mutating_a", "version": "0.1.0", "dependencies": []})
    b.__class__ = type("mutating_b", (MutatingAddOn,), {"name": "mutating_b", "version": "0.1.0", "dependencies": []})
    router.register(a, hooks=[HookPoint.ON_INPUT], priority=0)
    router.register(b, hooks=[HookPoint.ON_INPUT], priority=1)
    ctx = make_ctx(raw_input="start")
    results = await router.dispatch(HookPoint.ON_INPUT, ctx)
    assert len(results) == 2
    assert results[-1].modified_ctx.raw_input == "start_a_b"


@pytest.mark.asyncio
async def test_dispatch_returns_list_of_addon_results():
    router = AddOnRouter()
    a = make_tracking("a1")
    b = make_tracking("b1")
    router.register(a, hooks=[HookPoint.ON_INPUT], priority=0)
    router.register(b, hooks=[HookPoint.ON_INPUT], priority=1)
    ctx = make_ctx()
    results = await router.dispatch(HookPoint.ON_INPUT, ctx)
    assert len(results) == 2
    assert all(isinstance(r, AddOnResult) for r in results)


# =============================================================================
# dispatch() — Priority & Reihenfolge
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_priority_order():
    """Niedrigere priority wird zuerst aufgerufen."""
    call_order: list[str] = []

    class First(AddOn):
        name = "first"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("first")
            return AddOnResult(modified_ctx=ctx)

    class Second(AddOn):
        name = "second"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("second")
            return AddOnResult(modified_ctx=ctx)

    router = AddOnRouter()
    router.register(Second(), hooks=[HookPoint.ON_INPUT], priority=10)
    router.register(First(), hooks=[HookPoint.ON_INPUT], priority=1)
    await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert call_order == ["first", "second"]


@pytest.mark.asyncio
async def test_dispatch_same_priority_registration_order():
    """Bei gleicher priority gilt Registrierungsreihenfolge."""
    call_order: list[str] = []

    class A(AddOn):
        name = "a_addon"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("a")
            return AddOnResult(modified_ctx=ctx)

    class B(AddOn):
        name = "b_addon"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("b")
            return AddOnResult(modified_ctx=ctx)

    class C(AddOn):
        name = "c_addon"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("c")
            return AddOnResult(modified_ctx=ctx)

    router = AddOnRouter()
    router.register(A(), hooks=[HookPoint.ON_INPUT], priority=5)
    router.register(B(), hooks=[HookPoint.ON_INPUT], priority=5)
    router.register(C(), hooks=[HookPoint.ON_INPUT], priority=5)
    await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert call_order == ["a", "b", "c"]


# =============================================================================
# dispatch() — halt-Flag
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_halt_stops_chain():
    """halt=True: keine weiteren AddOns für diesen Hook."""
    call_order: list[str] = []

    class Halter(AddOn):
        name = "halter"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("halter")
            return AddOnResult(modified_ctx=ctx, halt=True)

    class AfterHalt(AddOn):
        name = "after_halt"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("after_halt")
            return AddOnResult(modified_ctx=ctx)

    router = AddOnRouter()
    router.register(Halter(), hooks=[HookPoint.ON_INPUT], priority=0)
    router.register(AfterHalt(), hooks=[HookPoint.ON_INPUT], priority=1)
    results = await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert call_order == ["halter"]
    assert len(results) == 1
    assert results[0].halt is True


# =============================================================================
# dispatch() — Fehler-Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_exception_isolated():
    """Exception in AddOn wird gefangen, nicht propagiert."""
    router = AddOnRouter()
    router.register(BrokenAddOn(), hooks=[HookPoint.ON_INPUT])
    results = await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert len(results) == 1
    assert results[0].ack is False
    assert results[0].error is not None
    assert "Simulierter Fehler" in results[0].error or "fehlgeschlagen" in results[0].error


@pytest.mark.asyncio
async def test_dispatch_exception_does_not_stop_chain():
    """Fehler in einem AddOn: Chain läuft weiter."""
    call_order: list[str] = []

    class After(AddOn):
        name = "after_broken"
        async def on_input(self, ctx: PipelineContext, history=None):
            call_order.append("after")
            return AddOnResult(modified_ctx=ctx)

    router = AddOnRouter()
    router.register(BrokenAddOn(), hooks=[HookPoint.ON_INPUT], priority=0)
    router.register(After(), hooks=[HookPoint.ON_INPUT], priority=1)
    results = await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert "after" in call_order
    assert len(results) == 2


@pytest.mark.asyncio
async def test_dispatch_exception_triggers_on_error():
    """Fehler in AddOn → ON_ERROR wird dispatcht."""
    error_handler = make_tracking("error_handler")
    router = AddOnRouter()
    router.register(BrokenAddOn(), hooks=[HookPoint.ON_INPUT])
    router.register(error_handler, hooks=[HookPoint.ON_ERROR])
    await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert "error_handler:on_error" in error_handler.calls


@pytest.mark.asyncio
async def test_dispatch_on_error_not_recursive():
    """Fehler in ON_ERROR-Handler löst kein weiteres ON_ERROR aus."""
    class BrokenErrorHandler(AddOn):
        name = "broken_error_handler"
        async def on_error(self, ctx: PipelineContext, history=None):
            raise RuntimeError("Auch kaputt")

    router = AddOnRouter()
    router.register(BrokenAddOn(), hooks=[HookPoint.ON_INPUT])
    router.register(BrokenErrorHandler(), hooks=[HookPoint.ON_ERROR])
    # Darf keine Exception propagieren
    results = await router.dispatch(HookPoint.ON_INPUT, make_ctx())
    assert results is not None


# =============================================================================
# Concurrency
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_concurrent_calls():
    """Parallele dispatch()-Aufrufe korrumpieren sich nicht gegenseitig."""
    router = AddOnRouter()
    router.register(MutatingAddOn("_x"), hooks=[HookPoint.ON_INPUT])

    # Override name für Eindeutigkeit
    list(router._entries.values())[0].addon.__class__ = type(
        "mutating_x", (MutatingAddOn,),
        {"name": "mutating_x", "version": "0.1.0", "dependencies": []}
    )

    ctxs = [make_ctx(raw_input=f"msg{i}") for i in range(10)]
    results_all = await asyncio.gather(*[
        router.dispatch(HookPoint.ON_INPUT, ctx) for ctx in ctxs
    ])
    for i, results in enumerate(results_all):
        assert results[-1].modified_ctx.raw_input == f"msg{i}_x"


# =============================================================================
# Performance
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_performance_10_addons():
    """10 AddOns an einem Hook: dispatch() < 1ms Overhead."""
    import time

    router = AddOnRouter()
    for i in range(10):
        class _A(AddOn):
            name = f"perf_{i}"
            async def on_input(self, ctx: PipelineContext, history=None):
                return AddOnResult(modified_ctx=ctx)
        _A.name = f"perf_{i}"
        router.register(_A(), hooks=[HookPoint.ON_INPUT], priority=i)

    ctx = make_ctx()
    start = time.perf_counter()
    for _ in range(100):
        await router.dispatch(HookPoint.ON_INPUT, ctx)
    elapsed_per_call = (time.perf_counter() - start) / 100

    assert elapsed_per_call < 0.001, f"dispatch() zu langsam: {elapsed_per_call*1000:.3f}ms"
