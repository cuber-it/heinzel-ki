"""
H.E.I.N.Z.E.L. Provider — Chainlit PoC

Verbindet sich mit einem laufenden Provider-Container via HTTP.
Jede Chat-Session bekommt eine UUID als session_id — wird an den
Provider mitgegeben und taucht im Dialog-Log und den Metriken auf.

Provider-URL: Env-Var PROVIDER_URL (default: http://provider-openai:8000)

Start:
  chainlit run chainlit_app.py --port 8000
"""
import json
import os
import uuid
import urllib.request
import urllib.error

import chainlit as cl

PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://provider-openai:8000").rstrip("/")
PROVIDER_NAME = os.environ.get("PROVIDER_TYPE", "openai").lower()


def _build_content(text: str, elements: list) -> str | list:
    """Baut MessageContent aus Text + hochgeladenen Dateien."""
    if not elements:
        return text

    # Lazy import — läuft im Container wo file_processor verfügbar ist
    try:
        import sys, os as _os
        sys.path.insert(0, "/app")
        from file_processor import process_file
    except ImportError:
        # Fallback wenn file_processor nicht vorhanden
        return text

    blocks = [{"type": "text", "text": text}] if text else []
    for el in elements:
        mime = getattr(el, "mime", None) or "application/octet-stream"
        name = getattr(el, "name", "datei")
        path = getattr(el, "path", None)
        if not path:
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            block = process_file(data, name, mime, PROVIDER_NAME)
            blocks.append(block.model_dump())
        except Exception as e:
            blocks.append({"type": "text", "text": f"[Fehler bei {name}: {e}]"})

    if not blocks:
        return text
    # Nur ein Text-Block ohne Datei → str zurückgeben
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return blocks[0]["text"]
    # Alles andere (Datei dabei, mehrere Blöcke) → Liste
    return blocks


def _get(path: str, timeout: int = 5) -> dict:
    try:
        with urllib.request.urlopen(f"{PROVIDER_URL}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _post(path: str, timeout: int = 5) -> dict:
    try:
        req = urllib.request.Request(
            f"{PROVIDER_URL}{path}", data=b"",
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


@cl.on_chat_start
async def on_start():
    health = _get("/health")
    info = _get("/capabilities")
    provider = info.get("provider", health.get("provider", "unbekannt"))
    models = info.get("tiers", {}).get("core", [])
    default_model = info.get("default_model", "")
    session_id = str(uuid.uuid4())

    cl.user_session.set("messages", [])
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("model", default_model)
    cl.user_session.set("provider", provider)
    cl.user_session.set("available_models", models)

    status = health.get("status", "unbekannt")
    await cl.Message(
        content=(
            f"**H.E.I.N.Z.E.L. Provider bereit**\n\n"
            f"Provider: `{provider}` | Modell: `{default_model}` | Status: `{status}`\n"
            f"Session: `{session_id}`\n\n"
            f"`/help` für Befehle"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    text = message.content.strip()
    messages    = cl.user_session.get("messages", [])
    session_id  = cl.user_session.get("session_id", str(uuid.uuid4()))
    model       = cl.user_session.get("model", "")

    # ─── Befehle ───────────────────────────────────────────────
    if text.startswith("/"):
        cmd = text.strip()
        cmd_lower = cmd.lower()

        if cmd_lower == "/help":
            avail = cl.user_session.get("available_models", [])
            await cl.Message(content=(
                "**Befehle:**\n"
                "- `/clear` — Gesprächsverlauf leeren\n"
                "- `/health` — Provider-Status\n"
                "- `/info` — Provider-Capabilities\n"
                "- `/model <name>` — Modell wechseln\n"
                "- `/models` — Verfügbare Modelle anzeigen\n"
                "- `/session` — Aktuelle Session-ID anzeigen\n"
                "- `/log on|off` — Dialog-Logging umschalten\n"
                "- `/metrics` — Metriken-Zusammenfassung dieser Session\n"
                "- `/help` — Diese Hilfe"
            )).send()

        elif cmd_lower == "/clear":
            cl.user_session.set("messages", [])
            await cl.Message(content="Gesprächsverlauf geleert.").send()

        elif cmd_lower == "/health":
            h = _get("/health")
            await cl.Message(content=f"```json\n{json.dumps(h, indent=2)}\n```").send()

        elif cmd_lower == "/info":
            i = _get("/capabilities")
            await cl.Message(
                content=f"```json\n{json.dumps(i, indent=2, ensure_ascii=False)}\n```"
            ).send()

        elif cmd_lower == "/models":
            avail = cl.user_session.get("available_models", [])
            cur = cl.user_session.get("model", "")
            lines = "\n".join(f"- {'**' if m == cur else ''}{m}{'**' if m == cur else ''}"
                              for m in avail)
            await cl.Message(content=f"**Verfügbare Modelle** (aktuell: `{cur}`):\n{lines}").send()

        elif cmd_lower.startswith("/model "):
            new_model = cmd[7:].strip()
            cl.user_session.set("model", new_model)
            await cl.Message(content=f"Modell gewechselt auf: `{new_model}`").send()

        elif cmd_lower == "/session":
            await cl.Message(content=f"Session-ID: `{session_id}`").send()

        elif cmd_lower.startswith("/log "):
            action = "enable" if cmd_lower.endswith("on") else "disable"
            result = _post(f"/logging/{action}")
            status = "aktiviert" if result.get("dialog_logging") else "deaktiviert"
            if "error" in result:
                await cl.Message(content=f"Fehler: {result['error']}").send()
            else:
                await cl.Message(content=f"Dialog-Logging {status}.").send()

        elif cmd_lower == "/metrics":
            m = _get(f"/metrics/summary?session_id={session_id}")
            await cl.Message(
                content=f"```json\n{json.dumps(m, indent=2, default=str)}\n```"
            ).send()

        else:
            await cl.Message(content=f"Unbekannter Befehl: `{text}`").send()
        return

    # ─── Chat ──────────────────────────────────────────────────
    messages.append({"role": "user", "content": _build_content(text, message.elements)})

    payload = json.dumps({
        "messages": messages,
        "max_tokens": 2048,
        **({"model": model} if model else {}),
        "context": {
            "session_id": session_id,
            "heinzel_id": "chainlit-poc",
            "task_id": None,
        },
    }).encode()

    req = urllib.request.Request(
        f"{PROVIDER_URL}/chat/stream",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    response_text = ""
    msg = cl.Message(content="")
    await msg.send()

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("type") == "content_delta":
                    token = chunk.get("content", "")
                    response_text += token
                    await msg.stream_token(token)
                elif chunk.get("type") == "command_response":
                    cmd_name = chunk.get("command", "")
                    result = chunk.get("result", {})
                    rendered = f"**[!{cmd_name}]**\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"
                    response_text = rendered
                    await msg.stream_token(rendered)
                elif chunk.get("type") == "error":
                    await msg.stream_token(f"\n[Fehler: {chunk.get('error')}]")
    except urllib.error.URLError as e:
        await msg.stream_token(f"\n[Verbindungsfehler: {e.reason}]")
    except Exception as e:
        await msg.stream_token(f"\n[Fehler: {e}]")

    await msg.update()

    if response_text:
        messages.append({"role": "assistant", "content": response_text})
        cl.user_session.set("messages", messages)
