"""BuiltinCommandsAddOn — Eingebaute !-Commands für Heinzel.

Setzt CommandAddOn (I oder II) voraus — registriert dort die Handler.

Commands:
    Session & History:
        !history [n]          — letzte n Turns
        !!                    — letzten Input wiederholen
        !history redo [n]     — n-ten letzten Input wiederholen
        !sessions [limit]     — letzte Sessions
        !resume <id>          — Session wechseln
        !new                  — neue Session
        !end                  — aktuelle Session beenden

    Facts (In-Memory + DB optional):
        !fact set <key> <val>
        !fact get <key>
        !fact list / !facts
        !fact delete <key>
        !fact clear

    Skills:
        !skill list
        !skill load <name>
        !skill unload <name>
        !skill reload <name>

    Provider:
        !provider             — Status
        !provider switch <n>
        !provider list
        !model <n>
        !model list

    System:
        !status               — Gesamtübersicht
        !addons               — aktive AddOns
        !help [command]       — bereits in CommandAddOn
        !quit / !exit         — Session beenden + Signal

Konfiguration:
    Keine — wird über CommandAddOn registry verdrahtet.
"""

from __future__ import annotations

import logging
from typing import Any

from core.addon import AddOn
from core.models import AddOnResult, PipelineContext, ContextHistory
from addons.command.addon import CommandContext, CommandResult, CommandRegistry

logger = logging.getLogger(__name__)


