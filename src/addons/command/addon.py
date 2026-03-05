"""CommandAddOn — !-Kommandos für Heinzel.

Erkennt !-Prefix im Input, dispatcht an registrierte Handler.
Zwei Teile:
  CommandAddOn   — Hook ON_INPUT_PARSED, Parsing + Dispatch
  CommandRegistry — Handler registrieren/verwalten

Format: !command [arg1] [arg2] ...

Verwendung:
    registry = CommandRegistry()

    @registry.register("history", description="Zeigt Gesprächsverlauf")
    async def cmd_history(ctx: CommandContext) -> CommandResult:
        n = int(ctx.args[0]) if ctx.args else 10
        return CommandResult(success=True, message=f"Letzte {n} Einträge...")

Konfiguration: keine — Commands werden per Code registriert.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from core.addon import AddOn
from core.models import AddOnResult, PipelineContext, ContextHistory

logger = logging.getLogger(__name__)


# =============================================================================
# Datenmodelle
# =============================================================================


@dataclass
class CommandContext:
    """Kontext für einen Command-Handler."""

    command: str                         # "history"
    args: list[str]                      # ["10"]
    raw: str                             # "!history 10"
    session_id: str = ""
    heinzel: Any = None                  # Runner-Referenz


@dataclass
class CommandResult:
    """Ergebnis eines Command-Handlers."""

    success: bool = True
    message: str = ""
    data: Any = None


# =============================================================================
# CommandRegistry
# =============================================================================


@dataclass
class _CommandEntry:
    name: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]
    description: str = ""
    usage: str = ""


class CommandRegistry:
    """Registry für Command-Handler."""

    def __init__(self) -> None:
        self._commands: dict[str, _CommandEntry] = {}

    def register(
        self,
        name: str,
        description: str = "",
        usage: str = "",
    ) -> Callable:
        """Decorator zum Registrieren eines Handlers.

        @registry.register("history", description="Verlauf anzeigen")
        async def cmd_history(ctx: CommandContext) -> CommandResult:
            ...
        """
        def decorator(fn: Callable) -> Callable:
            self._commands[name.lower()] = _CommandEntry(
                name=name.lower(),
                handler=fn,
                description=description,
                usage=usage or f"!{name}",
            )
            return fn
        return decorator

    def add(
        self,
        name: str,
        handler: Callable[[CommandContext], Awaitable[CommandResult]],
        description: str = "",
        usage: str = "",
    ) -> None:
        """Handler direkt (ohne Decorator) registrieren."""
        self._commands[name.lower()] = _CommandEntry(
            name=name.lower(),
            handler=handler,
            description=description,
            usage=usage or f"!{name}",
        )

    def get(self, name: str) -> _CommandEntry | None:
        return self._commands.get(name.lower())

    def list_commands(self) -> list[dict]:
        return [
            {"name": e.name, "description": e.description, "usage": e.usage}
            for e in sorted(self._commands.values(), key=lambda e: e.name)
        ]

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._commands


# =============================================================================
# CommandAddOn
# =============================================================================


class CommandAddOn(AddOn):
    """Erkennt !-Kommandos und dispatcht an CommandRegistry.

    Hook: ON_INPUT_PARSED
    Wenn Input mit ! beginnt:
      - Parsen → CommandContext
      - Handler aufrufen → CommandResult
      - ctx.response setzen → Pipeline stoppt (kein LLM-Call)
    Wenn kein !:
      - Context unverändert weiterleiten
    """

    name = "command"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or CommandRegistry()
        self._heinzel = None
        # Eingebaute Commands registrieren
        self._register_builtins()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._heinzel = heinzel
        logger.info(
            f"[CommandAddOn] bereit — "
            f"{len(self._registry._commands)} Commands registriert"
        )

    async def on_detach(self, heinzel) -> None:
        self._heinzel = None

    # -------------------------------------------------------------------------
    # Hook
    # -------------------------------------------------------------------------

    async def on_input_parsed(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        text = (ctx.parsed_input or "").strip()

        if not text.startswith("!"):
            return AddOnResult(modified_ctx=ctx)

        cmd_ctx = _parse(text, ctx.session_id, self._heinzel)
        result = await _dispatch(cmd_ctx, self._registry)

        response = result.message if result.success else f"[Fehler] {result.message}"
        updated = ctx.model_copy(update={"response": response})
        # Signal: Pipeline soll nach diesem Hook stoppen — kein LLM-Call
        return AddOnResult(modified_ctx=updated, halt=True)

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    @property
    def registry(self) -> CommandRegistry:
        return self._registry

    # -------------------------------------------------------------------------
    # Eingebaute Commands
    # -------------------------------------------------------------------------

    def _register_builtins(self) -> None:
        registry = self._registry

        @registry.register("help", description="Zeigt verfügbare Commands")
        async def cmd_help(ctx: CommandContext) -> CommandResult:
            commands = registry.list_commands()
            if not commands:
                return CommandResult(message="Keine Commands registriert.")
            lines = ["Verfügbare Commands:"]
            for c in commands:
                desc = f" — {c['description']}" if c["description"] else ""
                lines.append(f"  !{c['name']}{desc}")
            return CommandResult(message="\n".join(lines))


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _parse(text: str, session_id: str, heinzel: Any) -> CommandContext:
    """!command [args...] parsen."""
    try:
        parts = shlex.split(text[1:])  # ! entfernen, dann shell-like parsen
    except ValueError:
        parts = text[1:].split()

    command = parts[0].lower() if parts else ""
    args = parts[1:] if len(parts) > 1 else []

    return CommandContext(
        command=command,
        args=args,
        raw=text,
        session_id=session_id,
        heinzel=heinzel,
    )


async def _dispatch(ctx: CommandContext, registry: CommandRegistry) -> CommandResult:
    """Handler aufrufen oder Fehlermeldung zurückgeben."""
    entry = registry.get(ctx.command)
    if entry is None:
        known = [e.name for e in registry._commands.values()]
        hint = f"Bekannte Commands: {', '.join(sorted(known))}" if known else ""
        return CommandResult(
            success=False,
            message=f"Unbekannter Command: '!{ctx.command}'. {hint}".strip(),
        )

    try:
        return await entry.handler(ctx)
    except Exception as exc:
        logger.error(f"[CommandAddOn] Fehler in '!{ctx.command}': {exc}")
        return CommandResult(success=False, message=f"Fehler: {exc}")
