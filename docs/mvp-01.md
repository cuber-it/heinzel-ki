# MVP-01: LLM Provider Gateway

Dieser MVP bringt den LLM Provider Gateway — eine einheitliche HTTP-API für OpenAI, Anthropic und Google — sowie CLI und Chainlit als Frontends.

---

## Architektur

```
frontend/ (CLI oder Chainlit)
    │ HTTP POST /chat oder /chat/stream
    ▼
Provider Gateway (src/)
    ├── OpenAI Provider   → Port 12101
    ├── Anthropic Provider
    └── Google Provider
```

---

## Schnellstart

### 1. API-Key setzen

```bash
cp docker/llm-provider/.env.example docker/llm-provider/.env
# .env öffnen und OPENAI_API_KEY eintragen
```

### 2. Provider starten

```bash
cd docker/llm-provider
docker compose up --build -d
# Provider läuft auf http://localhost:12101
```

### 3. Chainlit starten

```bash
cd docker/frontend
docker compose up --build -d
# Web-UI: http://<host-ip>:12201
```

---

## Provider-API

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/health` | GET | Status prüfen |
| `/status` | GET | Provider-Info, verfügbare Modelle |
| `/chat` | POST | Synchrone Antwort |
| `/chat/stream` | POST | Server-Sent Events (Streaming) |
| `/metrics/rate-limits` | GET | Retry-Metriken |

### Beispiel

```bash
curl -s http://localhost:12101/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hallo!"}]}'
```

---

## Kommandos im Chat

Mit `!`-Prefix direkt im Chat steuerbar:

| Kommando | Beschreibung |
|----------|--------------|
| `!help` | Alle Kommandos anzeigen |
| `!status` | Provider-Status |
| `!set model=gpt-4o-mini` | Modell wechseln |
| `!get model` | Aktuelles Modell abfragen |
| `!dlglog` | Dialog-Log anzeigen |

---

## Unterstützte Dateitypen

| Typ | Formate | Verhalten |
|-----|---------|-----------|
| Bilder | JPEG, PNG, GIF, WEBP | Alle Provider nativ |
| PDF | .pdf | Anthropic/Google nativ · OpenAI → Text-Extraktion |
| Office | DOCX, XLSX, PPTX | Text-Extraktion (alle Provider) |
| Text/Code | JSON, CSV, YAML, .py, .js, … | Direkt als Text |
| Video/Audio | mp4, mp3, … | Fehlermeldung, kein Crash |

---

## Provider-Matrix

| Feature | OpenAI | Anthropic | Google |
|---------|--------|-----------|--------|
| Text | ✅ | ✅ | ✅ |
| Streaming | ✅ | ✅ | ✅ |
| Bilder nativ | ✅ | ✅ | ✅ |
| PDF nativ | ❌ (Extraktion) | ✅ | ✅ |
| Office | ✅ Extraktion | ✅ Extraktion | ✅ Extraktion |
| Retry/Backoff | ✅ | ✅ | ✅ |

---

## Provider wechseln

In `docker/llm-provider/.env`:

```bash
PROVIDER_TYPE=anthropic   # oder: openai, google
```

Dann neu starten: `docker compose up --build -d`

---

## Lokale Entwicklung & Tests

```bash
# Abhängigkeiten installieren
pip install -r requirements.txt

# Tests ausführen
python3 -m pytest test/ -q
# → 120 passed
```

---

## Bekannte Eigenheiten

**OpenAI PDF:** Die Chat Completions API unterstützt kein natives PDF. Bildbasierte PDFs (Scans) liefern eine Fehlermeldung im Chat — für Scans Anthropic oder Google wählen.

**Responses API (OpenAI):** Bewusst nicht genutzt — OpenAI-spezifisch, würde das einheitliche Interface zerstören.
