# LLM Provider — PoC

Proof-of-Concept gegen einen laufenden Provider-Container.
Kein extra Install nötig — nur Python 3.10+.

## Voraussetzung

Container läuft:
```bash
cd /path/to/heinzel-projekt
docker compose up provider-anthropic   # Port 12002
docker compose up provider-openai      # Port 12001
docker compose up provider-google      # Port 12003
```

## CLI starten

```bash
cd services/llm_provider/poc

# Anthropic (default)
python3 cli.py

# OpenAI
python3 cli.py --url http://localhost:12001

# Google
python3 cli.py --url http://localhost:12003

# Mit System-Prompt
python3 cli.py --system "Du bist ein hilfreicher Assistent. Antworte immer auf Deutsch."
```

## Befehle im Chat

| Befehl | Funktion |
|--------|----------|
| `/help` | Hilfe anzeigen |
| `/stream` | Streaming ein-/ausschalten |
| `/log on\|off` | Dialog-Logging zur Laufzeit steuern |
| `/system <text>` | System-Prompt setzen |
| `/clear` | Gesprächsverlauf leeren |
| `/info` | Provider-Capabilities anzeigen |
| `/health` | Health-Status |
| `/exit` | Beenden |
