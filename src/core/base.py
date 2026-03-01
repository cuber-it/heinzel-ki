"""BaseHeinzel — minimaler Orchestrator fuer das Heinzel-System.

Designprinzip:
    BaseHeinzel kennt: LLM-Provider, AddOnRouter, Pipeline-Loop,
                       PipelineContext und ContextHistory.
    BaseHeinzel kennt NICHT: Memory, Strategy, Goals, Estimator,
                              Evaluator, Session-Logik.

    Alles was ueber einen generischen LLM-Chatbot hinausgeht
    wird ausschliesslich via AddOns realisiert.

Fallbacks fuer den nackten Heinzel (0 AddOns):
    - messages      = [{"role": "user", "content": raw_input}]
    - system_prompt = ""
    - loop_done     = True nach erstem LLM-Response
    - session_id    = uuid4() wenn keiner uebergeben
    - parsed_input  = raw_input wenn kein ParserAddOn
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator
from uuid import uuid4

from .addon import AddOn
from .exceptions import AddOnError
from .models import (
    AddOnResult,
    ContextHistory,
    HookPoint,
    PipelineContext,
)
from .router import AddOnRouter

logger = logging.getLogger(__name__)


# =============================================================================
# LLMProvider Protocol — minimales Interface, Details in HNZ-002-0008
# =============================================================================


class LLMProvider(ABC):
    """Minimales Interface fuer LLM-Provider.

    Konkrete Implementierungen folgen in HNZ-002-0008.
    Fuer Tests genuegt ein Mock der dieses Interface implementiert.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> str:
        """Einfacher Chat-Call. Gibt den Response-Text zurueck."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streaming-Call. Liefert Text-Chunks."""


# =============================================================================
# BaseHeinzel
# =============================================================================


