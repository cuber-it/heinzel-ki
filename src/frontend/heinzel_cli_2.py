#!/usr/bin/env python3
"""heinzel_cli_2.py — Interaktive CLI via HeinzelLoader.

Alle AddOns, Kommandos und Strategien kommen aus heinzel.yaml.
Die CLI selbst ist dumm: sie liest Input, delegiert alles und gibt aus.

Kommandos (via CommandAddOn + BuiltinCommandsAddOn):
  !help            — alle verfügbaren Kommandos
  !history [n]     — letzte n Turns
  !sessions        — letzte Sessions
  !skill list      — geladene Skills
  !provider        — Provider-Status
  !model <n>       — Model wechseln
  !status          — Gesamtübersicht
  !addons          — aktive AddOns
  !fact set k v    — Fact setzen
  !fact get k      — Fact lesen
  !quit / !exit    — beenden

Verwendung:
  python heinzel_cli_2.py
  python heinzel_cli_2.py --config config/riker.yaml
  python heinzel_cli_2.py --config config/riker.yaml --provider http://thebrain:12101
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import HookPoint
from core.startup import HeinzelLoader
from addons.command.addon import CommandAddOn
from addons.command.builtins import BuiltinCommandsAddOn


_QUIT_CMDS = {"!quit", "!exit", "!q"}


# =============================================================================
# REPL
# =============================================================================


async def run_repl(runner) -> None:
    """Hauptschleife — so dünn wie möglich."""
    _print_header(runner)

    try:
        while True:
            try:
                user_input = input("Du: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[Ctrl+C — tschüss]")
                break

            if not user_input:
                continue

            # Quit direkt — kein Roundtrip
            if user_input.lower() in _QUIT_CMDS:
                print("[Tschüss]")
                break

            if user_input.startswith("!"):
                # Kommando: chat() → CommandAddOn dispatcht, gibt ctx.response zurück
                response = await runner.chat(user_input)
                if response:
                    print(response)
            else:
                # Chat: stream
                print(f"{runner.name}: ", end="", flush=True)
                try:
                    async for chunk in runner.chat_stream(user_input):
                        print(chunk, end="", flush=True)
                    print()
                    await _print_status(runner)
                except Exception as exc:
                    print(f"\n[Fehler: {exc}]")

    finally:
        await runner.disconnect()


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _print_header(runner) -> None:
    """Kompakter Start-Header."""
    provider_url = getattr(runner._provider, "_base_url", "?")
    model = getattr(runner._provider, "_model", "") or "(auto)"
    strategy = runner.reasoning_strategy.name
    addons = [a.name for a in runner._addons]

    print(f"\n{'─' * 56}")
    print(f"  {runner.name}  ·  {runner.agent_id[:12]}…")
    print(f"  Provider : {provider_url}  [{model}]")
    print(f"  Strategie: {strategy}")
    print(f"  AddOns   : {', '.join(addons) or '—'}")
    print(f"  !help für Kommandos  ·  !quit zum Beenden")
    print(f"{'─' * 56}\n")


async def _print_status(runner) -> None:
    """Einzeilige Kontextanzeige nach jeder Antwort."""
    try:
        session = runner.session_manager.active_session
        if session is None:
            return
        wm = await runner.session_manager.get_working_memory(session.id)
        tokens = wm.estimated_tokens()
        cw = getattr(runner._provider, "context_window", None) or wm.max_tokens
        pct = int(tokens / cw * 100) if cw else 0
        turns = session.turn_count
        print(f"\033[2m[{turns} Turns | ~{tokens:,} Token | {pct}%]\033[0m")
    except Exception:
        pass  # Status ist nice-to-have


# =============================================================================
# Setup
# =============================================================================


async def _attach_late(runner, addon, hooks: set[HookPoint]) -> None:
    """AddOn nach connect() einhängen — register + on_attach manuell."""
    runner.register_addon(addon, hooks=hooks)
    await addon.on_attach(runner)


async def build_runner(config_path: str | None, provider_override: str | None):
    """Runner via HeinzelLoader bauen + CommandAddOns einhängen."""
    loader = HeinzelLoader(config_path)
    runner = await loader.build()

    if provider_override:
        try:
            runner._provider._base_url = provider_override
        except Exception:
            pass

    # CommandAddOn + BuiltinCommandsAddOn — falls nicht via YAML konfiguriert
    if runner.addons.get("command") is None:
        await _attach_late(runner, CommandAddOn(), {HookPoint.ON_INPUT_PARSED})

    if runner.addons.get("builtin_commands") is None:
        await _attach_late(runner, BuiltinCommandsAddOn(), {HookPoint.ON_SESSION_START})

    return runner


# =============================================================================
# Einstiegspunkt
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Heinzel CLI 2")
    parser.add_argument("--config", "-c", help="Pfad zur YAML-Config")
    parser.add_argument("--provider", "-p", help="Provider-URL (überschreibt Config)")
    args = parser.parse_args()

    async def _run():
        runner = await build_runner(args.config, args.provider)
        await run_repl(runner)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"[Startup-Fehler: {exc}]", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
