"""Tests für BaseHeinzel + Pipeline Engine — HNZ-002-0006.

Testet:
  - Lifecycle (connect/disconnect)
  - Pipeline-Sequenz via history.at_phase()
  - Immutabilität: jede Phase erzeugt neuen Snapshot
  - loop_done Fallback: Loop endet nach einem Durchlauf ohne LoopControl-AddOn
  - Fallback session_id wird gesetzt wenn keiner übergeben
  - chat() gibt NIE Exception — immer String
  - halt=True in AddOnResult bricht Pipeline ab
  - Nackter Heinzel (0 AddOns) funktioniert
"""

from __future__ import annotations

import pytest
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

from core.base import BaseHeinzel, LLMProvider
from core.addon import AddOn
from core.models import AddOnResult, ContextHistory, HookPoint, PipelineContext


# =============================================================================
# Fixtures
# =============================================================================


class MockProvider(LLMProvider):
    """Minimaler Provider für Tests."""

    def __init__(self, response: str = "mock response") -> None:
        self.response = response
        self.call_count = 0

    async def chat(self, messages, system_prompt="", model="") -> str:
        self.call_count += 1
        return self.response

    async def stream(self, messages, system_prompt="", model="") -> AsyncGenerator[str, None]:
        for chunk in self.response.split():
            yield chunk


class BrokenProvider(LLMProvider):
    """Provider der immer eine Exception wirft."""

    async def chat(self, messages, system_prompt="", model="") -> str:
        raise RuntimeError("Provider kaputt")

    async def stream(self, messages, system_prompt="", model="") -> AsyncGenerator[str, None]:
        raise RuntimeError("Provider kaputt")
        yield  # noqa: unreachable


class RecordingAddOn(AddOn):
    """AddOn das alle aufgerufenen Hooks aufzeichnet."""
    name = "recording"

    def __init__(self) -> None:
        super().__init__()
        self.called_hooks: list[str] = []
        self.attached = False
        self.detached = False

    async def on_attach(self, heinzel):
        self.attached = True

    async def on_detach(self, heinzel):
        self.detached = True

    async def on_input(self, ctx, history=None) -> AddOnResult:
        self.called_hooks.append("on_input")
        return AddOnResult(modified_ctx=ctx)

    async def on_session_start(self, ctx, history=None) -> AddOnResult:
        self.called_hooks.append("on_session_start")
        return AddOnResult(modified_ctx=ctx)

    async def on_llm_response(self, ctx, history=None) -> AddOnResult:
        self.called_hooks.append("on_llm_response")
        return AddOnResult(modified_ctx=ctx)

    async def on_output(self, ctx, history=None) -> AddOnResult:
        self.called_hooks.append("on_output")
        return AddOnResult(modified_ctx=ctx)


class HaltAddOn(AddOn):
    """AddOn das nach ON_INPUT die Pipeline stoppt."""
    name = "halt_addon"

    async def on_input(self, ctx, history=None) -> AddOnResult:
        return AddOnResult(modified_ctx=ctx, halt=True)


class ContextMutatorAddOn(AddOn):
    """AddOn das system_prompt setzt."""
    name = "context_mutator"

    async def on_context_build(self, ctx, history=None) -> AddOnResult:
        new_ctx = ctx.evolve(system_prompt="Du bist ein hilfreicher Assistent.")
        return AddOnResult(modified_ctx=new_ctx)


class LoopControlAddOn(AddOn):
    """AddOn das den Loop nach N Iterationen stoppt."""
    name = "loop_control"

    def __init__(self, max_iterations: int = 3) -> None:
        super().__init__()
        self.max_iterations = max_iterations

    async def on_llm_response(self, ctx, history=None) -> AddOnResult:
        # Loop weiterlaufen lassen bis max_iterations
        loop_done = ctx.loop_iteration >= self.max_iterations - 1
        return AddOnResult(modified_ctx=ctx.evolve(loop_done=loop_done))


