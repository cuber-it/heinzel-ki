# MVP-01: LLM Provider Gateway + Frontends

Dieser MVP baut auf MVP-00 auf und ergänzt den LLM Provider Gateway sowie das Chainlit Web-Frontend.

---

## Enthaltene Komponenten

| Komponente       | Port  | Beschreibung                            |
|------------------|-------|-----------------------------------------|
| Provider OpenAI  | 12101 | LLM Gateway für OpenAI-Modelle          |
| Provider Anthropic | 12102 | LLM Gateway für Anthropic-Modelle     |
| Provider Google  | 12103 | LLM Gateway für Google-Modelle          |
| Chainlit         | 12201 | Web-Frontend für direkte Konversation   |

---

## Voraussetzung

MVP-00 muss laufen:

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

---

## API-Key eintragen

```bash
cp docker/llm-provider/.env.example docker/llm-provider/.env
# .env öffnen und Key eintragen:
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GOOGLE_API_KEY=...
```

---

## Provider starten

### Einzeln über das zentrale Compose

```bash
# OpenAI
docker compose -f docker/docker-compose.yml --env-file .env --profile provider-openai up --build -d

# Anthropic
docker compose -f docker/docker-compose.yml --env-file .env --profile provider-anthropic up --build -d

# Google
docker compose -f docker/docker-compose.yml --env-file .env --profile provider-google up --build -d
```

### Oder direkt über die Provider-Compose-Dateien

```bash
docker compose -f docker/llm-provider/compose.openai.yml up --build -d
docker compose -f docker/llm-provider/compose.anthropic.yml up --build -d
docker compose -f docker/llm-provider/compose.google.yml up --build -d
```

---

## Chainlit Frontend starten

```bash
docker compose -f docker/docker-compose.yml --env-file .env --profile frontend up --build -d
```

Aufruf: `http://<host>:12201`

Das Frontend verbindet sich standardmäßig mit dem OpenAI Provider (`http://heinzel-provider-openai:8000`).

---

## Provider API

### Health-Check

```bash
curl http://localhost:12101/health
# {"status":"ok","provider":"openai",...}
```

### Verfügbare Modelle

```bash
curl http://localhost:12101/models
```

### Chat-Anfrage

```bash
curl -X POST http://localhost:12101/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hallo!"}],
    "model": "gpt-4o-mini"
  }'
```

---

## Alles auf einmal

```bash
docker compose -f docker/docker-compose.yml --env-file .env \
  --profile provider-openai --profile frontend up --build -d
```

---

## Stack verwalten

```bash
# Status
docker compose -f docker/docker-compose.yml --profile provider-openai --profile frontend ps

# Logs Provider
docker logs heinzel-provider-openai -f

# Logs Chainlit
docker logs heinzel-chainlit -f

# Provider stoppen
docker compose -f docker/docker-compose.yml --profile provider-openai down
```

---

## Fehlerbehebung

**Provider startet nicht:**
→ API-Key in `docker/llm-provider/.env` prüfen.

**Chainlit zeigt "Provider nicht erreichbar":**
→ Provider muss vor Chainlit laufen. Reihenfolge: erst Provider, dann Frontend.

**Build schlägt fehl:**
→ `docker compose ... up --build` erzwingt Neu-Build. Bei Abhängigkeitsproblemen:
   `docker system prune -f` und neu bauen.