class BaseHeinzel:
    """Minimaler Orchestrator: Lifecycle + Pipeline-Loop + LLM-Aufruf.

    Alles darueber hinaus wird via AddOns realisiert.

    Verwendung:
        heinzel = BaseHeinzel(provider=my_provider, name="test")
        heinzel.register_addon(my_addon, hooks={HookPoint.ON_INPUT})
        await heinzel.connect()
        response = await heinzel.chat("Hallo!")
        await heinzel.disconnect()
    """

    def __init__(
        self,
        provider: LLMProvider,
        name: str = "heinzel",
        heinzel_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._provider = provider
        self._name = name
        self._heinzel_id = heinzel_id or str(uuid4())
        self._config: dict[str, Any] = config or {}
        self._router = AddOnRouter()
        self._addons: list[AddOn] = []   # Reihenfolge fuer Lifecycle
        self._connected = False

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def heinzel_id(self) -> str:
        return self._heinzel_id

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def addon_router(self) -> AddOnRouter:
        return self._router

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def register_addon(
        self,
        addon: AddOn,
        hooks: set[HookPoint],
        priority: int = 0,
    ) -> None:
        """AddOn registrieren. Muss vor connect() aufgerufen werden."""
        self._router.register(addon, hooks, priority=priority)
        if addon not in self._addons:
            self._addons.append(addon)

    async def connect(self) -> None:
        """Alle AddOns in Registrierungsreihenfolge starten."""
        for addon in self._addons:
            try:
                await addon.on_attach(self)
            except Exception as exc:
                logger.error("on_attach fehlgeschlagen fuer %s: %s", addon, exc)
                raise AddOnError(f"on_attach fehlgeschlagen: {exc}") from exc
        self._connected = True
        logger.info("BaseHeinzel '%s' verbunden (%d AddOns)", self._name, len(self._addons))

    async def disconnect(self) -> None:
        """Alle AddOns in umgekehrter Reihenfolge stoppen."""
        for addon in reversed(self._addons):
            try:
                await addon.on_detach(self)
            except Exception as exc:
                logger.error("on_detach fehlgeschlagen fuer %s: %s", addon, exc)
        self._connected = False
        logger.info("BaseHeinzel '%s' getrennt", self._name)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """Chat-Runde. Gibt immer str zurueck — nie Exception nach aussen."""
        try:
            message = await self.on_before_chat(message)
            ctx_history, final_ctx = await self._run_pipeline(message, session_id)
            response = final_ctx.response or final_ctx.stream_buffer or ""
            return await self.on_after_chat(response, ctx_history)
        except Exception as exc:
            logger.error("chat() Fehler: %s", exc, exc_info=True)
            return f"[Fehler: {exc}]"

    async def chat_stream(
        self, message: str, session_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Streaming Chat-Runde. Chunks werden direkt geliefert.

        TODO HNZ-002-0008: Streaming korrekt mit Pipeline verdrahten.
        Aktuell: Direkt an Provider delegiert als Fallback.
        """
        try:
            message = await self.on_before_chat(message)
            msgs = self._build_messages(message)
            async for chunk in self._provider.stream(messages=msgs):
                yield chunk
        except Exception as exc:
            logger.error("chat_stream() Fehler: %s", exc, exc_info=True)
            yield f"[Fehler: {exc}]"

    # -------------------------------------------------------------------------
    # Subklassen-Hooks
    # -------------------------------------------------------------------------

    async def on_before_chat(self, message: str) -> str:
        """Optionaler Hook vor der Pipeline. Kann message transformieren."""
        return message

    async def on_after_chat(self, response: str, ctx_history: ContextHistory) -> str:
        """Optionaler Hook nach der Pipeline. Kann response transformieren."""
        return response

    # -------------------------------------------------------------------------
    # Pipeline
    # -------------------------------------------------------------------------

    async def _run_pipeline(
        self, message: str, session_id: str | None
    ) -> tuple[ContextHistory, PipelineContext]:
        """Vollstaendiger Pipeline-Durchlauf. Gibt (history, final_ctx) zurueck."""

        sid = session_id or str(uuid4())
        ctx_history = ContextHistory()

        # Initialer Snapshot
        ctx = PipelineContext(
            raw_input=message,
            parsed_input=message,   # Fallback: kein ParserAddOn
            session_id=sid,
            heinzel_id=self._heinzel_id,
            phase=HookPoint.ON_SESSION_START,  # Session startet vor Input
        )
        ctx_history.push(ctx)

        # --- Vorphasen ---
        halted = False
        for phase in [
            HookPoint.ON_INPUT,
            HookPoint.ON_INPUT_PARSED,
            HookPoint.ON_MEMORY_QUERY,
            HookPoint.ON_CONTEXT_BUILD,
            HookPoint.ON_CONTEXT_READY,
        ]:
            ctx, halted = await self._phase(phase, ctx, ctx_history)
            if halted:
                break

            # Nach MEMORY_QUERY: HIT oder MISS
            if phase == HookPoint.ON_MEMORY_QUERY:
                memory_phase = (
                    HookPoint.ON_MEMORY_HIT
                    if ctx.memory_results
                    else HookPoint.ON_MEMORY_MISS
                )
                ctx, halted = await self._phase(memory_phase, ctx, ctx_history)
                if ctx.short_circuit or halted:
                    halted = True
                    break

        # --- Reasoning Loop ---
        if not halted:
            iteration = 0
            while True:
                # LLM-Request Phase
                ctx, halted = await self._phase(HookPoint.ON_LLM_REQUEST, ctx, ctx_history)
                if halted:
                    break

                # LLM aufrufen — einzige echte Core-Logik
                ctx = await self._call_provider(ctx)
                ctx_history.push(ctx)

                # LLM-Response dispatchen
                ctx, halted = await self._dispatch_and_apply(
                    HookPoint.ON_LLM_RESPONSE, ctx, ctx_history
                )
                if halted:
                    break

                # Loop-Abbruch: Fallback loop_done=True aus _call_provider
                if ctx.loop_done:
                    break

                # Loop-Iteration
                iteration += 1
                ctx = ctx.evolve(
                    phase=HookPoint.ON_LOOP_ITERATION,
                    loop_iteration=iteration,
                )
                ctx_history.push(ctx)
                ctx, halted = await self._dispatch_and_apply(
                    HookPoint.ON_LOOP_ITERATION, ctx, ctx_history
                )
                if halted:
                    break

        # --- Nachphase ---
        for phase in [
            HookPoint.ON_LOOP_END,
            HookPoint.ON_OUTPUT,
            HookPoint.ON_OUTPUT_SENT,
            HookPoint.ON_STORE,
            HookPoint.ON_STORED,
            HookPoint.ON_SESSION_END,
        ]:
            ctx, _ = await self._phase(phase, ctx, ctx_history)

        return ctx_history, ctx

    async def _phase(
        self,
        phase: HookPoint,
        ctx: PipelineContext,
        ctx_history: ContextHistory,
    ) -> tuple[PipelineContext, bool]:
        """Einzelne Pipeline-Phase: evolve + push + dispatch."""
        ctx = ctx.evolve(phase=phase)
        ctx_history.push(ctx)
        return await self._dispatch_and_apply(phase, ctx, ctx_history)

    async def _dispatch_and_apply(
        self,
        phase: HookPoint,
        ctx: PipelineContext,
        ctx_history: ContextHistory,
    ) -> tuple[PipelineContext, bool]:
        """Router aufrufen und modified_ctx anwenden.

        Gibt (final_ctx, halt) zurueck.
        Exceptions aus AddOns werden abgefangen und als ON_ERROR dispatcht.
        """
        try:
            results: list[AddOnResult] = await self._router.dispatch(
                phase, ctx, ctx_history
            )
        except Exception as exc:
            logger.error("Dispatch-Fehler bei %s: %s", phase, exc, exc_info=True)
            ctx = ctx.evolve(
                phase=HookPoint.ON_ERROR,
                metadata={**ctx.metadata, "error": str(exc)},
            )
            ctx_history.push(ctx)
            return ctx, False

        halted = False
        for result in results:
            if result.modified_ctx is not None:
                ctx = result.modified_ctx
            if result.halt:
                halted = True
                break

        return ctx, halted

    async def _call_provider(self, ctx: PipelineContext) -> PipelineContext:
        """LLM aufrufen und Response in neuen Context-Snapshot schreiben.

        Setzt loop_done=True als Fallback — kein LoopControl-AddOn vorhanden.
        Ein LoopControl-AddOn kann loop_done via modified_ctx auf False setzen.
        """
        messages = self._build_messages_from_ctx(ctx)
        try:
            response = await self._provider.chat(
                messages=messages,
                system_prompt=ctx.system_prompt,
                model=ctx.model,
            )
        except Exception as exc:
            logger.error("Provider-Fehler: %s", exc, exc_info=True)
            response = f"[Provider-Fehler: {exc}]"

        return ctx.evolve(
            phase=HookPoint.ON_LLM_RESPONSE,
            response=response,
            stream_buffer=response,
            loop_done=True,   # Fallback: Loop endet nach erstem Turn
        )

    # -------------------------------------------------------------------------
    # Hilfsmethoden
    # -------------------------------------------------------------------------

    def _build_messages(self, message: str) -> list[dict[str, Any]]:
        """Minimal-Fallback: eine User-Message ohne Context."""
        return [{"role": "user", "content": message}]

    def _build_messages_from_ctx(self, ctx: PipelineContext) -> list[dict[str, Any]]:
        """Messages aus Context bauen.

        Fallback wenn kein ContextBuilder-AddOn registriert:
        nur die aktuelle User-Message.
        """
        if ctx.messages:
            return [{"role": m.role, "content": m.content} for m in ctx.messages]
        return [{"role": "user", "content": ctx.parsed_input or ctx.raw_input}]
