"""_pipeline — Pipeline-Logik fuer Runner.

Package-intern: nicht in __init__.py exportiert.

Funktionen nehmen runner: Runner als ersten Parameter.
TYPE_CHECKING-Guard verhindert zirkulaeren Import zur Runtime.

Oeffentliche Helfer fuer base.py:
    run_pre_phases      — SESSION_START bis ON_CONTEXT_READY inkl. Memory
    run_post_phases     — ON_LOOP_END bis ON_SESSION_END inkl. Turn-Storage
    run_pipeline        — vollstaendiger nicht-streamender Durchlauf
    phase               — einzelne Phase: evolve + push + dispatch
    dispatch_and_apply  — Router aufrufen und modified_ctx anwenden
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import AddOnResult, ContextHistory, HookPoint, PipelineContext
from .session import Turn, WorkingMemory
from ._provider_bridge import call_provider, build_tool_result_message

if TYPE_CHECKING:
    from .runner import Runner

logger = logging.getLogger(__name__)


# =============================================================================
# Wiederverwendbare Phase-Helfer (fuer chat und chat_stream)
# =============================================================================


async def run_pre_phases(
    heinzel: Runner,
    message: str,
    session_id: str | None,
) -> tuple[str, WorkingMemory, PipelineContext, ContextHistory, bool]:
    """Vorphasen: SESSION_START bis ON_CONTEXT_READY inkl. Working Memory.

    Gibt (sid, working_memory, ctx, ctx_history, halted) zurueck.
    halted=True bedeutet: Pipeline soll abgebrochen werden.
    """
    sid, working_memory = await heinzel._ensure_session(session_id)
    ctx_history = ContextHistory()

    ctx = PipelineContext(
        raw_input=message,
        parsed_input=message,
        session_id=sid,
        agent_id=heinzel._agent_id,
        phase=HookPoint.ON_SESSION_START,
    )
    ctx_history.push(ctx)
    heinzel._dialog_log.log_user(message)

    halted = False
    for pre_phase in [
        HookPoint.ON_INPUT,
        HookPoint.ON_INPUT_PARSED,
        HookPoint.ON_MEMORY_QUERY,
        HookPoint.ON_CONTEXT_BUILD,
        HookPoint.ON_CONTEXT_READY,
    ]:
        ctx, halted = await phase(heinzel, pre_phase, ctx, ctx_history)
        if halted:
            break

        if pre_phase == HookPoint.ON_MEMORY_QUERY:
            wm_messages = await working_memory.get_context_messages()
            wm_tokens = working_memory.estimated_tokens()
            wm_turns = len(await working_memory.get_recent_turns(999))
            if wm_messages:
                ctx = ctx.evolve(
                    messages=wm_messages + ctx.messages,
                    working_memory_turns=wm_turns,
                    memory_tokens_used=wm_tokens,
                )
            else:
                ctx = ctx.evolve(
                    working_memory_turns=0,
                    memory_tokens_used=0,
                )

            memory_phase = (
                HookPoint.ON_MEMORY_HIT
                if ctx.memory_results
                else HookPoint.ON_MEMORY_MISS
            )
            ctx, halted = await phase(heinzel, memory_phase, ctx, ctx_history)
            if ctx.short_circuit or halted:
                halted = True
                break

    return sid, working_memory, ctx, ctx_history, halted


async def run_post_phases(
    heinzel: Runner,
    ctx: PipelineContext,
    ctx_history: ContextHistory,
    sid: str,
    message: str,
    response: str,
    working_memory: WorkingMemory,
) -> PipelineContext:
    """Nachphasen: ON_LOOP_END bis ON_SESSION_END inkl. Turn-Storage.

    Gibt den finalen ctx zurueck.
    """
    for post_phase in [
        HookPoint.ON_LOOP_END,
        HookPoint.ON_OUTPUT,
        HookPoint.ON_OUTPUT_SENT,
        HookPoint.ON_STORE,
        HookPoint.ON_STORED,
        HookPoint.ON_SESSION_END,
    ]:
        ctx, _ = await phase(heinzel, post_phase, ctx, ctx_history)
        if post_phase == HookPoint.ON_OUTPUT_SENT:
            heinzel._dialog_log.log_heinzel(response)
        elif post_phase == HookPoint.ON_STORED:
            turn = Turn(
                session_id=sid,
                raw_input=message,
                final_response=response,
                tokens_used=ctx.memory_tokens_used,
                history_depth=ctx.working_memory_turns,
            )
            await working_memory.add_turn(turn)
            await heinzel._session_manager.add_turn(sid, turn)

            # Compaction-Monitor: nach jedem Turn pruefen
            handover = await heinzel._maybe_compact(working_memory, sid)
            if handover is not None:
                # Rolling Session: ON_SESSION_ROLL feuern
                ctx, _ = await phase(
                    heinzel, HookPoint.ON_SESSION_ROLL, ctx, ctx_history
                )

    return ctx


# =============================================================================
# Vollstaendiger Pipeline-Durchlauf (nicht-streamend)
# =============================================================================


async def run_pipeline(
    heinzel: Runner,
    message: str,
    session_id: str | None,
) -> tuple[ContextHistory, PipelineContext]:
    """Vollstaendiger Pipeline-Durchlauf. Gibt (history, final_ctx) zurueck."""

    sid, working_memory, ctx, ctx_history, halted = await run_pre_phases(
        heinzel, message, session_id
    )

    if not halted:
        strategy = heinzel.reasoning_strategy

        # Strategy initialisieren — darf ctx anreichern (z.B. Ziele setzen)
        ctx_before = ctx
        ctx = await strategy.initialize(ctx, ctx_history)
        if ctx.snapshot_id != ctx_before.snapshot_id:
            ctx_history.push(ctx)

        iteration = 0
        while True:
            # Strategy plant naechsten Schritt
            plan = await strategy.plan_next_step(ctx, ctx_history)
            ctx = ctx.evolve(step_plan=plan)
            if plan.prompt_addition:
                ctx = ctx.evolve(
                    system_prompt=(ctx.system_prompt or "")
                    + "\n" + plan.prompt_addition
                )

            ctx, halted = await phase(heinzel, HookPoint.ON_LLM_REQUEST, ctx, ctx_history)
            if halted:
                break

            ctx = await call_provider(heinzel, ctx)
            ctx_history.push(ctx)

            ctx, halted = await dispatch_and_apply(
                heinzel, HookPoint.ON_LLM_RESPONSE, ctx, ctx_history
            )
            if halted:
                break

            # Tool-Loop: wenn das LLM Tool-Calls angefordert hat
            # ON_TOOL_REQUEST → MCPRouter fuehrt aus → ON_TOOL_RESULT → naechster LLM-Call
            if ctx.tool_requests:
                ctx, halted = await phase(
                    heinzel, HookPoint.ON_TOOL_REQUEST, ctx, ctx_history
                )
                if halted:
                    break

                # Tool-Ergebnis-Message fuer die History aufbauen
                if ctx.tool_results:
                    tool_result_msg = build_tool_result_message(ctx.tool_results)
                    existing_msgs = list(ctx.metadata.get("hnz_tool_messages", []))
                    existing_msgs.append(tool_result_msg)
                    ctx = ctx.evolve(
                        metadata={**ctx.metadata, "hnz_tool_messages": existing_msgs},
                        tool_requests=(),   # verarbeitet
                    )

                ctx, halted = await phase(
                    heinzel, HookPoint.ON_TOOL_RESULT, ctx, ctx_history
                )
                if halted:
                    break

                # Strategy bewertet Tool-Ergebnis
                if ctx.tool_results:
                    for tool_result in ctx.tool_results:
                        await strategy.on_tool_result(ctx, tool_result, ctx_history)

                # Weiter im Loop — naechster LLM-Call mit Tool-Ergebnissen
                iteration += 1
                ctx = ctx.evolve(
                    phase=HookPoint.ON_LOOP_ITERATION,
                    loop_iteration=iteration,
                    loop_done=False,
                    tool_results=(),   # verarbeitet
                )
                ctx_history.push(ctx)
                ctx, halted = await dispatch_and_apply(
                    heinzel, HookPoint.ON_LOOP_ITERATION, ctx, ctx_history
                )
                if halted:
                    break
                continue   # naechster LLM-Call

            # Strategy reflektiert nach jedem LLM-Call (kein Tool-Call)
            reflection = await strategy.reflect(ctx, ctx_history)
            ctx = ctx.evolve(reflection=reflection)

            # Zwei unabhaengige Loop-Kontrollen:
            #
            # Operative Ebene (ctx.loop_done): Provider setzt True als Default.
            # Ein LoopControl-AddOn kann auf False setzen um den Loop fortzusetzen.
            # Diese Ebene entscheidet allein — kein Strategy-Check danach.
            #
            # Kognitive Ebene (strategy.should_continue): Greift nur wenn
            # loop_done=True (kein AddOn hat den Loop offen gehalten).
            # Erlaubt Strategien (ReAct, ChainOfThought) einen eigenen
            # Denkloop unabhaengig vom operativen Loop zu steuern.
            if not ctx.loop_done:
                # AddOn haelt Loop explizit offen — operative Ebene entscheidet.
                pass
            elif not await strategy.should_continue(ctx, ctx_history):
                # loop_done=True und Strategy will keinen weiteren Schritt.
                break
            else:
                # loop_done=True aber Strategy will weitermachen (Reasoning-Loop).
                ctx = ctx.evolve(loop_done=False)

            iteration += 1
            ctx = ctx.evolve(
                phase=HookPoint.ON_LOOP_ITERATION,
                loop_iteration=iteration,
            )
            ctx_history.push(ctx)
            ctx, halted = await dispatch_and_apply(
                heinzel, HookPoint.ON_LOOP_ITERATION, ctx, ctx_history
            )
            if halted:
                break

    response = ctx.response or ctx.stream_buffer or ""
    ctx = await run_post_phases(
        heinzel, ctx, ctx_history, sid, message, response, working_memory
    )
    return ctx_history, ctx


# =============================================================================
# Niedrig-Level Phase-Helfer
# =============================================================================


async def phase(
    heinzel: Runner,
    hook: HookPoint,
    ctx: PipelineContext,
    ctx_history: ContextHistory,
) -> tuple[PipelineContext, bool]:
    """Einzelne Pipeline-Phase: evolve + push + dispatch."""
    ctx = ctx.evolve(phase=hook)
    ctx_history.push(ctx)
    return await dispatch_and_apply(heinzel, hook, ctx, ctx_history)


async def dispatch_and_apply(
    heinzel: Runner,
    hook: HookPoint,
    ctx: PipelineContext,
    ctx_history: ContextHistory,
) -> tuple[PipelineContext, bool]:
    """Router aufrufen und modified_ctx anwenden.

    Gibt (final_ctx, halt) zurueck.
    Exceptions aus AddOns werden abgefangen und als ON_ERROR dispatcht.
    """
    try:
        results: list[AddOnResult] = await heinzel._router.dispatch(
            hook, ctx, ctx_history
        )
    except Exception as exc:
        logger.error("Dispatch-Fehler bei %s: %s", hook, exc, exc_info=True)
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

    if heinzel._dialog_log.log_addons and results:
        for result in results:
            addon_name = getattr(result, "addon_name", "?")
            heinzel._dialog_log.log_addon(
                addon_name, hook.value, result.modified_ctx is not None
            )

    if heinzel._dialog_log.log_mcp:
        if hook == HookPoint.ON_TOOL_REQUEST and ctx.tool_requests:
            for tr in ctx.tool_requests:
                heinzel._dialog_log.log_mcp_request(
                    tr.tool_name, getattr(tr, "arguments", {})
                )
        elif hook == HookPoint.ON_TOOL_RESULT and ctx.tool_results:
            for tr in ctx.tool_results:
                heinzel._dialog_log.log_mcp_result(
                    tr.tool_name, not bool(getattr(tr, "error", None))
                )

    return ctx, halted
