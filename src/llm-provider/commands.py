"""
H.E.I.N.Z.E.L. Provider — Kommandoschnittstelle

Kommandos werden als normale Chat-Nachrichten mit Prefix '!' gesendet
und BEVOR sie ans LLM gehen abgefangen.

Verfügbare Kommandos (provider-level, stateless):
  !help       — Kommandoliste
  !status     — Provider-Zustand
  !dlglog on|off — Dialog-Logging umschalten (process-level)

Alles was Session-State braucht (!set, !get, !history) gehört in den Core.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from base import BaseProvider

COMMAND_PREFIX = "!"


def is_command(content: str) -> bool:
    s = content.strip() if isinstance(content, str) else ""
    return s.startswith(COMMAND_PREFIX) and len(s) > 1 and s[1:2] != " "


def extract_command(content: str) -> tuple[str, list[str]]:
    parts = content.strip()[len(COMMAND_PREFIX):].split()
    if not parts:
        return "", []
    return parts[0].lower(), parts[1:]


def execute_command(cmd: str, args: list[str], provider: "BaseProvider") -> dict:
    """Führt ein Provider-Kommando aus. Kein Session-State."""

    # ─── !help ────────────────────────────────────────────────
    if cmd == "help":
        return {
            "commands": [
                "!help              — Diese Liste",
                "!status            — Provider-Zustand",
                "!dlglog on|off     — Dialog-Logging umschalten",
            ],
            "note": "Session-Parameter (model, temperature, max_tokens) im Request-Body setzen.",
        }

    # ─── !status ──────────────────────────────────────────────
    elif cmd == "status":
        return {
            "provider":         provider.provider_name,
            "connected":        provider._connected,
            "default_model":    provider.get_default_model(),
            "available_models": provider.get_models(),
            "dialog_logging":   provider.logger.enabled,
            "retry_config":     provider.config.get("retry", {}),
            "rate_limit_hits":  len(getattr(provider, "_rate_limit_hits", [])),
        }

    # ─── !dlglog on|off ───────────────────────────────────────
    elif cmd == "dlglog":
        if not args:
            return {"error": "Syntax: !dlglog on|off", "current": provider.logger.enabled}
        val = args[0].lower()
        if val == "on":
            provider.logger.enabled = True
            return {"ok": True, "dialog_logging": True}
        elif val == "off":
            provider.logger.enabled = False
            return {"ok": True, "dialog_logging": False}
        else:
            return {"error": f"Unbekannter Wert '{val}'. Erwartet: on|off"}

    else:
        return {
            "error": f"Unbekanntes Kommando '!{cmd}'",
            "hint":  "!help für eine Liste. Session-Kommandos (!set, !get, !history) gehören in den Core.",
        }
