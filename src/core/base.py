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
from typing import Any, AsyncGenerator
from uuid import uuid4

from .addon import AddOn
from .exceptions import AddOnError
from ._dialog_logger import _DialogLogger
from ._pipeline import (
    dispatch_and_apply,
    phase,
    run_pipeline,
    run_post_phases,
    run_pre_phases,
)
from ._provider_bridge import build_messages, build_messages_from_ctx, call_provider
from .provider import LLMProvider
from .models import (
    ContextHistory,
    HookPoint,
    PipelineContext,
)
from .router import AddOnRouter
from .session import SessionManager, WorkingMemory
from .session_noop import NoopSessionManager

logger = logging.getLogger(__name__)


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
        _mem_cfg = self._config.get("memory", {})
        _max_tokens: int = int(_mem_cfg.get("max_tokens", 128_000))
        _max_turns: int = int(_mem_cfg.get("max_turns", 10_000))
        self._session_manager: SessionManager = NoopSessionManager(
            max_tokens=_max_tokens,
            max_turns=_max_turns,
        )
        self._working_memory: WorkingMemory | None = None   # lazy via _ensure_session()

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

    @property
    def session_manager(self) -> SessionManager:
        return self._session_manager

    def set_session_manager(self, manager: SessionManager) -> None:
        """SessionManager austauschen — muss vor dem ersten chat()-Call passieren.

        Der Manager verwaltet Sessions, Turns und Working Memory.
        Default ist NoopSessionManager (in-memory, kein Persist).
        """
        self._session_manager = manager
        self._working_memory = None   # Working Memory zuruecksetzen — lazy neu holen

    async def _ensure_session(self, session_id: str | None) -> tuple[str, WorkingMemory]:
        """Lazy Session-Init: Session anlegen oder fortsetzen, Working Memory holen.

        Gibt (session_id, working_memory) zurueck.
        Wird beim ersten Turn in _run_pipeline() aufgerufen.
        """
        from .exceptions import SessionNotFoundError

        if session_id is not None:
            # Explizite session_id: vorhandene Session fortsetzen
            try:
                session = await self._session_manager.resume_session(session_id)
            except SessionNotFoundError:
                # Unbekannte ID: neue Session mit dieser ID ist nicht moeglich
                # -> neue Session anlegen, session_id ignorieren
                logger.warning(
                    "_ensure_session: session_id %s unbekannt, neue Session gestartet",
                    session_id,
                )
                session = await self._session_manager.create_session(
                    self._heinzel_id, session_id=session_id
                )
        else:
            active = self._session_manager.active_session
            if active is not None:
                session = active
            else:
                session = await self._session_manager.create_session(self._heinzel_id)

        working_memory = await self._session_manager.get_working_memory(session.id)
        return session.id, working_memory

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
          1. run_pre_phases  — SESSION_START bis ON_CONTEXT_READY inkl. Memory
          2. ON_LLM_REQUEST  — AddOns koennen Request anpassen
          3. Provider-Stream — Chunks direkt yielden, stream_buffer akkumulieren
          4. run_post_phases — ON_LOOP_END bis ON_SESSION_END inkl. Turn-Storage
        """
        try:
            message = await self.on_before_chat(message)
            sid, working_memory, ctx, ctx_history, halted = await run_pre_phases(
                self, message, session_id
            )
            if halted:
                return

            ctx, halted = await self._phase(HookPoint.ON_LLM_REQUEST, ctx, ctx_history)
            if halted:
                return

            # Streaming — yield muss in dieser Funktion bleiben
            stream_buffer = ""
            try:
                async for chunk in self._provider.stream(
                    messages=self._build_messages_from_ctx(ctx),
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
            await run_post_phases(
                self, ctx, ctx_history, sid, message, stream_buffer, working_memory
            )

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
        """Delegate: Pipeline-Durchlauf — Implementierung in _pipeline."""
        return await run_pipeline(self, message, session_id)

    async def _phase(
        self,
        hook: HookPoint,
        ctx: PipelineContext,
        ctx_history: ContextHistory,
    ) -> tuple[PipelineContext, bool]:
        """Delegate: Pipeline-Phase — Implementierung in _pipeline."""
        return await phase(self, hook, ctx, ctx_history)

    async def _dispatch_and_apply(
        self,
        hook: HookPoint,
        ctx: PipelineContext,
        ctx_history: ContextHistory,
    ) -> tuple[PipelineContext, bool]:
        """Delegate: Router aufrufen — Implementierung in _pipeline."""
        return await dispatch_and_apply(self, hook, ctx, ctx_history)

    async def _call_provider(self, ctx: PipelineContext) -> PipelineContext:
        """Delegate: LLM aufrufen — Implementierung in _provider_bridge."""
        return await call_provider(self, ctx)

    # -------------------------------------------------------------------------
    # Hilfsmethoden
    # -------------------------------------------------------------------------

    def _build_messages(self, message: str) -> list[dict[str, Any]]:
        """Delegate: Minimal-Fallback — Implementierung in _provider_bridge."""
        return build_messages(message)

    def _build_messages_from_ctx(self, ctx: PipelineContext) -> list[dict[str, Any]]:
        """Delegate: Messages aus Context bauen — Implementierung in _provider_bridge."""
        return build_messages_from_ctx(ctx)

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
