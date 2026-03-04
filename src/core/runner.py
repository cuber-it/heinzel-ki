"""runner — agnostischer Laufzeit-Orchestrator.

Designprinzip:
    Runner kennt: LLM-Provider, AddOnRouter, Pipeline-Loop,
                  PipelineContext und ContextHistory.
    Runner kennt NICHT: Memory, Strategy, Goals, Estimator,
                        Evaluator, Session-Logik.

    Alles was ueber einen generischen LLM-Chatbot hinausgeht
    wird ausschliesslich via AddOns realisiert.

Fallbacks ohne AddOns:
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
from .exceptions import AddOnError, SessionNotFoundError
from .models import ContextHistory, HookPoint, PipelineContext
from .provider import LLMProvider
from .router import AddOnRouter
from .compaction import RollingSessionRegistry
from .reasoning import ReasoningStrategy, StrategyRegistry
from .models.placeholders import HandoverContext, ResourceBudget
from .session import SessionManager, WorkingMemory
from .session_noop import NoopSessionManager
from ._dialog_logger import _DialogLogger
from ._pipeline import (
    dispatch_and_apply, phase, run_pipeline,
    run_post_phases, run_pre_phases,
)
from ._provider_bridge import build_messages_from_ctx, call_provider

logger = logging.getLogger(__name__)


# =============================================================================
# Runner
# =============================================================================

class Runner:
    """Agnostischer Laufzeit-Orchestrator: Lifecycle + Pipeline-Loop + LLM-Aufruf.

    Alles darueber hinaus wird via AddOns realisiert.
    Kein Domänenwissen, kein Name, kein Charakter — rein strukturell.

    Verwendung:
        runner = Runner(provider=my_provider, name="test")
        runner.register_addon(my_addon, hooks={HookPoint.ON_INPUT})
        await runner.connect()
        response = await runner.chat("Hallo!")
        await runner.disconnect()
    """

    def __init__(
        self,
        provider: LLMProvider,
        name: str = "heinzel",
        agent_id: str | None = None,
        config: dict[str, Any] | None = None,
        config_path: str | None = None,
    ) -> None:
        self._provider = provider
        self._name = name
        self._agent_id = agent_id or str(uuid4())
        self._config: dict[str, Any] = self._load_config(config, config_path)
        self._router = AddOnRouter()
        self._addons: list[AddOn] = []   # Reihenfolge fuer Lifecycle
        self._connected = False
        self._dialog_log = _DialogLogger(self._agent_id, self._config)
        self._pending_provider: LLMProvider | None = None   # turn-safe swap
        self._in_turn: bool = False                         # laufender LLM-Call
        self._reasoning_strategy_name: str = "passthrough"  # Standard-Reasoning
        _mem_cfg = self._config.get("memory", {})
        _max_tokens: int = int(_mem_cfg.get("max_tokens", 128_000))
        _max_turns: int = int(_mem_cfg.get("max_turns", 10_000))
        self._session_manager: SessionManager = NoopSessionManager(
            max_tokens=_max_tokens,
            max_turns=_max_turns,
        )

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def agent_id(self) -> str:
        return self._agent_id

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

    @property
    def reasoning_strategy(self) -> ReasoningStrategy:
        """Aktuelle Reasoning-Strategie (Default: PassthroughStrategy)."""
        return (
            StrategyRegistry.get(self._reasoning_strategy_name)
            or StrategyRegistry.get_default()
        )

    def set_strategy(
        self,
        strategy: ReasoningStrategy | str,
    ) -> None:
        """Reasoning-Strategie zur Laufzeit wechseln.

        Akzeptiert Strategie-Objekt (wird registriert) oder
        registrierten Namen. Unbekannte Namen -> KeyError.

        Beispiel:
            heinzel.set_strategy("my_strategy")
            heinzel.set_strategy(MyStrategy())  # registriert + setzt
        """
        if isinstance(strategy, str):
            if StrategyRegistry.get(strategy) is None:
                raise KeyError(
                    f"Strategie '{strategy}' nicht registriert. "
                    f"Verfuegbar: {StrategyRegistry.list_available()}"
                )
            self._reasoning_strategy_name = strategy
        else:
            StrategyRegistry.register(strategy)
            self._reasoning_strategy_name = strategy.name
        logger.debug(
            "set_strategy: Strategie auf '%s' gesetzt.",
            self._reasoning_strategy_name,
        )

    async def _ensure_session(self, session_id: str | None) -> tuple[str, WorkingMemory]:
        """Lazy Session-Init: Session anlegen oder fortsetzen, Working Memory holen.

        Gibt (session_id, working_memory) zurueck.
        Wird beim ersten Turn in _run_pipeline() aufgerufen.
        """
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
                    self._agent_id, session_id=session_id
                )
        else:
            active = self._session_manager.active_session
            if active is not None:
                session = active
            else:
                session = await self._session_manager.create_session(self._agent_id)

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
        logger.info("Runner '%s' verbunden (%d AddOns)", self._name, len(self._addons))

    async def disconnect(self) -> None:
        """Alle AddOns in umgekehrter Reihenfolge stoppen."""
        for addon in reversed(self._addons):
            try:
                await addon.on_detach(self)
            except Exception as exc:
                logger.error("on_detach fehlgeschlagen fuer %s: %s", addon, exc)
        self._connected = False
        self._dialog_log.close()
        logger.info("Runner '%s' getrennt", self._name)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """Chat-Runde. Gibt immer str zurueck — nie Exception nach aussen."""
        try:
            message = await self.on_before_chat(message)
            ctx_history, final_ctx = await run_pipeline(self, message, session_id)
            response = final_ctx.response or final_ctx.stream_buffer or ""
            return await self.on_after_chat(response, ctx_history)
        except Exception as exc:
            logger.error("chat() Fehler: %s", exc, exc_info=True)
            return f"[Fehler: {exc}]"

    async def chat_stream(
        self, message: str, session_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Streaming Chat-Runde mit vollem Reasoning-Loop.

        Ablauf:
          - Reasoning-Schritte (next_action != "respond") laufen non-streaming
            intern durch — Strategy akkumuliert Trace in ctx.metadata.
          - Nur der finale Schritt (next_action == "respond") streamt.
          - PassthroughStrategy: erster Plan ist immer "respond" → direkt streamen.

        Damit funktioniert deep_reasoning/chain_of_thought vollstaendig
        auch im interaktiven CLI, waehrend einfache Strategien unveraendert
        bleiben.
        """
        try:
            message = await self.on_before_chat(message)
            sid, working_memory, ctx, ctx_history, halted = await run_pre_phases(
                self, message, session_id
            )
            if halted:
                return

            strategy = self.reasoning_strategy

            # Strategy initialisieren
            ctx_before = ctx
            ctx = await strategy.initialize(ctx, ctx_history)
            if ctx.snapshot_id != ctx_before.snapshot_id:
                ctx_history.push(ctx)

            # Reasoning-Loop: alle nicht-finalen Schritte non-streaming
            while True:
                plan = await strategy.plan_next_step(ctx, ctx_history)
                ctx = ctx.evolve(step_plan=plan)

                if plan.prompt_addition:
                    ctx = ctx.evolve(
                        system_prompt=(ctx.system_prompt or "")
                        + "\n" + plan.prompt_addition
                    )

                ctx, halted = await phase(
                    self, HookPoint.ON_LLM_REQUEST, ctx, ctx_history
                )
                if halted:
                    return

                if plan.next_action == "respond":
                    # Finaler Schritt — streamen und Loop beenden
                    break

                # Reasoning-Schritt: live streamen mit Phase-Header
                phase_name = ctx.metadata.get("hnz_rt_phase", plan.next_action)
                logger.debug(
                    "chat_stream: Reasoning-Schritt '%s' (iter %d)",
                    phase_name, ctx.loop_iteration,
                )
                yield f"\n\n▶ [{phase_name.upper()}]\n"
                step_buffer = ""
                try:
                    async for chunk in self._provider.stream(
                        messages=build_messages_from_ctx(ctx),
                        system_prompt=ctx.system_prompt,
                        model=ctx.model,
                    ):
                        step_buffer += chunk
                        yield chunk
                except Exception as exc:
                    logger.error("Reasoning-Stream-Fehler: %s", exc, exc_info=True)
                    step_buffer = f"[Fehler: {exc}]"
                    yield step_buffer

                yield "\n"
                ctx = ctx.evolve(
                    phase=HookPoint.ON_LLM_RESPONSE,
                    response=step_buffer,
                    stream_buffer=step_buffer,
                    loop_done=True,
                )
                ctx_history.push(ctx)

                ctx, halted = await dispatch_and_apply(
                    self, HookPoint.ON_LLM_RESPONSE, ctx, ctx_history
                )
                if halted:
                    return

                reflection = await strategy.reflect(ctx, ctx_history)
                ctx = ctx.evolve(reflection=reflection)

                # Kognitive Ebene: Strategy entscheidet ob weiterer Schritt
                if not await strategy.should_continue(ctx, ctx_history):
                    # Keine weiteren Reasoning-Schritte — direkt zur Synthese
                    plan_final = await strategy.plan_next_step(ctx, ctx_history)
                    ctx = ctx.evolve(step_plan=plan_final)
                    if plan_final.prompt_addition:
                        ctx = ctx.evolve(
                            system_prompt=(ctx.system_prompt or "")
                            + "\n" + plan_final.prompt_addition
                        )
                    ctx, _ = await phase(
                        self, HookPoint.ON_LLM_REQUEST, ctx, ctx_history
                    )
                    break

                iteration = ctx.loop_iteration + 1
                ctx = ctx.evolve(
                    phase=HookPoint.ON_LOOP_ITERATION,
                    loop_iteration=iteration,
                )
                ctx_history.push(ctx)
                ctx, halted = await dispatch_and_apply(
                    self, HookPoint.ON_LOOP_ITERATION, ctx, ctx_history
                )
                if halted:
                    return

            # Finaler Schritt: streamen (mit Header wenn Reasoning aktiv war)
            final_phase = ctx.metadata.get("hnz_rt_phase", "")
            if final_phase:
                yield f"\n\n▶ [ANTWORT]\n"
            stream_buffer = ""
            try:
                async for chunk in self._provider.stream(
                    messages=build_messages_from_ctx(ctx),
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
            ctx, _ = await dispatch_and_apply(
                self, HookPoint.ON_LLM_RESPONSE, ctx, ctx_history
            )

            reflection = await strategy.reflect(ctx, ctx_history)
            ctx = ctx.evolve(reflection=reflection)

            await run_post_phases(
                self, ctx, ctx_history, sid, message, stream_buffer, working_memory
            )

        except Exception as exc:
            logger.error("chat_stream() Fehler: %s", exc, exc_info=True)
            yield f"[Fehler: {exc}]"

    async def _run_pipeline(
        self, message: str, session_id: str | None
    ) -> tuple[ContextHistory, PipelineContext]:
        """Pipeline-Durchlauf — Implementierung in _pipeline."""
        return await run_pipeline(self, message, session_id)

    # -------------------------------------------------------------------------
    # Subklassen-Hooks
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Compaction + Rolling Session
    # -------------------------------------------------------------------------

    def _compaction_budget(self) -> ResourceBudget:
        """ResourceBudget aus Config oder Default."""
        mem_cfg = self._config.get("memory", {})
        return ResourceBudget(
            max_tokens=int(mem_cfg.get("max_tokens", 128_000)),
        )

    async def _build_handover_summary(
        self, turns: list, session_id: str
    ) -> str:
        """LLM-Call: destilliert die Session zu einem Handover-Summary.

        Nutzt den aktuellen Provider direkt — kein AddOn-Dispatch,
        kein Context-Update. Reiner destillierender Hilfs-Call.
        """
        if not turns:
            return "(leere Session)"

        # Turns als komprimierten Dialog aufbereiten
        lines = []
        for t in turns[-40:]:   # max 40 Turns als Input
            lines.append(f"User: {t.raw_input[:200]}")
            lines.append(f"Heinzel: {t.final_response[:200]}")
        dialog = "\n".join(lines)

        system = (
            "Du bist ein Archiv-Assistent. Deine Aufgabe ist es, "
            "eine Konversation in ein kompaktes Handover-Dokument "
            "zu destillieren. Fokus: Fakten, Entscheidungen, offene Ziele, "
            "wichtige Kontextinfo. Antworte nur mit dem "
            "Handover-Text, kein Kommentar davor oder danach."
        )
        prompt = (
            f"Handover-Dokument fuer Session "
            f"(ID: {session_id[:8]}):\n\n{dialog}"
        )

        try:
            from .models import Message
            summary = ""
            async for chunk in self._provider.stream(
                messages=(Message(role="user", content=prompt),),
                system_prompt=system,
            ):
                summary += chunk
            return summary.strip() or "(kein Summary erhalten)"
        except Exception as exc:
            logger.warning("Handover-LLM-Call fehlgeschlagen: %s", exc)
            return f"(LLM-Handover fehlgeschlagen: {exc})"

    async def _maybe_compact(
        self,
        working_memory: WorkingMemory,
        session_id: str,
    ) -> HandoverContext | None:
        """Compaction-Monitor: wird nach jedem Turn aufgerufen.

        Schwellen (Config: memory.compact_threshold / memory.roll_threshold):
            compact_threshold (default 0.80): compact() ausfuehren
            roll_threshold    (default 0.95): Rolling Session einleiten

        Gibt HandoverContext zurueck wenn gerollt wurde, sonst None.
        Der Aufrufer feuert ON_SESSION_ROLL.
        """
        mem_cfg = self._config.get("memory", {})
        compact_threshold = float(mem_cfg.get("compact_threshold", 0.80))
        roll_threshold = float(mem_cfg.get("roll_threshold", 0.95))

        budget = self._compaction_budget()
        tokens = working_memory.estimated_tokens()
        ratio = tokens / budget.max_tokens if budget.max_tokens else 0.0

        if ratio < compact_threshold:
            return None   # Alles gut

        if ratio >= roll_threshold:
            # Rolling Session: LLM baut Handover, dann Context-Reset
            logger.info(
                "Rolling Session: %.0f%% Kontext — starte Handover",
                ratio * 100,
            )
            n = 9999
            turns = await working_memory.get_recent_turns(n)
            summary = await self._build_handover_summary(
                turns, session_id
            )

            policy = RollingSessionRegistry.get_default()
            from .compaction import CompactionResult
            compaction_result = CompactionResult(
                kept_turns=(),
                dropped_turns=tuple(turns),
                summary=summary,
                tokens_before=tokens,
                tokens_after=0,
                tokens_saved=tokens,
                critical_preserved=True,
            )
            session = self._session_manager.active_session
            if session is None:
                return None

            handover = await policy.create_handover(
                session, compaction_result
            )
            # Handover-Summary in HandoverContext eintragen
            handover = handover.model_copy(
                update={"summary": summary}
            )

            # Rolling Session via SessionManager
            await self._session_manager.end_session(session_id)
            new_session = await self._session_manager.create_session(
                agent_id=self._agent_id,
                user_id=session.user_id,
            )
            # Handover in neue Session-Metadata
            from .session_noop import NoopSessionManager
            sm = self._session_manager
            if isinstance(sm, NoopSessionManager):
                sm._sessions[new_session.id] = new_session.model_copy(
                    update={"metadata": {"handover": handover}}
                )
                sm._active = sm._sessions[new_session.id]
            # Working Memory der neuen Session mit Handover-Turn befuellen
            new_wm = await self._session_manager.get_working_memory(
                new_session.id)
            from .session import Turn as _Turn
            handover_turn = _Turn(
                session_id=new_session.id,
                raw_input="[Session-Handover]",
                final_response=summary,
            )
            await new_wm.add_turn(handover_turn)

            logger.info(
                "Rolling Session abgeschlossen — neue Session %s",
                new_session.id[:8],
            )
            return handover

        # Normale Compaction
        logger.info(
            "Compaction: %.0f%% Kontext — kompaktiere",
            ratio * 100,
        )
        await working_memory.compact()
        return None

    async def on_before_chat(self, message: str) -> str:
        """Optionaler Hook vor der Pipeline. Kann message transformieren."""
        return message

    async def on_after_chat(self, response: str, ctx_history: ContextHistory) -> str:
        """Optionaler Hook nach der Pipeline. Kann response transformieren."""
        return response

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
