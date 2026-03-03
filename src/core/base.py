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
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
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
# DialogLogger — natives Dialoglogging, pro Heinzel eine Datei
# =============================================================================


class _DialogLogger:
    """Schreibt den kompletten Dialog eines Heinzel in eine Textdatei.

    Immer aktiv: USER-Eingaben und HEINZEL-Antworten.
    Optional: AddOn-Aufrufe (log_addons) und MCP-Nutzung (log_mcp).

    Dateiname: {log_dir}/{heinzel_id}.log
    Format:    [ISO-Timestamp] ROLE: Text
    """

    def __init__(self, heinzel_id: str, cfg: dict) -> None:
        log_cfg = cfg.get("logging", {})
        log_dir = Path(log_cfg.get("log_dir", "./logs"))
        self.log_addons: bool = bool(log_cfg.get("log_addons", False))
        self.log_mcp: bool = bool(log_cfg.get("log_mcp", False))
        self._enabled = True
        self._turn_nr: int = 0    # Laufende Nummer: USER+HEINZEL teilen sich eine Nr.
        self._path: Path | None = None

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._path = log_dir / f"{heinzel_id}.log"
            self._file = open(self._path, "a", encoding="utf-8", buffering=1)
            self._write(f"=== Session Start -- Heinzel {heinzel_id} ===")
        except Exception as exc:
            logging.getLogger(__name__).error(
                "DialogLogger: Datei konnte nicht geoeffnet werden: %s", exc
            )
            self._enabled = False
            self._file = None

    @property
    def log_path(self) -> Path | None:
        """Pfad zur Logdatei — fuer CLI-Ausgabe und !history."""
        return self._path

    def _write(self, line: str) -> None:
        if not self._enabled or self._file is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self._file.write(f"[{ts}] {line}\n")
        except Exception as exc:
            logging.getLogger(__name__).error("DialogLogger Schreibfehler: %s", exc)

    def log_user(self, message: str) -> None:
        self._turn_nr += 1
        self._write(f"#{self._turn_nr:04d} USER: {message}")

    def log_heinzel(self, response: str) -> None:
        self._write(f"#{self._turn_nr:04d} HEINZEL: {response}")

    def log_addon(self, addon_name: str, hook: str, had_changes: bool) -> None:
        if not self.log_addons:
            return
        marker = "*" if had_changes else " "
        self._write(f"  [{marker}ADDON] {addon_name} @ {hook}")

    def log_mcp_request(self, tool_name: str, args: dict) -> None:
        if not self.log_mcp:
            return
        self._write(f"  [MCP>] {tool_name}({args})")

    def log_mcp_result(self, tool_name: str, ok: bool) -> None:
        if not self.log_mcp:
            return
        status = "OK" if ok else "ERR"
        self._write(f"  [MCP<] {tool_name} [{status}]")

    def close(self) -> None:
        if self._file is not None:
            try:
                self._write("=== Session End ===")
                self._file.close()
            except Exception:
                pass
            self._file = None


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
        config_path: str | None = None,
    ) -> None:
        self._provider = provider
        self._name = name
        self._heinzel_id = heinzel_id or str(uuid4())
        self._config: dict[str, Any] = self._load_config(config, config_path)
        self._router = AddOnRouter()
        self._addons: list[AddOn] = []   # Reihenfolge fuer Lifecycle
        self._connected = False
        self._dialog_log = _DialogLogger(self._heinzel_id, self._config)
        self._pending_provider: LLMProvider | None = None   # turn-safe swap
        self._in_turn: bool = False                         # laufender LLM-Call

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

    async def set_provider(self, provider: LLMProvider) -> bool:
        """Provider wechseln — mit health-Check und turn-safem Swap.

        Laeuft gerade ein LLM-Call, wird der neue Provider als pending
        gesetzt und erst nach dem naechsten Turn aktiviert.

        Returns True bei Erfolg, False wenn Provider unhealthy.
        """
        # Health-Check — provider muss health()-Methode haben wenn vorhanden
        healthy = True
        if hasattr(provider, "health"):
            try:
                healthy = await provider.health()
            except Exception as exc:
                logger.warning("set_provider health-Check fehlgeschlagen: %s", exc)
                healthy = False

        if not healthy:
            logger.warning("set_provider abgelehnt: Provider unhealthy")
            return False

        if self._in_turn:
            logger.info("set_provider: Turn laeuft — Provider als pending gesetzt")
            self._pending_provider = provider
        else:
            self._provider = provider
            logger.info("set_provider: Provider sofort gewechselt auf %s", provider)

        return True

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
        self._dialog_log.close()
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
        """Streaming Chat-Runde durch die volle Pipeline.

        Ablauf:
          1. Vorphasen (SESSION_START → CONTEXT_READY) — AddOns koennen
             system_prompt, messages etc. setzen
          2. ON_LLM_REQUEST dispatchen
          3. Provider streamen — Chunks direkt yielden, stream_buffer akkumulieren
          4. Nachphase (ON_LLM_RESPONSE → SESSION_END) — AddOns koennen
             auf das vollstaendige Ergebnis reagieren
        """
        try:
            message = await self.on_before_chat(message)
            sid = session_id or str(uuid4())
            ctx_history = ContextHistory()

            # Initialer Snapshot
            ctx = PipelineContext(
                raw_input=message,
                parsed_input=message,
                session_id=sid,
                heinzel_id=self._heinzel_id,
                phase=HookPoint.ON_SESSION_START,
            )
            ctx_history.push(ctx)
            self._dialog_log.log_user(message)

            # --- Vorphasen (synchron, kein Yield) ---
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

            if halted:
                return

            # ON_LLM_REQUEST
            ctx, halted = await self._phase(HookPoint.ON_LLM_REQUEST, ctx, ctx_history)
            if halted:
                return

            # --- Streaming ---
            messages = self._build_messages_from_ctx(ctx)
            stream_buffer = ""
            try:
                async for chunk in self._provider.stream(
                    messages=messages,
                    system_prompt=ctx.system_prompt,
                    model=ctx.model,
                ):
                    stream_buffer += chunk
                    yield chunk
            except Exception as exc:
                logger.error("Provider-Stream-Fehler: %s", exc, exc_info=True)
                error_chunk = f"[Fehler: {exc}]"
                stream_buffer += error_chunk
                yield error_chunk

            # --- Nachphase (synchron, kein Yield) ---
            ctx = ctx.evolve(
                phase=HookPoint.ON_LLM_RESPONSE,
                response=stream_buffer,
                stream_buffer=stream_buffer,
                loop_done=True,
            )
            ctx_history.push(ctx)
            ctx, _ = await self._dispatch_and_apply(
                HookPoint.ON_LLM_RESPONSE, ctx, ctx_history
            )

            for phase in [
                HookPoint.ON_LOOP_END,
                HookPoint.ON_OUTPUT,
                HookPoint.ON_OUTPUT_SENT,
                HookPoint.ON_STORE,
                HookPoint.ON_STORED,
                HookPoint.ON_SESSION_END,
            ]:
                ctx, _ = await self._phase(phase, ctx, ctx_history)
                if phase == HookPoint.ON_OUTPUT_SENT:
                    self._dialog_log.log_heinzel(stream_buffer)

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
        self._dialog_log.log_user(message)

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
            if phase == HookPoint.ON_OUTPUT_SENT:
                self._dialog_log.log_heinzel(ctx.response or ctx.stream_buffer or "")

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

        # AddOn-Logging (optional)
        if self._dialog_log.log_addons and results:
            for result in results:
                addon_name = getattr(result, "addon_name", "?")
                self._dialog_log.log_addon(addon_name, phase.value, result.modified_ctx is not None)

        # MCP-Logging (optional)
        if self._dialog_log.log_mcp:
            if phase == HookPoint.ON_TOOL_REQUEST and ctx.tool_requests:
                for tr in ctx.tool_requests:
                    self._dialog_log.log_mcp_request(tr.tool_name, getattr(tr, "arguments", {}))
            elif phase == HookPoint.ON_TOOL_RESULT and ctx.tool_results:
                for tr in ctx.tool_results:
                    self._dialog_log.log_mcp_result(tr.tool_name, not bool(getattr(tr, "error", None)))

        return ctx, halted

    async def _call_provider(self, ctx: PipelineContext) -> PipelineContext:
        """LLM aufrufen und Response in neuen Context-Snapshot schreiben.

        Setzt loop_done=True als Fallback — kein LoopControl-AddOn vorhanden.
        Ein LoopControl-AddOn kann loop_done via modified_ctx auf False setzen.

        Turn-Safety: _in_turn-Flag verhindert Provider-Swap waehrend des Calls.
        Ein pending Provider wird nach dem Call aktiviert.
        """
        messages = self._build_messages_from_ctx(ctx)
        self._in_turn = True
        try:
            response = await self._provider.chat(
                messages=messages,
                system_prompt=ctx.system_prompt,
                model=ctx.model,
            )
        except Exception as exc:
            logger.error("Provider-Fehler: %s", exc, exc_info=True)
            response = f"[Provider-Fehler: {exc}]"
        finally:
            self._in_turn = False
            # Pending swap nach Turn-Ende anwenden
            if self._pending_provider is not None:
                self._provider = self._pending_provider
                self._pending_provider = None
                logger.info("set_provider: pending Provider aktiviert nach Turn-Ende")

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

    @staticmethod
    def _load_config(
        config: dict[str, Any] | None,
        config_path: str | None,
    ) -> dict[str, Any]:
        """Config laden: direkt uebergebenes dict hat Vorrang vor Datei.

        config_path-Support (YAML) folgt vollstaendig in HNZ-002-0007.
        Aktuell: Datei wird gelesen wenn vorhanden, Fehler werden geloggt.
        """
        if config is not None:
            return config
        if config_path is not None:
            try:
                import yaml  # type: ignore[import]
                with open(config_path) as f:
                    loaded = yaml.safe_load(f) or {}
                logger.info("Config geladen aus %s", config_path)
                return loaded
            except FileNotFoundError:
                logger.warning("Config-Datei nicht gefunden: %s", config_path)
            except Exception as exc:
                logger.error("Config-Ladefehler (%s): %s", config_path, exc)
        return {}
