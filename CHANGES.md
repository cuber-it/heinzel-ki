# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.
Format: `[MVP-XX] — Datum — Kurzbeschreibung`, Details darunter.

---

## [MVP-01] — 2026-02-28 — LLM Provider + Frontends

Branch: `main` | Tests: 110 passing

### Hinzugefügt

- `src/llm-provider/` — Provider Gateway (OpenAI, Anthropic, Google)
  - `base.py` — Abstrakte Basisklasse, unified interface (Tier 1/2/3)
  - `models.py` — Pydantic-Modelle (ChatRequest, ChatResponse, ContentBlocks)
  - `openai_provider.py` / `anthropic_provider.py` / `google_provider.py`
  - `file_processor.py` — Datei-Konverter (Bilder, PDF, Office, Text)
  - `config.py` — InstanceConfig: ENV > instance.yaml > Default
  - `database.py` — SQLite Metriken-Logging (costs.db)
  - `logger.py` — JSONL Session-Logging
  - `log_reader.py` — Log-Abfrage mit Filtern (session_id, heinzel_id, task_id)
  - `commands.py` — Provider-Kommandos: `!help`, `!status`, `!dlglog`
  - `retention.py` — Log-Rotation und Cleanup
  - `retry.py` — Exponential Backoff (max 3 Retries, 429/5xx)
  - `provider_template/` — Template + ONBOARDING.md für neue Provider
- `src/frontend/`
  - `cli.py` — Interaktives Terminal-Frontend mit Streaming
  - `chainlit_app.py` — Web-Chat-Interface
- `docker/docker-compose.yml` — Zentrales Compose mit Profiles
- `docker/llm-provider/` — Dockerfile + compose.{openai,anthropic,google}.yml
- `docker/frontend/` — Dockerfile + compose.yml
- `docs/mvp-01.md` — Setup-Doku: Provider/Frontend, API-Beispiele, Troubleshooting
- `test/` — 110 Tests: provider_base, commands, metrics, multimodal, file_processor, retention, retry, logging

### Ports

| Service | Port |
|---------|------|
| Provider OpenAI | 12101 |
| Provider Anthropic | 12102 |
| Provider Google | 12103 |
| Chainlit | 12201 |

### Designentscheidungen

- Provider ist **stateless** — kein Session-State, kein globales session_params
- Kommandos nur provider-level: `!help`, `!status`, `!dlglog` — Session-Kommandos gehören in HNZ-002 Core
- Dialog-Logging: Default ON, zur Laufzeit togglebar via `!dlglog` oder `POST /logging/enable|disable`
- Metriken-Logging: immer aktiv (SQLite), kein Toggle
- Multimodal: Bilder nativ bei allen Providern; PDF nativ bei Anthropic+Google, via pypdf bei OpenAI

---

## [MVP-00] — 2026-02-19/28 — Basis-Infrastruktur

Branch: `main` | Stack: theBrain

### Hinzugefügt

- `docker/docker-compose.yml` — Zentrales Compose mit Profiles
- Services: PostgreSQL (12001), Mattermost (12002), JupyterHub (12003), Caddy (12004), Portainer (12005), Gitea (12006)
- `scripts/setup.sh` — Idempotentes Setup: .env generieren, Secrets, Verzeichnisse, Mattermost-Permissions
- `.env.example` — Alle konfigurierbaren Variablen mit Platzhaltern
- `config/ports.yaml` — Zentrales Port-Schema (12xxx)
- `docs/mvp-00.md` — Vollständige Ersteinrichtung inkl. manueller Account-Schritte

### Healthchecks

Alle Container nutzen native Binaries statt curl/wget:
- PostgreSQL: `pg_isready`
- Mattermost: `/mattermost/bin/mattermost version`
- Caddy: `caddy version`
- Portainer: `/portainer --version`
- JupyterHub: `curl localhost:8000`
- Gitea: `gitea --version`

### Designentscheidungen

- Bind-Mounts statt Named Volumes
- Netzwerk `heinzel` extern, Ownership im Root-Compose
- Mattermost-Datenbank muss einmalig manuell angelegt werden (`CREATE DATABASE mattermost`)
- setup.sh ohne hängenden Gitea-Auto-Setup — manuelle Ersteinrichtung per Browser

---

## [Housekeeping] — 2026-02-28 — Git-History bereinigt

- Hostnamen und Benutzernamen aus kompletter Git-History entfernt (`git filter-repo`)
- Force-push auf alle Branches (main, development, mvp-00)
- Beide Maschinen neu geklont
