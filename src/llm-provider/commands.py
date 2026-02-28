"""
H.E.I.N.Z.E.L. Provider — In-Stream Kommandoschnittstelle

Kommandos werden als normale Chat-Nachrichten mit dem Prefix '!' gesendet.
Der Provider erkennt und filtert sie BEVOR sie ans LLM weitergereicht werden.
Antwort kommt als StreamChunk(type="command_response").

Protokoll:
  Nachricht:  {"role": "user", "content": "!kommando [argument]"}
  Antwort:    StreamChunk(type="command_response", command="...", result={...})

Verfuegbare Kommandos:
  !status               — Provider-Status (health, model, logging, retry)
  !dlglog on|off        — Dialog-Logging ein-/ausschalten
  !set key=value        — Parameter setzen: model, temperature, max_tokens
  !get key              — Parameter abfragen
  !help                 — Kommandoliste

Beispiele:
  !set temperature=0.7
  !set model=gpt-4o-mini
  !set max_tokens=512
  !get temperature
  !dlglog off
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from base import BaseProvider

COMMAND_PREFIX = "!"

# Setzbare/abfragbare Parameter
SETTABLE_PARAMS = {"model", "temperature", "max_tokens"}


def is_command(content: str) -> bool:
    """Ist diese Nachricht ein Provider-Kommando?"""
    s = content.strip() if isinstance(content, str) else ""
    return s.startswith(COMMAND_PREFIX) and len(s) > 1 and s[1:2] != " "


def extract_command(content: str) -> tuple[str, list[str]]:
    """
    Gibt (kommando, args) zurueck. Kommando lowercase ohne Prefix.
    Beispiel: '!set temperature=0.7' -> ('set', ['temperature=0.7'])
    """
    parts = content.strip()[len(COMMAND_PREFIX):].split()
    if not parts:
        return "", []
    return parts[0].lower(), parts[1:]


def _parse_kv(arg: str) -> tuple[str, str] | None:
    """'key=value' -> ('key', 'value') oder None."""
    if "=" not in arg:
        return None
    k, _, v = arg.partition("=")
    return k.strip().lower(), v.strip()


def execute_command(cmd: str, args: list[str], provider: "BaseProvider",
                    session_params: dict) -> dict:
    """
    Fuehrt ein Kommando aus. Gibt result-dict zurueck.
    session_params: mutable dict mit model, temperature, max_tokens
    """

    # ─── !help ────────────────────────────────────────────────
    if cmd == "help":
        return {
            "commands": [
                "!status               — Provider-Status",
                "!dlglog on|off        — Dialog-Logging umschalten",
                "!set key=value        — Parameter setzen (model, temperature, max_tokens)",
                "!get key              — Parameter abfragen",
                "!help                 — Diese Liste",
            ],
            "examples": [
                "!set model=gpt-4o-mini",
                "!set temperature=0.7",
                "!set max_tokens=512",
                "!get temperature",
                "!dlglog off",
            ]
        }

    # ─── !status ──────────────────────────────────────────────
    elif cmd == "status":
        return {
            "provider":       provider.provider_name,
            "connected":      provider._connected,
            "model":          session_params.get("model") or provider.get_default_model(),
            "default_model":  provider.get_default_model(),
            "available_models": provider.get_models(),
            "dialog_logging": provider.logger.enabled,
            "temperature":    session_params.get("temperature"),
            "max_tokens":     session_params.get("max_tokens"),
            "retry_config":   provider.config.get("retry", {}),
            "rate_limit_hits": len(getattr(provider, "_rate_limit_hits", [])),
        }

    # ─── !dlglog on|off ───────────────────────────────────────
    elif cmd == "dlglog":
        if not args:
            return {"error": "Syntax: !dlglog on|off",
                    "current": provider.logger.enabled}
        val = args[0].lower()
        if val == "on":
            provider.logger.enabled = True
            return {"ok": True, "dialog_logging": True}
        elif val == "off":
            provider.logger.enabled = False
            return {"ok": True, "dialog_logging": False}
        else:
            return {"error": f"Unbekannter Wert '{val}'. Erwartet: on|off"}

    # ─── !set key=value ───────────────────────────────────────
    elif cmd == "set":
        if not args:
            return {"error": "Syntax: !set key=value",
                    "settable": list(SETTABLE_PARAMS)}
        kv = _parse_kv(args[0])
        if kv is None:
            return {"error": f"Syntax: !set key=value (kein '=' in '{args[0]}')"}
        key, value = kv

        if key == "temperature":
            try:
                v = float(value)
                if not (0.0 <= v <= 2.0):
                    return {"error": "temperature muss zwischen 0.0 und 2.0 liegen"}
                session_params["temperature"] = v
                return {"ok": True, "temperature": v}
            except ValueError:
                return {"error": f"Ungültiger Wert: {value}"}

        elif key == "max_tokens":
            try:
                v = int(value)
                if v < 1:
                    return {"error": "max_tokens muss >= 1 sein"}
                session_params["max_tokens"] = v
                return {"ok": True, "max_tokens": v}
            except ValueError:
                return {"error": f"Ungültiger Wert: {value}"}

        elif key == "model":
            available = provider.get_models()
            if value not in available:
                return {"error": f"Unbekanntes Modell '{value}'",
                        "available": available}
            session_params["model"] = value
            return {"ok": True, "model": value}

        else:
            return {"error": f"Unbekannter Parameter '{key}'",
                    "settable": list(SETTABLE_PARAMS)}

    # ─── !get key ─────────────────────────────────────────────
    elif cmd == "get":
        if not args:
            # Alle Parameter ausgeben
            return {
                "model":       session_params.get("model") or provider.get_default_model(),
                "temperature": session_params.get("temperature"),
                "max_tokens":  session_params.get("max_tokens"),
            }
        key = args[0].lower()
        if key == "model":
            return {"model": session_params.get("model") or provider.get_default_model()}
        elif key == "temperature":
            return {"temperature": session_params.get("temperature")}
        elif key == "max_tokens":
            return {"max_tokens": session_params.get("max_tokens")}
        elif key == "dialog_logging":
            return {"dialog_logging": provider.logger.enabled}
        else:
            return {"error": f"Unbekannter Parameter '{key}'",
                    "gettable": list(SETTABLE_PARAMS) + ["dialog_logging"]}

    else:
        return {"error": f"Unbekanntes Kommando '!{cmd}'",
                "hint": "Schreibe !help fuer eine Liste"}
