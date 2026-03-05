"""CommandAddOn II — Alias, Ketten, Makros.

Erweitert CommandAddOn I um:
  Aliase:   !alias h history       — Kurzform
  Ketten:   !status && !history 5  — sequenziell, fail-fast
  Makros:   !macro save morning '!status && !history 5'
            !morning               — ausführen
            Persistent in SQLite

Verwendung:
    addon = CommandAddOnII(db_path="data/macros.db")

Alias/Makro-Lookup passiert vor normalem Dispatch:
    Input → Alias expandieren → Kette splitten → je Command dispatchen
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.models import AddOnResult, PipelineContext, ContextHistory
from addons.command.addon import (
    CommandAddOn, CommandRegistry, CommandContext, CommandResult,
    _parse, _dispatch,
)

logger = logging.getLogger(__name__)

_CHAIN_SEP = "&&"


# =============================================================================
# AliaStore — in-memory
# =============================================================================


class AliasStore:
    """In-memory Alias-Registry."""

    def __init__(self) -> None:
        self._aliases: dict[str, str] = {}

    def set(self, name: str, expansion: str) -> None:
        self._aliases[name.lower()] = expansion

    def get(self, name: str) -> str | None:
        return self._aliases.get(name.lower())

    def remove(self, name: str) -> bool:
        return self._aliases.pop(name.lower(), None) is not None

    def list_all(self) -> list[dict]:
        return [
            {"name": k, "expansion": v}
            for k, v in sorted(self._aliases.items())
        ]


# =============================================================================
# MacroStore — SQLite-persistent
# =============================================================================


class MacroStore:
    """SQLite-persistente Makro-Registry.

    Kein asyncpg — aiosqlite für Einfachheit.
    Öffnet Verbindung lazy bei erstem Zugriff.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            import aiosqlite
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS macros (
                    name TEXT PRIMARY KEY,
                    body TEXT NOT NULL
                )
            """)
            await self._conn.commit()
        return self._conn

    async def save(self, name: str, body: str) -> None:
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO macros (name, body) VALUES (?, ?)",
            (name.lower(), body)
        )
        await conn.commit()

    async def get(self, name: str) -> str | None:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT body FROM macros WHERE name = ?", (name.lower(),)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def delete(self, name: str) -> bool:
        conn = await self._get_conn()
        cur = await conn.execute(
            "DELETE FROM macros WHERE name = ?", (name.lower(),)
        )
        await conn.commit()
        return cur.rowcount > 0

    async def list_all(self) -> list[dict]:
        conn = await self._get_conn()
        async with conn.execute("SELECT name, body FROM macros ORDER BY name") as cur:
            rows = await cur.fetchall()
            return [{"name": r[0], "body": r[1]} for r in rows]

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


# =============================================================================
# CommandAddOnII
# =============================================================================


class CommandAddOnII(CommandAddOn):
    """CommandAddOn mit Alias, Ketten und persistenten Makros.

    Erbt von CommandAddOn — alle Commands aus I verfügbar.
    Dispatch-Reihenfolge:
      1. Makro?        → expandieren → weiter
      2. Alias?        → expandieren → weiter
      3. Kette (&&)?   → aufteilen, je dispatchen
      4. Normaler Dispatch via CommandRegistry
    """

    name = "command"

    def __init__(
        self,
        registry: CommandRegistry | None = None,
        db_path: str = ":memory:",
    ) -> None:
        super().__init__(registry)
        self._aliases = AliasStore()
        self._macros = MacroStore(db_path=db_path)
        self._register_alias_commands()
        self._register_macro_commands()

    async def on_detach(self, heinzel) -> None:
        await self._macros.close()
        await super().on_detach(heinzel)

    # -------------------------------------------------------------------------
    # Hook
    # -------------------------------------------------------------------------

    async def on_input_parsed(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        text = (ctx.parsed_input or "").strip()
        if not text.startswith("!"):
            return AddOnResult(modified_ctx=ctx)

        response = await self._execute_chain(text, ctx.session_id)
        updated = ctx.model_copy(update={"response": response})
        return AddOnResult(modified_ctx=updated, halt=True)

    # -------------------------------------------------------------------------
    # Ketten-Execution
    # -------------------------------------------------------------------------

    async def _execute_chain(self, text: str, session_id: str) -> str:
        """Kette ausführen — && trennt Commands. Fail-fast."""
        parts = _split_chain(text)
        results = []
        for part in parts:
            expanded = await self._expand(part.strip())
            cmd_ctx = _parse(expanded, session_id, self._heinzel)
            result = await _dispatch(cmd_ctx, self._registry)
            results.append(result.message)
            if not result.success:
                break  # fail-fast
        return "\n".join(r for r in results if r)

    async def _expand(self, text: str) -> str:
        """Makro → Alias → unveränderter Text."""
        if not text.startswith("!"):
            return text
        cmd_name = text[1:].split()[0].lower()

        # Makro zuerst
        macro_body = await self._macros.get(cmd_name)
        if macro_body:
            return macro_body

        # Alias
        alias_exp = self._aliases.get(cmd_name)
        if alias_exp:
            rest = text[1 + len(cmd_name):].strip()
            expanded = alias_exp if alias_exp.startswith("!") else f"!{alias_exp}"
            return f"{expanded} {rest}".strip() if rest else expanded

        return text

    # -------------------------------------------------------------------------
    # Alias-Commands
    # -------------------------------------------------------------------------

    def _register_alias_commands(self) -> None:
        reg = self._registry

        @reg.register("alias", description="Alias verwalten (!alias name expansion | !alias list | !alias remove name)")
        async def cmd_alias(ctx: CommandContext) -> CommandResult:
            args = ctx.args
            if not args or args[0] == "list":
                aliases = self._aliases.list_all()
                if not aliases:
                    return CommandResult(message="Keine Aliase definiert.")
                lines = [f"  !{a['name']} → {a['expansion']}" for a in aliases]
                return CommandResult(message="Aliase:\n" + "\n".join(lines))

            if args[0] == "remove":
                if len(args) < 2:
                    return CommandResult(success=False, message="!alias remove <name>")
                ok = self._aliases.remove(args[1])
                return CommandResult(
                    message=f"Alias '!{args[1]}' entfernt." if ok
                    else f"Alias '!{args[1]}' nicht gefunden.",
                    success=ok,
                )

            if len(args) < 2:
                return CommandResult(success=False, message="!alias <name> <expansion>")
            name, expansion = args[0], " ".join(args[1:])
            self._aliases.set(name, expansion)
            return CommandResult(message=f"Alias gesetzt: !{name} → {expansion}")

    # -------------------------------------------------------------------------
    # Makro-Commands
    # -------------------------------------------------------------------------

    def _register_macro_commands(self) -> None:
        reg = self._registry

        @reg.register("macro", description="Makro verwalten (!macro save name body | !macro list | !macro delete name)")
        async def cmd_macro(ctx: CommandContext) -> CommandResult:
            args = ctx.args
            if not args or args[0] == "list":
                macros = await self._macros.list_all()
                if not macros:
                    return CommandResult(message="Keine Makros gespeichert.")
                lines = [f"  !{m['name']}: {m['body']}" for m in macros]
                return CommandResult(message="Makros:\n" + "\n".join(lines))

            if args[0] == "delete":
                if len(args) < 2:
                    return CommandResult(success=False, message="!macro delete <name>")
                ok = await self._macros.delete(args[1])
                return CommandResult(
                    message=f"Makro '!{args[1]}' gelöscht." if ok
                    else f"Makro '!{args[1]}' nicht gefunden.",
                    success=ok,
                )

            if args[0] == "save":
                if len(args) < 3:
                    return CommandResult(success=False, message="!macro save <name> <body>")
                name = args[1]
                body = " ".join(args[2:])
                await self._macros.save(name, body)
                return CommandResult(message=f"Makro '!{name}' gespeichert: {body}")

            return CommandResult(success=False, message="!macro [save|delete|list]")


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _split_chain(text: str) -> list[str]:
    """!cmd1 && !cmd2 && !cmd3 → ['!cmd1', '!cmd2', '!cmd3']."""
    parts = text.split(f" {_CHAIN_SEP} ")
    return [p.strip() for p in parts if p.strip()]
