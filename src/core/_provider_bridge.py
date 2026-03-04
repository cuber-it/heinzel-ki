"""_provider_bridge — LLM-Provider-Aufruf und Message-Bau fuer Runner.

Package-intern: nicht in __init__.py exportiert.

Funktionen nehmen runner: Runner als ersten Parameter.
TYPE_CHECKING-Guard verhindert zirkulaeren Import zur Runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .exceptions import ContextLengthExceededError
from .models import HookPoint, PipelineContext
from .models.base import ToolCall, ToolResult

if TYPE_CHECKING:
    from .runner import Runner

logger = logging.getLogger(__name__)


def build_messages_from_ctx(ctx: PipelineContext) -> list[dict[str, Any]]:
    """Messages aus Context bauen.

    Aufbau: [Working Memory History...] + [aktuelle User-Message]
    Fuer tool_use/tool_result: strukturierte content_blocks einbetten.

    ctx.messages enthaelt Working Memory (prepended in ON_MEMORY_QUERY).
    ctx.metadata["hnz_tool_messages"] enthaelt tool_use + tool_result Bloecke
    aus dem laufenden ReAct-Loop — werden zwischen User-Message und aktuellem
    Prompt eingefuegt.
    """
    current = {"role": "user", "content": ctx.parsed_input or ctx.raw_input}
    history = [{"role": m.role, "content": m.content} for m in ctx.messages] if ctx.messages else []

    # Reasoning-Messages: Gespraech zwischen Strategy-Phasen
    # Format: [user: phase-frage, assistant: phase-antwort, user: naechste-frage, ...]
    reasoning_messages: list[dict] = ctx.metadata.get("hnz_reasoning_messages", [])

    # Tool-Messages aus dem ReAct-Loop (tool_use + tool_result Paare)
    tool_messages: list[dict] = ctx.metadata.get("hnz_tool_messages", [])

    if reasoning_messages:
        # Aufbau: [history...] + [user: original-frage] + [reasoning-dialog] + [tool-messages]
        return history + [current] + reasoning_messages + tool_messages
    if tool_messages:
        return history + [current] + tool_messages
    return history + [current]


def _parse_tool_calls(content_blocks: list[dict[str, Any]]) -> list[ToolCall]:
    """Extrahiert ToolCall-Objekte aus Anthropic content_blocks.

    Erkennt Bloecke mit type=='tool_use' und baut ToolCall-Objekte daraus.
    """
    calls = []
    for block in content_blocks:
        if block.get("type") == "tool_use":
            calls.append(ToolCall(
                call_id=block.get("id", ""),
                tool_name=block.get("name", ""),
                args=block.get("input", {}),
            ))
    return calls


def build_tool_use_message(content_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Baut eine assistant-Message mit tool_use-Bloecken fuer die History."""
    return {"role": "assistant", "content": content_blocks}


def build_tool_result_message(tool_results: tuple) -> dict[str, Any]:
    """Baut eine user-Message mit tool_result-Bloecken fuer die History."""
    blocks = []
    for result in tool_results:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": result.call_id,
        }
        if result.error:
            block["content"] = f"[Fehler: {result.error}]"
            block["is_error"] = True
        else:
            content = result.result
            block["content"] = str(content) if not isinstance(content, str) else content
        blocks.append(block)
    return {"role": "user", "content": blocks}


async def call_provider(heinzel: Runner, ctx: PipelineContext) -> PipelineContext:
    """LLM aufrufen und Response in neuen Context-Snapshot schreiben.

    Setzt loop_done=True als Fallback — kein LoopControl-AddOn vorhanden.
    Ein LoopControl-AddOn kann loop_done via modified_ctx auf False setzen.

    Turn-Safety: _in_turn-Flag verhindert Provider-Swap waehrend des Calls.
    Ein pending Provider wird nach dem Call aktiviert.
    """
    messages = build_messages_from_ctx(ctx)
    tools: list[dict[str, Any]] | None = ctx.metadata.get("hnz_tools") or None
    heinzel._in_turn = True
    content_blocks: list[dict[str, Any]] = []
    try:
        if tools:
            response, content_blocks = await heinzel._provider.chat_tools(
                messages=messages,
                system_prompt=ctx.system_prompt,
                model=ctx.model,
                tools=tools,
            )
        else:
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

    # Tool-Calls aus content_blocks extrahieren
    tool_calls = _parse_tool_calls(content_blocks)

    # tool_use-Blöcke für die Message-History merken (ReAct-Loop)
    new_meta = dict(ctx.metadata)
    if content_blocks and tool_calls:
        existing = list(ctx.metadata.get("hnz_tool_messages", []))
        existing.append(build_tool_use_message(content_blocks))
        new_meta["hnz_tool_messages"] = existing

    return ctx.evolve(
        phase=HookPoint.ON_LLM_RESPONSE,
        response=response,
        stream_buffer=response,
        tool_requests=tuple(tool_calls),
        loop_done=not bool(tool_calls),   # Tool-Calls? Loop läuft weiter.
        metadata=new_meta,
    )
