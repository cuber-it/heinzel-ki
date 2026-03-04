#!/home/ucuber/Workspace/heinzel-ki/.venv/bin/python3
"""heinzel_cli.py — Interaktiver Heinzel auf dem Core-Runner.

Demonstriert und nutzt alle Core-Features:
  - Runner mit HttpLLMProvider
  - NoopSessionManager mit Session-Tracking
  - SummarizingCompactionStrategy (oder Truncation per Config)
  - PassthroughStrategy (Default) — erweiterbar per Config
  - AgentConfig via YAML oder Defaults

Kommandos:
  !quit      — Session beenden
  !history   — Dialoglog der aktuellen Session anzeigen
  !memory    — Working Memory Status (Turns, Tokens, Compaction)
  !session   — Session-Details
  !strategy  — Aktive Reasoning-Strategie anzeigen/wechseln
  !compact   — Compaction-Strategie anzeigen
  !config    — Aktive Konfiguration anzeigen

Config-YAML (optional, Suchpfad: ./heinzel.yaml, ./config/heinzel.yaml):

  agent:
    id: mein-heinzel
    name: Heinzel
    role: assistant
    goal: Helfe dem Nutzer so gut wie moeglich.

  provider:
    url: http://localhost:12101
    model: ""

  memory:
    max_tokens: 128000
    max_turns: 10000
    compact_threshold: 0.80   # Compaction ab 80% Kontext
    roll_threshold: 0.95      # Rolling Session ab 95%
    compaction_strategy: summarizing   # summarizing | truncation

  reasoning:
    strategy: passthrough     # passthrough | (HNZ-003+: chain_of_thought etc.)

  logging:
    log_dir: ./logs
    log_addons: false

Verwendung:
  python heinzel_cli.py
  python heinzel_cli.py --config heinzel.yaml
  python heinzel_cli.py --provider http://localhost:12101
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import (
    CompactionRegistry,
    HttpLLMProvider,
    NoopSessionManager,
    Runner,
    StrategyRegistry,
    SummarizingCompactionStrategy,
    TruncationCompactionStrategy,
    find_config_file,
)


# =============================================================================
# Kommando-Handler
# =============================================================================


def handle_history(runner: Runner) -> None:
    """!history — Dialoglog der aktuellen Session ausgeben."""
    log_path = runner._dialog_log.log_path
    if log_path is None or not log_path.exists():
        print("[kein Log vorhanden]")
        return
    print(f"\n--- History: {log_path} ---")
    print(log_path.read_text(encoding="utf-8"))
    print("--- Ende ---\n")


async def handle_memory(runner: Runner) -> None:
    """!memory — Working Memory + Compaction-Status anzeigen."""
    session = runner.session_manager.active_session
    if session is None:
        print("[keine aktive Session]")
        return

    wm = await runner.session_manager.get_working_memory(session.id)
    turns = await wm.get_recent_turns(9999)
    tokens = wm.estimated_tokens()
    cw = runner.provider.context_window or wm.max_tokens

    compact_pct = int(tokens / cw * 100) if cw else 0
    compact_thresh = int(getattr(wm, "compact_threshold", 0.80) * 100)
    roll_thresh = int(getattr(wm, "roll_threshold", 0.95) * 100)

    strat_name = getattr(
        getattr(wm, "compaction_strategy", None), "name", "unbekannt"
    )

    print(f"\n--- Working Memory ---")
    print(f"  Session:     {session.id[:12]}...")
    print(f"  Turns:       {len(turns)} / {wm.max_turns}")
    print(f"  ~Token:      {tokens} / {cw} ({compact_pct}%)")
    print(f"  Compact ab:  {compact_thresh}%  |  Roll ab: {roll_thresh}%")
    print(f"  Strategie:   {strat_name}")
    if turns:
        first = turns[0].raw_input[:60].replace("\n", " ")
        last = turns[-1].raw_input[:60].replace("\n", " ")
        print(f"  Aeltester:   '{first}'")
        print(f"  Juengster:   '{last}'")
    print("--- Ende ---\n")


async def handle_session(runner: Runner) -> None:
    """!session — Session-Details."""
    session = runner.session_manager.active_session
    if session is None:
        print("[keine aktive Session]")
        return
    print(f"\n--- Session ---")
    print(f"  ID:       {session.id}")
    print(f"  Status:   {session.status}")
    print(f"  Turns:    {session.turn_count}")
    print(f"  Start:    {session.started_at.strftime('%H:%M:%S')}")
    print(f"  Zuletzt:  {session.last_active_at.strftime('%H:%M:%S')}")
    print("--- Ende ---\n")


def handle_strategy(runner: Runner, args: str) -> None:
    """!strategy [name] — Strategie anzeigen oder wechseln."""
    if not args:
        name = runner.reasoning_strategy.name
        desc = runner.reasoning_strategy.description
        available = StrategyRegistry.list_available()
        print(f"\n--- Reasoning-Strategie ---")
        print(f"  Aktiv:      {name}")
        print(f"  Info:       {desc}")
        print(f"  Verfuegbar: {', '.join(available)}")
        print("--- Ende ---\n")
    else:
        try:
            runner.set_strategy(args.strip())
            print(f"[Strategie gewechselt auf: {args.strip()}]")
        except KeyError:
            available = StrategyRegistry.list_available()
            print(
                f"[Unbekannte Strategie: '{args.strip()}' "
                f"— verfuegbar: {', '.join(available)}]"
            )


def handle_compact(runner: Runner) -> None:
    """!compact — Compaction-Strategie anzeigen."""
    available = CompactionRegistry.list_available()
    default = CompactionRegistry.get_default().name
    print(f"\n--- Compaction ---")
    print(f"  Default:    {default}")
    print(f"  Verfuegbar: {', '.join(available)}")
    print("--- Ende ---\n")


def handle_config(cfg: dict[str, Any]) -> None:
    """!config — Aktive Konfiguration anzeigen."""
    print("\n--- Aktive Config ---")
    for section, values in cfg.items():
        if isinstance(values, dict):
            print(f"  [{section}]")
            for k, v in values.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {section}: {values}")
    print("--- Ende ---\n")


# =============================================================================
# Status-Zeile nach jeder Antwort
# =============================================================================


async def status_line(runner: Runner) -> str:
    """Einzeilige Kontext-Anzeige."""
    session = runner.session_manager.active_session
    if session is None:
        return ""
    wm = await runner.session_manager.get_working_memory(session.id)
    turns = await wm.get_recent_turns(9999)
    tokens = wm.estimated_tokens()
    cw = runner.provider.context_window or wm.max_tokens
    pct = int(tokens / cw * 100) if cw else 0
    strategy = runner.reasoning_strategy.name
    return (
        f"[{len(turns)} Turns | ~{tokens} Token | "
        f"{pct}% | Strategie: {strategy}]"
    )


# =============================================================================
# REPL
# =============================================================================


async def run_repl(runner: Runner, cfg: dict[str, Any]) -> None:
    """Hauptschleife."""
    provider_url = cfg.get("provider", {}).get("url", "?")
    log_path = runner._dialog_log.log_path

    print(f"\n{'=' * 60}")
    print(f"  Agent:    {runner.name}  ({runner.agent_id[:8]}...)")
    print(f"  Provider: {provider_url}")
    print(f"  Strategie:{runner.reasoning_strategy.name}")
    print(f"  Log:      {log_path or '(kein Log)'}")
    print(f"  Kommandos: !quit  !history  !memory  !session")
    print(f"             !strategy [name]  !compact  !config")
    print(f"{'=' * 60}\n")

    await runner.connect()

    try:
        while True:
            try:
                user_input = input("Du: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[Ctrl+C — beende Session]")
                break

            if not user_input:
                continue

            cmd, _, rest = user_input.partition(" ")
            cmd_lower = cmd.lower()

            if cmd_lower == "!quit":
                print("[Session beendet]")
                break
            elif cmd_lower == "!history":
                handle_history(runner)
                continue
            elif cmd_lower == "!memory":
                await handle_memory(runner)
                continue
            elif cmd_lower == "!session":
                await handle_session(runner)
                continue
            elif cmd_lower == "!strategy":
                handle_strategy(runner, rest)
                continue
            elif cmd_lower == "!compact":
                handle_compact(runner)
                continue
            elif cmd_lower == "!config":
                handle_config(cfg)
                continue
            elif user_input.startswith("!"):
                print(
                    f"[Unbekanntes Kommando: {cmd}  —  "
                    f"!quit !history !memory !session !strategy !compact !config]"
                )
                continue

            # Chat
            print("Heinzel: ", end="", flush=True)
            try:
                async for chunk in runner.chat_stream(user_input):
                    print(chunk, end="", flush=True)
                print()
                line = await status_line(runner)
                if line:
                    print(f"\033[2m{line}\033[0m")
            except Exception as exc:
                print(f"\n[Fehler: {exc}]")

    finally:
        await runner.disconnect()


# =============================================================================
# Setup
# =============================================================================


def build_session_manager(cfg: dict[str, Any]) -> NoopSessionManager:
    """SessionManager aus Config bauen inkl. Compaction-Strategie."""
    mem = cfg.get("memory", {})
    max_tokens = int(mem.get("max_tokens", 128_000))
    max_turns = int(mem.get("max_turns", 10_000))
    strategy_name = str(mem.get("compaction_strategy", "summarizing")).lower()

    # Compaction-Strategie registrieren und als Default setzen
    if strategy_name == "truncation":
        CompactionRegistry.set_default("truncation")
    else:
        CompactionRegistry.set_default("summarizing")

    return NoopSessionManager(
        max_tokens=max_tokens,
        max_turns=max_turns,
    )


def build_runner(cfg: dict[str, Any]) -> Runner:
    """Runner aus Config zusammenbauen."""
    provider_cfg = cfg.get("provider", {})
    agent_cfg = cfg.get("agent", {})
    reasoning_cfg = cfg.get("reasoning", {})

    provider = HttpLLMProvider(
        name="cli-provider",
        base_url=provider_cfg.get("url", "http://localhost:12101"),
        model=provider_cfg.get("model", ""),
    )

    runner = Runner(
        provider=provider,
        name=agent_cfg.get("name", "Heinzel"),
        agent_id=agent_cfg.get("id", None),
        config=cfg,
    )

    # SessionManager mit Compaction
    runner.set_session_manager(build_session_manager(cfg))

    # Reasoning-Strategie
    strategy_name = reasoning_cfg.get("strategy", "passthrough")
    try:
        runner.set_strategy(strategy_name)
    except KeyError:
        print(
            f"[Warnung: Strategie '{strategy_name}' nicht registriert "
            f"— nutze passthrough]"
        )

    return runner


# =============================================================================
# Config laden
# =============================================================================


def load_config(config_path: str | None, provider_override: str | None) -> dict[str, Any]:
    """Config laden mit Defaults."""
    defaults: dict[str, Any] = {
        "agent": {"name": "Heinzel", "id": None},
        "provider": {"url": "http://localhost:12101", "model": ""},
        "memory": {
            "max_tokens": 128_000,
            "max_turns": 10_000,
            "compact_threshold": 0.80,
            "roll_threshold": 0.95,
            "compaction_strategy": "summarizing",
        },
        "reasoning": {"strategy": "passthrough"},
        "logging": {"log_dir": "./logs", "log_addons": False},
    }

    path = Path(config_path) if config_path else find_config_file()

    if path and path.exists():
        try:
            loaded = yaml.safe_load(path.read_text()) or {}
            for section, values in loaded.items():
                if section in defaults and isinstance(values, dict):
                    defaults[section].update(values)
                else:
                    defaults[section] = values
        except Exception as exc:
            print(f"[Warnung: Config-Fehler: {exc} — nutze Defaults]")
    elif config_path:
        print(f"[Warnung: '{config_path}' nicht gefunden — nutze Defaults]")

    if provider_override:
        defaults["provider"]["url"] = provider_override

    return defaults


# =============================================================================
# Einstiegspunkt
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Heinzel CLI")
    parser.add_argument("--config", "-c", help="Pfad zur YAML-Config")
    parser.add_argument("--provider", "-p", help="Provider-URL (ueberschreibt Config)")
    args = parser.parse_args()

    cfg = load_config(args.config, args.provider)
    runner = build_runner(cfg)

    asyncio.run(run_repl(runner, cfg))


if __name__ == "__main__":
    main()