class BuiltinCommandsAddOn(AddOn):
    """Registriert alle eingebauten Commands in der CommandRegistry.

    Muss NACH CommandAddOn attached werden.
    dependencies = ['command']
    """

    name = "builtin_commands"
    version = "0.1.0"
    dependencies: list[str] = ["command"]

    def __init__(self) -> None:
        self._facts: dict[str, str] = {}   # In-Memory Facts
        self._heinzel = None

    async def on_attach(self, heinzel) -> None:
        self._heinzel = heinzel
        cmd_addon = heinzel.addons.get("command")
        if cmd_addon is None:
            logger.error("[BuiltinCommandsAddOn] CommandAddOn nicht gefunden!")
            return
        registry = cmd_addon.registry
        self._register_all(registry)
        logger.info("[BuiltinCommandsAddOn] Commands registriert")

    async def on_detach(self, heinzel) -> None:
        self._heinzel = None

    # -------------------------------------------------------------------------
    # Registrierung
    # -------------------------------------------------------------------------

    def _register_all(self, registry: CommandRegistry) -> None:
        self._register_history(registry)
        self._register_sessions(registry)
        self._register_facts(registry)
        self._register_skills(registry)
        self._register_provider(registry)
        self._register_system(registry)

    # =========================================================================
    # HISTORY
    # =========================================================================

    def _register_history(self, registry: CommandRegistry) -> None:

        @registry.register("history", description="Letzte n Turns anzeigen (Default: 10)")
        async def cmd_history(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")

            # redo-Subcommand
            if ctx.args and ctx.args[0] == "redo":
                n = int(ctx.args[1]) if len(ctx.args) > 1 else 1
                return await _redo(runner, n)

            n = int(ctx.args[0]) if ctx.args else 10
            sm = runner._session_manager
            session = sm.active_session
            if session is None:
                return CommandResult(message="Keine aktive Session.")

            turns = await sm.get_turns(session.id, limit=n)
            if not turns:
                return CommandResult(message="Keine Turns in dieser Session.")

            lines = []
            for i, t in enumerate(turns, 1):
                inp = t.raw_input[:80].replace("\n", " ")
                resp = t.final_response[:80].replace("\n", " ")
                lines.append(f"[{i}] You: {inp}")
                lines.append(f"     Heinzel: {resp}")
            return CommandResult(message="\n".join(lines))

        @registry.register("!", description="Letzten Input wiederholen (wie !! in bash)")
        async def cmd_repeat(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            return await _redo(runner, 1)

    # =========================================================================
    # SESSIONS
    # =========================================================================

    def _register_sessions(self, registry: CommandRegistry) -> None:

        @registry.register("sessions", description="Letzte Sessions anzeigen")
        async def cmd_sessions(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            limit = int(ctx.args[0]) if ctx.args else 10
            sm = runner._session_manager
            sessions = await sm.list_sessions(limit=limit)
            if not sessions:
                return CommandResult(message="Keine Sessions gefunden.")
            lines = []
            for s in sessions:
                active = " ← aktiv" if sm.active_session and sm.active_session.id == s.id else ""
                lines.append(
                    f"  {s.id[:8]}… | {s.started_at.strftime('%Y-%m-%d %H:%M')} "
                    f"| {s.turn_count} Turns{active}"
                )
            return CommandResult(message="Sessions:\n" + "\n".join(lines))

        @registry.register("resume", description="Session fortsetzen: !resume <id>")
        async def cmd_resume(ctx: CommandContext) -> CommandResult:
            if not ctx.args:
                return CommandResult(success=False, message="!resume <session-id>")
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            sid = ctx.args[0]
            try:
                session = await runner._session_manager.resume_session(sid)
                return CommandResult(message=f"Session {session.id[:8]}… fortgesetzt.")
            except Exception as exc:
                return CommandResult(success=False, message=f"Fehler: {exc}")

        @registry.register("new", description="Neue Session starten")
        async def cmd_new(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            session = await runner._session_manager.create_session(
                agent_id=runner.agent_id
            )
            return CommandResult(message=f"Neue Session: {session.id[:8]}…")

        @registry.register("end", description="Aktuelle Session beenden")
        async def cmd_end(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            sm = runner._session_manager
            if sm.active_session is None:
                return CommandResult(message="Keine aktive Session.")
            await sm.end_session(sm.active_session.id)
            return CommandResult(message="Session beendet.")

    # =========================================================================
    # FACTS
    # =========================================================================

    def _register_facts(self, registry: CommandRegistry) -> None:
        facts = self._facts  # closure

        async def _fact_dispatch(ctx: CommandContext) -> CommandResult:
            sub = ctx.args[0].lower() if ctx.args else "list"

            if sub == "set":
                if len(ctx.args) < 3:
                    return CommandResult(success=False, message="!fact set <key> <value>")
                key, value = ctx.args[1], " ".join(ctx.args[2:])
                facts[key] = value
                # DB optional
                await _db_fact_set(ctx, key, value)
                return CommandResult(message=f"Fact gesetzt: {key} = {value}")

            if sub == "get":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!fact get <key>")
                key = ctx.args[1]
                val = facts.get(key)
                if val is None:
                    return CommandResult(success=False, message=f"Fact '{key}' nicht gefunden.")
                return CommandResult(message=f"{key} = {val}")

            if sub in ("list", ""):
                if not facts:
                    return CommandResult(message="Keine Facts gespeichert.")
                lines = [f"  {k} = {v}" for k, v in sorted(facts.items())]
                return CommandResult(message="Facts:\n" + "\n".join(lines))

            if sub == "delete":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!fact delete <key>")
                key = ctx.args[1]
                if key not in facts:
                    return CommandResult(success=False, message=f"'{key}' nicht gefunden.")
                del facts[key]
                return CommandResult(message=f"Fact '{key}' gelöscht.")

            if sub == "clear":
                count = len(facts)
                facts.clear()
                return CommandResult(message=f"{count} Facts gelöscht.")

            return CommandResult(success=False, message=f"Unbekannter Subcommand: {sub}")

        registry.add("fact", _fact_dispatch, description="Facts verwalten: !fact [set|get|list|delete|clear]")
        registry.add("facts", _fact_dispatch, description="Kurzform für !fact list")

    # =========================================================================
    # SKILLS
    # =========================================================================

    def _register_skills(self, registry: CommandRegistry) -> None:

        @registry.register("skill", description="Skills verwalten: !skill [list|load|unload|reload]")
        async def cmd_skill(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            skills_addon = runner.addons.get("skills") if runner else None
            sub = ctx.args[0].lower() if ctx.args else "list"

            if sub == "list":
                if skills_addon is None:
                    return CommandResult(message="SkillsAddOn nicht geladen.")
                skills = getattr(skills_addon, "_skills", {})
                if not skills:
                    return CommandResult(message="Keine Skills geladen.")
                lines = [f"  {name}" for name in sorted(skills)]
                return CommandResult(message="Skills:\n" + "\n".join(lines))

            if sub == "load":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!skill load <name>")
                if skills_addon and hasattr(skills_addon, "load"):
                    try:
                        await skills_addon.load(ctx.args[1])
                        return CommandResult(message=f"Skill '{ctx.args[1]}' geladen.")
                    except Exception as exc:
                        return CommandResult(success=False, message=str(exc))
                return CommandResult(success=False, message="SkillsAddOn unterstützt kein dynamisches Laden.")

            if sub == "unload":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!skill unload <name>")
                if skills_addon and hasattr(skills_addon, "unload"):
                    skills_addon.unload(ctx.args[1])
                    return CommandResult(message=f"Skill '{ctx.args[1]}' entladen.")
                return CommandResult(success=False, message="SkillsAddOn unterstützt kein Entladen.")

            if sub == "reload":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!skill reload <name>")
                if skills_addon and hasattr(skills_addon, "reload"):
                    await skills_addon.reload(ctx.args[1])
                    return CommandResult(message=f"Skill '{ctx.args[1]}' neu geladen.")
                return CommandResult(success=False, message="SkillsAddOn unterstützt kein Reload.")

            return CommandResult(success=False, message=f"Unbekannt: {sub}")

    # =========================================================================
    # PROVIDER
    # =========================================================================

    def _register_provider(self, registry: CommandRegistry) -> None:

        @registry.register("provider", description="Provider-Status oder wechseln: !provider [switch <name>|list]")
        async def cmd_provider(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")

            sub = ctx.args[0].lower() if ctx.args else "status"

            if sub == "status" or not ctx.args:
                p = runner._provider
                name = getattr(p, "_name", type(p).__name__)
                model = getattr(p, "_model", "?")
                url = getattr(p, "_url", "?")
                return CommandResult(message=f"Provider: {name}\nModel: {model}\nURL: {url}")

            if sub == "switch":
                if len(ctx.args) < 2:
                    return CommandResult(success=False, message="!provider switch <name>")
                # ProviderRegistry optional
                reg = getattr(runner, "_provider_registry", None)
                if reg and hasattr(reg, "switch_to"):
                    ok = await reg.switch_to(ctx.args[1])
                    return CommandResult(
                        success=ok,
                        message=f"Gewechselt zu '{ctx.args[1]}'." if ok
                        else f"Provider '{ctx.args[1]}' nicht gefunden."
                    )
                return CommandResult(success=False, message="Kein ProviderRegistry verfügbar.")

            if sub == "list":
                reg = getattr(runner, "_provider_registry", None)
                if reg:
                    providers = list(reg._providers.keys()) if hasattr(reg, "_providers") else []
                    return CommandResult(message="Provider:\n" + "\n".join(f"  {p}" for p in providers))
                return CommandResult(message="Kein ProviderRegistry verfügbar.")

            return CommandResult(success=False, message=f"Unbekannt: {sub}")

        @registry.register("model", description="Model setzen oder anzeigen: !model [<name>|list]")
        async def cmd_model(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            p = runner._provider
            current = getattr(p, "_model", "?")

            if not ctx.args or ctx.args[0] == "list":
                return CommandResult(message=f"Aktuelles Model: {current}")

            new_model = ctx.args[0]
            if hasattr(p, "_model"):
                object.__setattr__(p, "_model", new_model) if hasattr(p, "__slots__") else setattr(p, "_model", new_model)
                return CommandResult(message=f"Model gewechselt: {current} → {new_model}")
            return CommandResult(success=False, message="Provider unterstützt kein Model-Wechsel.")

    # =========================================================================
    # SYSTEM
    # =========================================================================

    def _register_system(self, registry: CommandRegistry) -> None:

        @registry.register("status", description="Gesamtübersicht: Session, Provider, AddOns")
        async def cmd_status(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")

            sm = runner._session_manager
            session = sm.active_session
            p = runner._provider
            provider_name = getattr(p, "_name", type(p).__name__)
            model = getattr(p, "_model", "?")

            lines = [
                f"Heinzel:   {runner._name} ({runner.agent_id[:8]}…)",
                f"Provider:  {provider_name} / {model}",
            ]
            if session:
                wm = await sm.get_working_memory(session.id)
                lines += [
                    f"Session:   {session.id[:8]}… | {session.turn_count} Turns",
                    f"Kontext:   ~{wm.estimated_tokens} Tokens",
                ]
            else:
                lines.append("Session:   keine")

            lines.append(f"AddOns:    {len(runner._addons)}")
            return CommandResult(message="\n".join(lines))

        @registry.register("addons", description="Aktive AddOns mit Hooks")
        async def cmd_addons(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner is None:
                return CommandResult(success=False, message="Kein Runner verfügbar.")
            if not runner._addons:
                return CommandResult(message="Keine AddOns geladen.")
            lines = []
            for a in runner._addons:
                entry = runner._router._entries.get(a.name)
                hooks = sorted(h.name for h in entry.hooks) if entry else []
                hook_str = ", ".join(hooks) if hooks else "lifecycle-only"
                lines.append(f"  {a.name} v{getattr(a, 'version', '?')} [{hook_str}]")
            return CommandResult(message="AddOns:\n" + "\n".join(lines))

        @registry.register("quit", description="Heinzel beenden")
        async def cmd_quit(ctx: CommandContext) -> CommandResult:
            runner = _runner(ctx)
            if runner:
                sm = runner._session_manager
                if sm.active_session:
                    await sm.end_session(sm.active_session.id)
            return CommandResult(message="Tschüss!", data={"quit": True})

        # !exit als Alias für !quit
        registry.add(
            "exit",
            cmd_quit,
            description="Heinzel beenden (Alias für !quit)",
        )


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _runner(ctx: CommandContext) -> Any | None:
    """Runner aus CommandContext holen."""
    heinzel = ctx.heinzel
    if heinzel is None:
        return None
    return getattr(heinzel, "runner", heinzel)


async def _redo(runner: Any, n: int = 1) -> CommandResult:
    """n-ten letzten Input wiederholen."""
    sm = runner._session_manager
    session = sm.active_session
    if session is None:
        return CommandResult(success=False, message="Keine aktive Session.")
    turns = await sm.get_turns(session.id, limit=n)
    if len(turns) < n:
        return CommandResult(success=False, message=f"Nur {len(turns)} Turns verfügbar.")
    target = turns[-n] if n <= len(turns) else turns[0]
    response = await runner.chat(target.raw_input)
    return CommandResult(message=response)


async def _db_fact_set(ctx: CommandContext, key: str, value: str) -> None:
    """Fact optional in DatabaseAddOn persistieren."""
    try:
        runner = _runner(ctx)
        if runner is None:
            return
        db = runner.addons.get("database")
        if db is None:
            return
        agent_id = runner.agent_id
        await db.execute(
            "INSERT OR REPLACE INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
            agent_id, key, value,
        )
    except Exception as exc:
        logger.debug(f"[BuiltinCommands] DB-Fact-Set fehlgeschlagen: {exc}")
