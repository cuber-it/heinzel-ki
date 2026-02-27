#!/usr/bin/env python3
"""
H.E.I.N.Z.E.L. Provider — Interaktive CLI

Spricht gegen einen laufenden Provider-Container via HTTP.
Streaming-Ausgabe in Echtzeit.

Verwendung:
  python3 cli.py [--url http://localhost:12002] [--stream] [--system "..."]

Beispiel:
  python3 cli.py --url http://localhost:12002
  python3 cli.py --url http://localhost:12001 --stream
"""
import argparse
import json
import sys
import urllib.request
import urllib.error


def chat(base_url: str, messages: list, system: str | None, stream: bool) -> str:
    payload = json.dumps({
        "messages": messages,
        "max_tokens": 2048,
        **({"system": system} if system else {}),
    }).encode()

    endpoint = f"{base_url}/chat/stream" if stream else f"{base_url}/chat"
    req = urllib.request.Request(
        endpoint, data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if stream:
                full = ""
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
                        text = chunk.get("content", "")
                        print(text, end="", flush=True)
                        full += text
                    elif chunk.get("type") == "command_response":
                        result = chunk.get("result", {})
                        cmd = chunk.get("command", "")
                        print(f"\n[!{cmd}]")
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                        full = json.dumps(result)
                    elif chunk.get("type") == "error":
                        print(f"\n[Fehler] {chunk.get('error')}", file=sys.stderr)
                print()  # Newline nach Stream
                return full
            else:
                result = json.loads(resp.read())
                return result.get("content", "")
    except urllib.error.URLError as e:
        print(f"\n[Verbindungsfehler] {e.reason}", file=sys.stderr)
        print(f"Läuft der Container? URL: {base_url}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"\n[Fehler] {e}", file=sys.stderr)
        return ""


def get_provider_info(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base_url}/capabilities", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def get_health(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def toggle_logging(base_url: str, enable: bool):
    action = "enable" if enable else "disable"
    req = urllib.request.Request(
        f"{base_url}/logging/{action}", data=b"",
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            result = json.loads(r.read())
            status = "aktiviert" if result.get("dialog_logging") else "deaktiviert"
            print(f"[Dialog-Logging {status}]")
    except Exception as e:
        print(f"[Fehler beim Toggle] {e}")


def print_help():
    print("""
Befehle:
  /exit, /quit    — Beenden
  /clear          — Gesprächsverlauf leeren
  /stream         — Streaming ein-/ausschalten
  /log on|off     — Dialog-Logging ein-/ausschalten
  /system <text>  — System-Prompt setzen
  /info           — Provider-Infos anzeigen
  /health         — Health-Status
  /help           — Diese Hilfe
""")


def main():
    parser = argparse.ArgumentParser(description="H.E.I.N.Z.E.L. Provider CLI")
    parser.add_argument("--url", default="http://localhost:12002",
                        help="Provider-URL (default: http://localhost:12002)")
    parser.add_argument("--stream", action="store_true", default=True,
                        help="Streaming aktivieren (default: an)")
    parser.add_argument("--system", default=None,
                        help="System-Prompt")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    stream = args.stream
    system = args.system
    messages = []

    # Verbindungscheck
    health = get_health(base_url)
    if not health:
        print(f"⚠  Provider nicht erreichbar: {base_url}")
        print("   Starte den Container oder prüfe die URL.")
        sys.exit(1)

    info = get_provider_info(base_url)
    provider = info.get("provider", health.get("provider", "unbekannt"))
    features = info.get("features", {})

    print(f"\n╔══════════════════════════════════════════╗")
    print(f"║  H.E.I.N.Z.E.L. Provider CLI             ║")
    print(f"╚══════════════════════════════════════════╝")
    print(f"  Provider : {provider}")
    print(f"  URL      : {base_url}")
    print(f"  Streaming: {'an' if stream else 'aus'}")
    if system:
        print(f"  System   : {system[:60]}{'...' if len(system) > 60 else ''}")
    print(f"  /help für Befehle\n")

    while True:
        try:
            user_input = input("Du: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTschüss!")
            break

        if not user_input:
            continue

        # Interne Befehle
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/exit", "/quit"):
                print("Tschüss!")
                break
            elif cmd == "/clear":
                messages = []
                print("[Verlauf geleert]")
            elif cmd == "/stream":
                stream = not stream
                print(f"[Streaming {'an' if stream else 'aus'}]")
            elif cmd.startswith("/log "):
                toggle_logging(base_url, cmd.endswith("on"))
            elif cmd.startswith("/system "):
                system = user_input[8:].strip()
                print(f"[System-Prompt gesetzt: {system[:60]}]")
            elif cmd == "/info":
                print(json.dumps(info, indent=2, ensure_ascii=False))
            elif cmd == "/health":
                print(json.dumps(get_health(base_url), indent=2))
            elif cmd == "/help":
                print_help()
            else:
                print(f"[Unbekannter Befehl: {user_input}]")
            continue

        messages.append({"role": "user", "content": user_input})
        print("Assistant: ", end="", flush=True)

        response = chat(base_url, messages, system, stream)

        if response:
            messages.append({"role": "assistant", "content": response})
        else:
            # Letzten User-Turn zurückrollen bei Fehler
            messages.pop()


if __name__ == "__main__":
    main()
