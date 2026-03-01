#!/usr/bin/env python3
"""heinzel_cli.py — Erster lebender Heinzel. Testheinzel auf nacktem Core.

Testwerkzeug und Referenzimplementierung — kein Produkt.
Beweist dass der Core als Bibliothek funktioniert.

Hardcodes (absichtlich, temporaer bis HNZ-002-0007/0008):
  - Provider-URL: http://localhost:12002
  - Log-Dir:      ./logs
  - Heinzel-Name: heinzel-1
  - Modell:       aus Provider-Default

Kommandos (alle mit !):
  !quit     — Session beenden
  !history  — Dialoglog der aktuellen Session anzeigen

Verwendung:
  python heinzel_cli.py
  python heinzel_cli.py --config heinzel.yaml

Config-YAML (optional):
  heinzel:
    name: mein-heinzel
    id: optional-feste-id
  provider:
    url: http://localhost:12002
    model: ""
  logging:
    log_dir: ./logs
    log_addons: false
    log_mcp: false
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
import yaml

# Core — einzige externe Abhaengigkeit
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import BaseHeinzel, LLMProvider


# =============================================================================
# HttpLLMProvider — spricht mit dem laufenden Provider-Service
# TODO: wird durch HNZ-002-0008 (Provider-Transport) ersetzt
# =============================================================================

class HttpLLMProvider(LLMProvider):
    """HTTP-Client gegen den Heinzel Provider-Service.

    Unterstuetzt /chat (blockierend) und /chat/stream (SSE).
    Temporaere Implementierung bis HNZ-002-0008.
    """

    def __init__(self, base_url: str, model: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> str:
        payload: dict[str, Any] = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt
        if model or self._model:
            payload["model"] = model or self._model

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self._base_url}/chat", json=payload)
            resp.raise_for_status()
            return resp.json().get("content", "")

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> AsyncGenerator[str, None]:
        payload: dict[str, Any] = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt
        if model or self._model:
            payload["model"] = model or self._model

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{self._base_url}/chat/stream", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        if chunk.get("type") == "content_delta" and chunk.get("content"):
                            yield chunk["content"]
                    except json.JSONDecodeError:
                        continue


# =============================================================================
# Kommando-Handler
# =============================================================================

def handle_history(heinzel: BaseHeinzel) -> None:
    """!history — Dialoglog der aktuellen Session ausgeben."""
    log_path = heinzel._dialog_log.log_path
    if log_path is None or not log_path.exists():
        print("[kein Log vorhanden]")
        return
    print(f"\n--- History: {log_path} ---")
    print(log_path.read_text(encoding="utf-8"))
    print("--- Ende ---\n")


# =============================================================================
# REPL
# =============================================================================

async def run_repl(heinzel: BaseHeinzel, provider_url: str) -> None:
    """Hauptschleife. Laeuft bis !quit."""
    log_path = heinzel._dialog_log.log_path
    print(f"\n{'='*60}")
    print(f"  Heinzel: {heinzel.name}  ({heinzel.heinzel_id[:8]}...)")
    print(f"  Provider: {provider_url}")
    print(f"  Log: {log_path or '(kein Log)'}")
    print(f"  Kommandos: !quit  !history")
    print(f"{'='*60}\n")

    await heinzel.connect()

    try:
        while True:
            try:
                user_input = input("Du: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[Ctrl+C — beende Session]")
                break

            if not user_input:
                continue

            # Kommandos
            if user_input.lower() == "!quit":
                print("[Session beendet]")
                break

            if user_input.lower() == "!history":
                handle_history(heinzel)
                continue

            if user_input.startswith("!"):
                print(f"[Unbekanntes Kommando: {user_input}  —  verfuegbar: !quit !history]")
                continue

            # Chat via Streaming
            print("Heinzel: ", end="", flush=True)
            try:
                async for chunk in heinzel.chat_stream(user_input):
                    print(chunk, end="", flush=True)
                print()  # Zeilenumbruch nach Antwort
            except Exception as exc:
                print(f"\n[Fehler: {exc}]")

    finally:
        await heinzel.disconnect()


# =============================================================================
# Einstiegspunkt
# =============================================================================

def load_config(config_path: str | None) -> dict[str, Any]:
    """Config laden. Hardcode-Defaults wenn keine Datei."""
    defaults: dict[str, Any] = {
        "heinzel": {
            "name": "heinzel-1",
        },
        "provider": {
            "url": "http://localhost:12002",
            "model": "",
        },
        "logging": {
            "log_dir": "./logs",
            "log_addons": False,
            "log_mcp": False,
        },
    }
    if config_path is None:
        return defaults

    try:
        with open(config_path) as f:
            loaded = yaml.safe_load(f) or {}
        # Tief-mergen: geladene Werte ueberschreiben Defaults
        for section, values in loaded.items():
            if section in defaults and isinstance(values, dict):
                defaults[section].update(values)
            else:
                defaults[section] = values
        return defaults
    except FileNotFoundError:
        print(f"[Warnung: Config-Datei '{config_path}' nicht gefunden — nutze Defaults]")
        return defaults
    except Exception as exc:
        print(f"[Warnung: Config-Fehler: {exc} — nutze Defaults]")
        return defaults


def main() -> None:
    parser = argparse.ArgumentParser(description="Heinzel CLI — Testheinzel")
    parser.add_argument("--config", "-c", help="Pfad zur YAML-Config", default=None)
    parser.add_argument("--provider", "-p", help="Provider-URL (ueberschreibt Config)", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)

    # --provider ueberschreibt Config
    if args.provider:
        cfg["provider"]["url"] = args.provider

    provider_url: str = cfg["provider"]["url"]
    model: str = cfg["provider"].get("model", "")
    heinzel_name: str = cfg["heinzel"]["name"]
    heinzel_id: str | None = cfg["heinzel"].get("id", None)

    provider = HttpLLMProvider(base_url=provider_url, model=model)
    heinzel = BaseHeinzel(
        provider=provider,
        name=heinzel_name,
        heinzel_id=heinzel_id,
        config=cfg,
    )

    asyncio.run(run_repl(heinzel, provider_url))


if __name__ == "__main__":
    main()
