# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.  
Format: `[MVP-XX] — Datum — Kurzbeschreibung`, Details darunter.

---

## [MVP-02] — 2026-02-27 — LLM Provider Integration

Branch: `mvp-02` | Commits: `5262ae3`–`8bbdf46`

**Hinzugefügt**
- `services/llm_provider/`: LLM Provider Gateway vollständig integriert
- `AnthropicProvider`: Tier 1 (chat, stream, models, token_count) + Tier 2 (batches)
- `OpenAIProvider`: Tier 1+2+3 (alle Endpoints inkl. Audio, Images, Moderation)
- `GoogleProvider` (neu): Tier 1 (chat, stream, models, token_count) + Tier 2 (embeddings)
- `src/config.py`: InstanceConfig — Ladereihenfolge ENV > instance.yaml > Default
- `config/instance.yaml.example`: Vorlage für api_key, log_requests, database.url
- `config/instance.yaml`: gitignored, enthält echte Secrets (nie committed)
- `tests/test_provider_base.py`: 19 Basis-Tests, alle offline (kein API-Call)
- `tests/conftest.py`: LOG_DIR auf tmp_path, DB-Env gecleart
- `poc/cli.py`: Interaktive CLI mit Streaming, /log on|off, /stream, Multi-Turn
- `poc/chainlit_app.py`: Browser-Chat-UI mit Streaming
- `poc/Dockerfile`: Chainlit-Container
- `docker/llm-provider-poc/compose.yml`: Standalone PoC, kein Postgres, SQLite

**Geändert**
- `config/anthropic.yaml`: Modelle auf claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5 aktualisiert
- `config/openai.yaml`: Fiktive gpt-5-Modelle durch reale ersetzt (gpt-4o, o1, o3, o4-mini)
- `config/google.yaml` (neu): Gemini-Modelle (2.5-pro, 2.0-flash, 2.0-flash-lite, 1.5-pro/flash)
- `docker/llm-provider/compose.yml`: Pfade korrigiert, Google-Container (12003), LOG_REQUESTS ergänzt
- `docker/llm-provider-poc/compose.yml`: Ports 12011 (Provider), 12017 (Chainlit)
- `src/database.py`: SQLite-Fallback wenn DATABASE_URL nicht gesetzt, aiosqlite
- `src/logger.py`: enabled-Flag, Dialog-Logging abschaltbar
- `src/base.py`: Docstrings an ABC-Methoden, instance_config für LOG_REQUESTS
- `src/main.py`: Google-Provider eingebunden, Config-Validierung, Runtime-Toggle /logging/enable|disable|status
- `.gitignore`: **/instance.yaml ergänzt
- `requirements.txt`: aiosqlite>=0.20.0 ergänzt

**Designentscheidungen**
- instance.yaml als Secret-Layer: ENV > YAML > Default, nie committed
- Dialog-Logging: Default ON, zur Laufzeit togglebar via API, kein Container-Restart nötig
- Cost-Logging immer aktiv (kein Toggle), separates Thema
- Komprimierung der Logs: separater Service unter HNZ-099, nicht im Provider
- SQLite als Default-DB: kein extra Service nötig für PoC und Einzelbetrieb
- PoC-Ports: 12011 (Provider), 12017 (Chainlit) — 12001 war bereits belegt

**Getestet**
- Provider antwortet: `PoC funktioniert!` via gpt-4o-2024-08-06, 17/4 Tokens
- Chainlit UI: HTTP 200 auf Port 12017
- 19 Unit-Tests: alle grün

**Offene Punkte (nächste Stories)**
- HNZ-099: Log-Komprimierung als separater Service (zstd)
- Chainlit: Authentifizierung fehlt noch für Produktivbetrieb
- Provider-Container der bestehenden Compose noch auf DATABASE_URL=PostgreSQL hardcodiert — sollte auf instance.yaml umgestellt werden

---

## [MVP-00] — 2026-02-19/20 — Infrastruktur

Initiales Setup der Basis-Infrastruktur.

**Hinzugefügt**
- Modulare Docker-Compose-Struktur: eigenes `compose.yml` pro Service
- Services: PostgreSQL 16, Mattermost Team Edition, JupyterHub, Caddy, Portainer
- Haupt-`docker-compose.yml` mit `include:` und Bridge-Netzwerk `heinzel`
- `scripts/setup.sh`: idempotentes Setup-Script mit Auto-Generierung von `.env` und Secrets
- `.env.example` mit allen konfigurierbaren Variablen
- `WORKFLOW.md`: Git-Branch-Strategie und MVP-Prozess
- Healthchecks: `pg_isready` (Postgres), `mmctl` (Mattermost), `curl` (JupyterHub), `nc` (Caddy)
- JupyterHub Custom-Image mit notebook, ipykernel, numpy, pandas, matplotlib

**Technische Entscheidungen**
- Bind-Mounts statt Named Volumes — Daten bleiben kontrollierbar auf dem Host
- Netzwerk `external: true` in Service-Composes — Netzwerk-Ownership liegt im Root-Compose
- chown via Alpine-Container — kein sudo erforderlich (user in docker group)
- Portainer ohne Healthcheck — distroless Image, kein Shell/Tools verfügbar

**Git-Commits**
- `1503ffa` init: Projektstruktur, .gitignore, requirements.txt
- `cd00d2f` feat: mvp-00 docker-compose, Dockerfiles, configs, setup.sh
- `1849993` fix: setup.sh default Zielpfad ~/docker
- `af184df` refactor: modulare compose.yml pro Service + include im Haupt-Compose
- `fe76a1f` fix: version: Direktive aus allen compose.yml entfernt (deprecated)
- `a450669` feat: setup.sh auto-generiert .env+Secrets, sudo chown Fallback
- `36bb549` fix: chown via Alpine-Container statt sudo
- `88b099a` fix: Portainer Healthcheck entfernt (kein wget/curl im Image)
- `1277bd7` fix: Mattermost Healthcheck entfernt (kein curl im Image)
- `9bd4c21` fix: Mattermost Image-Healthcheck deaktiviert
- `00c757c` fix: Healthchecks professionell - mmctl/curl/nc je nach Image
- `2fcc2d4` fix: Mattermost logs + client-plugins persistent gemountet
- `0bc4bdd` fix: JupyterHub - jovyan User im Dockerfile, Config bereinigt
- `35c51d8` feat: Gitea als sechsten Service hinzugefügt (Heinzel-eigener Git-Server)
- `f59ee1f` feat: Gitea Auto-Setup im setup.sh, README aktualisiert
- `bd0dc4d` sec: Alle Passwörter aus .env — keine Hardcodes im Script
