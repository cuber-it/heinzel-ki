"""_provider_bridge — LLM-Provider-Aufruf und Message-Bau fuer BaseHeinzel.

Package-intern: nicht in __init__.py exportiert.

Funktionen nehmen heinzel: BaseHeinzel als ersten Parameter (Option C).
TYPE_CHECKING-Guard verhindert zirkulaeren Import zur Runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .exceptions import ContextLengthExceededError
from .models import HookPoint, PipelineContext

if TYPE_CHECKING:
    from .base import BaseHeinzel

logger = logging.getLogger(__name__)


def build_messages_from_ctx(ctx: PipelineContext) -> list[dict[str, Any]]:
    """Messages aus Context bauen.

    Aufbau: [Working Memory History...] + [aktuelle User-Message]

    ctx.messages enthaelt Working Memory (prepended in ON_MEMORY_QUERY).
    Der aktuelle User-Input kommt immer als letzte Message dazu,
    damit das Modell weiss worauf es antworten soll.
    """
    current = {"role": "user", "content": ctx.parsed_input or ctx.raw_input}
    if ctx.messages:
        history = [{"role": m.role, "content": m.content} for m in ctx.messages]
        return history + [current]
    return [current]


async def call_provider(heinzel: BaseHeinzel, ctx: PipelineContext) -> PipelineContext:
    """LLM aufrufen und Response in neuen Context-Snapshot schreiben.

    Setzt loop_done=True als Fallback — kein LoopControl-AddOn vorhanden.
    Ein LoopControl-AddOn kann loop_done via modified_ctx auf False setzen.

    Turn-Safety: _in_turn-Flag verhindert Provider-Swap waehrend des Calls.
    Ein pending Provider wird nach dem Call aktiviert.
    """
    messages = build_messages_from_ctx(ctx)
    heinzel._in_turn = True
    try:
        response = await heinzel._provider.chat(
            messages=messages,
            system_prompt=ctx.system_prompt,
            model=ctx.model,
        )
    except ContextLengthExceededError as exc:
        # Lazy-Discovery: Limit merken, compact, einmal Retry
        logger.warning(
            "call_provider: Kontextfenster erschoepft (tokens_sent=%d, limit=%s) — compact + retry",
            exc.tokens_sent,
            exc.limit_discovered,
        )
        if exc.limit_discovered and hasattr(heinzel._provider, "context_window"):
            heinzel._provider.context_window = exc.limit_discovered
        working_memory = await heinzel._session_manager.get_working_memory(ctx.session_id)
        await working_memory.compact(keep_ratio=0.5)
        messages = build_messages_from_ctx(ctx)
        try:
            response = await heinzel._provider.chat(
                messages=messages,
                system_prompt=ctx.system_prompt,
                model=ctx.model,
            )
        except Exception as retry_exc:
            logger.error("call_provider: Retry nach compact fehlgeschlagen: %s", retry_exc)
            response = f"[Provider-Fehler nach compact: {retry_exc}]"
    except Exception as exc:
        logger.error("Provider-Fehler: %s", exc, exc_info=True)
        response = f"[Provider-Fehler: {exc}]"
    finally:
        heinzel._in_turn = False
        # Pending swap nach Turn-Ende anwenden
        if heinzel._pending_provider is not None:
            heinzel._provider = heinzel._pending_provider
            heinzel._pending_provider = None
            logger.info("set_provider: pending Provider aktiviert nach Turn-Ende")

    return ctx.evolve(
        phase=HookPoint.ON_LLM_RESPONSE,
        response=response,
        stream_buffer=response,
        loop_done=True,   # Fallback: Loop endet nach erstem Turn
    )