def make_heinzel(response="test response", **kwargs) -> tuple[BaseHeinzel, MockProvider]:
    provider = MockProvider(response)
    heinzel = BaseHeinzel(provider=provider, name="test-heinzel", **kwargs)
    return heinzel, provider


# =============================================================================
# Lifecycle Tests
# =============================================================================


class TestLifecycle:

    @pytest.mark.asyncio
    async def test_connect_ruft_on_attach_auf(self):
        heinzel, _ = make_heinzel()
        addon = RecordingAddOn()
        heinzel.register_addon(addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()
        assert addon.attached is True

    @pytest.mark.asyncio
    async def test_disconnect_ruft_on_detach_auf(self):
        heinzel, _ = make_heinzel()
        addon = RecordingAddOn()
        heinzel.register_addon(addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()
        await heinzel.disconnect()
        assert addon.detached is True

    @pytest.mark.asyncio
    async def test_properties_vorhanden(self):
        heinzel, provider = make_heinzel()
        assert heinzel.name == "test-heinzel"
        assert heinzel.heinzel_id is not None
        assert heinzel.provider is provider
        assert heinzel.addon_router is not None
        assert isinstance(heinzel.config, dict)

    @pytest.mark.asyncio
    async def test_heinzel_id_wird_generiert(self):
        h1, _ = make_heinzel()
        h2, _ = make_heinzel()
        assert h1.heinzel_id != h2.heinzel_id

    @pytest.mark.asyncio
    async def test_explizite_heinzel_id(self):
        heinzel, _ = make_heinzel(heinzel_id="test-id-42")
        assert heinzel.heinzel_id == "test-id-42"


# =============================================================================
# Pipeline-Sequenz Tests
# =============================================================================


class TestPipelineSequenz:

    @pytest.mark.asyncio
    async def test_jede_phase_erzeugt_neuen_snapshot(self):
        """Kernprinzip: immutable context — jede Phase neuer snapshot_id."""
        heinzel, _ = make_heinzel()
        await heinzel.connect()

        # Direkt _run_pipeline aufrufen um history zu inspizieren
        history, final_ctx = await heinzel._run_pipeline("test", None)

        snapshot_ids = [s.snapshot_id for s in history._snapshots]
        # Alle IDs müssen einzigartig sein
        assert len(snapshot_ids) == len(set(snapshot_ids)), "Doppelte snapshot_ids!"
        assert len(snapshot_ids) >= 10, f"Zu wenige Snapshots: {len(snapshot_ids)}"

    @pytest.mark.asyncio
    async def test_pipeline_phasen_reihenfolge(self):
        """Kritische Phasen müssen in der richtigen Reihenfolge auftreten."""
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", None)

        phases = [s.phase for s in history._snapshots]

        def idx(hook):
            for i, p in enumerate(phases):
                if p == hook:
                    return i
            return -1

        # Reihenfolge: SESSION_START < INPUT < LLM_REQUEST < LLM_RESPONSE < OUTPUT
        assert idx(HookPoint.ON_SESSION_START) < idx(HookPoint.ON_INPUT)
        assert idx(HookPoint.ON_INPUT) < idx(HookPoint.ON_LLM_REQUEST)
        assert idx(HookPoint.ON_LLM_REQUEST) < idx(HookPoint.ON_LLM_RESPONSE)
        assert idx(HookPoint.ON_LLM_RESPONSE) < idx(HookPoint.ON_OUTPUT)
        assert idx(HookPoint.ON_OUTPUT) < idx(HookPoint.ON_SESSION_END)

    @pytest.mark.asyncio
    async def test_memory_miss_wenn_keine_results(self):
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", None)

        # Kein MemoryAddOn → MEMORY_MISS muss auftreten, MEMORY_HIT nicht
        phases = [s.phase for s in history._snapshots]
        assert HookPoint.ON_MEMORY_MISS in phases
        assert HookPoint.ON_MEMORY_HIT not in phases

    @pytest.mark.asyncio
    async def test_at_phase_findet_snapshot(self):
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("Hallo", None)

        snap = history.at_phase(HookPoint.ON_INPUT)
        assert snap is not None
        assert snap.raw_input == "Hallo"

    @pytest.mark.asyncio
    async def test_to_reasoning_trace_aufrufbar(self):
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", None)

        trace = history.to_reasoning_trace()
        assert isinstance(trace, list)
        assert len(trace) > 0


# =============================================================================
# Loop Tests
# =============================================================================


class TestLoop:

    @pytest.mark.asyncio
    async def test_loop_done_fallback_ein_durchlauf(self):
        """Ohne LoopControl-AddOn: Loop endet nach genau einem Durchlauf."""
        heinzel, provider = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", None)

        loop_iterations = [s for s in history._snapshots if s.phase == HookPoint.ON_LOOP_ITERATION]
        assert len(loop_iterations) == 0, "Ohne LoopControl darf kein ON_LOOP_ITERATION erscheinen"
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_loop_control_addon_steuert_iterationen(self):
        """Mit LoopControl-AddOn: exakt 3 LLM-Calls."""
        heinzel, provider = make_heinzel()
        loop_addon = LoopControlAddOn(max_iterations=3)
        heinzel.register_addon(loop_addon, hooks={HookPoint.ON_LLM_RESPONSE})
        await heinzel.connect()

        history, _ = await heinzel._run_pipeline("test", None)

        assert provider.call_count == 3
        loop_iters = [s for s in history._snapshots if s.phase == HookPoint.ON_LOOP_ITERATION]
        assert len(loop_iters) == 2  # Iteration 1 und 2 (nach Call 1 und 2)


# =============================================================================
# Chat API Tests
# =============================================================================


class TestChatAPI:

    @pytest.mark.asyncio
    async def test_nackter_heinzel_gibt_string_zurueck(self):
        """Nackter Heinzel (0 AddOns) gibt String zurück."""
        heinzel, _ = make_heinzel("Hallo Welt")
        await heinzel.connect()
        response = await heinzel.chat("test")
        assert isinstance(response, str)
        assert response == "Hallo Welt"

    @pytest.mark.asyncio
    async def test_chat_gibt_nie_exception(self):
        """chat() fängt alle Exceptions und gibt String zurück."""
        heinzel = BaseHeinzel(provider=BrokenProvider(), name="broken")
        await heinzel.connect()
        response = await heinzel.chat("test")
        assert isinstance(response, str)
        assert "[" in response  # Fehlermeldung in eckigen Klammern

    @pytest.mark.asyncio
    async def test_fallback_session_id(self):
        """Ohne session_id: wird eine UUID generiert."""
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", None)

        initial = history.initial
        assert initial.session_id != ""
        assert len(initial.session_id) == 36  # UUID-Format

    @pytest.mark.asyncio
    async def test_explizite_session_id_wird_behalten(self):
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("test", "meine-session-123")

        assert history.initial.session_id == "meine-session-123"

    @pytest.mark.asyncio
    async def test_fallback_parsed_input(self):
        """Ohne ParserAddOn: parsed_input == raw_input."""
        heinzel, _ = make_heinzel()
        await heinzel.connect()
        history, _ = await heinzel._run_pipeline("original message", None)

        snap = history.at_phase(HookPoint.ON_INPUT)
        assert snap is not None
        assert snap.parsed_input == "original message"


# =============================================================================
# halt Tests
# =============================================================================


class TestHalt:

    @pytest.mark.asyncio
    async def test_halt_bricht_pipeline_ab(self):
        """halt=True in AddOnResult stoppt die Pipeline."""
        heinzel, provider = make_heinzel()
        halt_addon = HaltAddOn()
        heinzel.register_addon(halt_addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()

        await heinzel._run_pipeline("test", None)

        # LLM darf NICHT aufgerufen worden sein
        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_halt_pipeline_gibt_trotzdem_string(self):
        """Auch nach halt: chat() gibt String zurück."""
        heinzel, _ = make_heinzel()
        halt_addon = HaltAddOn()
        heinzel.register_addon(halt_addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()

        response = await heinzel.chat("test")
        assert isinstance(response, str)


# =============================================================================
# AddOn-Integration Tests
# =============================================================================


class TestAddOnIntegration:

    @pytest.mark.asyncio
    async def test_addon_kann_context_modifizieren(self):
        """AddOn via modified_ctx: system_prompt wird übernommen."""
        heinzel, _ = make_heinzel()
        mutator = ContextMutatorAddOn()
        heinzel.register_addon(mutator, hooks={HookPoint.ON_CONTEXT_BUILD})
        await heinzel.connect()

        history, _ = await heinzel._run_pipeline("test", None)

        snap = history.at_phase(HookPoint.ON_CONTEXT_READY)
        assert snap is not None
        assert snap.system_prompt == "Du bist ein hilfreicher Assistent."

    @pytest.mark.asyncio
    async def test_history_wird_an_addons_weitergereicht(self):
        """AddOn empfängt history beim Hook-Aufruf."""
        received_histories = []

        class HistoryCapturingAddOn(AddOn):
            name = "history_capture"

            async def on_input(self, ctx, history=None) -> AddOnResult:
                received_histories.append(history)
                return AddOnResult(modified_ctx=ctx)

        heinzel, _ = make_heinzel()
        capture_addon = HistoryCapturingAddOn()
        heinzel.register_addon(capture_addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()

        await heinzel._run_pipeline("test", None)

        assert len(received_histories) == 1
        assert received_histories[0] is not None
        assert isinstance(received_histories[0], ContextHistory)


# =============================================================================
# chat_stream Tests
# =============================================================================


class TestChatStream:

    @pytest.mark.asyncio
    async def test_stream_liefert_chunks(self):
        """chat_stream() liefert die Provider-Chunks direkt."""
        heinzel, _ = make_heinzel("hallo welt foo")
        await heinzel.connect()
        chunks = []
        async for chunk in heinzel.chat_stream("test"):
            chunks.append(chunk)
        assert len(chunks) == 3   # MockProvider splittet auf Leerzeichen
        assert "".join(chunks) == "halloweltfoo"

    @pytest.mark.asyncio
    async def test_stream_laeuft_durch_vorphasen(self):
        """Vorphasen werden vor dem Streaming durchlaufen."""
        mutator = ContextMutatorAddOn()
        received_system_prompts = []

        class CaptureProvider(LLMProvider):
            async def chat(self, messages, system_prompt="", model="") -> str:
                return ""
            async def stream(self, messages, system_prompt="", model=""):
                received_system_prompts.append(system_prompt)
                yield "chunk"

        heinzel = BaseHeinzel(provider=CaptureProvider(), name="test")
        heinzel.register_addon(mutator, hooks={HookPoint.ON_CONTEXT_BUILD})
        await heinzel.connect()

        async for _ in heinzel.chat_stream("test"):
            pass

        assert received_system_prompts == ["Du bist ein hilfreicher Assistent."]

    @pytest.mark.asyncio
    async def test_stream_gibt_nie_exception(self):
        """chat_stream() liefert Fehler-Chunk statt Exception."""
        heinzel = BaseHeinzel(provider=BrokenProvider(), name="broken")
        await heinzel.connect()
        chunks = []
        async for chunk in heinzel.chat_stream("test"):
            chunks.append(chunk)
        assert len(chunks) >= 1
        assert any("[" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_session_id_fallback(self):
        """Ohne session_id: UUID wird generiert."""
        heinzel, _ = make_heinzel("x")
        await heinzel.connect()
        # Kein Fehler = genug für diesen Test
        async for _ in heinzel.chat_stream("test"):
            pass

    @pytest.mark.asyncio
    async def test_config_path_parameter_vorhanden(self):
        """config_path=None ist akzeptierter Parameter."""
        provider = MockProvider()
        h = BaseHeinzel(provider=provider, name="test", config_path=None)
        assert h.config == {}

    @pytest.mark.asyncio
    async def test_config_dict_hat_vorrang(self):
        """Explizites config-dict wird direkt verwendet."""
        provider = MockProvider()
        h = BaseHeinzel(provider=provider, name="test", config={"key": "val"})
        assert h.config == {"key": "val"}
