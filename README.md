# H.E.I.N.Z.E.L.

**H**ighly **E**volved **I**ntelligent **N**ode with **Z**ero-overhead **E**xecution **L**ayer

Ein modulares Multi-Agent-System auf Basis von Claude. Heinzel sind autonome KI-Agenten mit genetisch inspirierten Persönlichkeitsprofilen — sie denken, lernen, kommunizieren und entwickeln sich weiter.

---

## Infrastruktur

Die Basis-Infrastruktur besteht aus fünf Diensten auf einem gemeinsamen Docker-Netzwerk (`heinzel`):

| Service      | Port  | Beschreibung                        |
|-------------|-------|-------------------------------------|
| PostgreSQL   | 5432  | Primäre Datenbank                   |
| Mattermost   | 8001  | Kommunikation & Heinzel-Messaging   |
| JupyterHub   | 8888  | Notebooks & Experimente             |
| Caddy        | 8000  | Artifact Server / Static Files      |
| Portainer    | 9000  | Docker-Management UI                |

Jeder Dienst hat sein eigenes `compose.yml` unter `docker/<service>/`. Das Haupt-`docker-compose.yml` im Root bindet sie per `include:` zusammen.

---

## Voraussetzungen

- Docker + Docker Compose v2.20+
- `openssl` (für Secret-Generierung)

---

## Quickstart

```bash
git clone <repo>
cd heinzel-projekt
bash scripts/setup.sh
docker compose up -d
```

Das Setup-Script erledigt automatisch:
- `.env` aus `.env.example` anlegen
- Sichere Passwörter und Keys generieren
- Docker-Netzwerk `heinzel` anlegen
- Persistente Verzeichnisse unter `~/docker` anlegen
- Mattermost-Datenbank in PostgreSQL anlegen

Optionaler Zielpfad: `bash scripts/setup.sh /pfad/nach/wahl`

---

## Projektstruktur

```
heinzel-projekt/
├── docker-compose.yml        # Haupt-Compose (nur includes + network)
├── docker/
│   ├── caddy/                # Caddyfile + compose.yml
│   ├── jupyterhub/           # Dockerfile + Config + compose.yml
│   ├── mattermost/           # compose.yml
│   ├── portainer/            # compose.yml
│   └── postgres/             # compose.yml
├── scripts/
│   └── setup.sh              # Setup-Script (idempotent)
├── src/                      # Heinzel-Quellcode
├── test/                     # Tests
├── .env.example              # Vorlage für Umgebungsvariablen
├── CHANGES.md                # Changelog
└── WORKFLOW.md               # Git-Workflow & Branch-Strategie
```

Persistente Daten liegen außerhalb des Repos unter `~/docker/<service>/`:
- `config/` — Konfigurationsdateien
- `data/`   — Anwendungsdaten
- `logs/`   — Logdateien
- `code/`   — Service-spezifischer Code / Plugins

---

## Konfiguration

Kopie von `.env.example` → `.env` (wird vom Setup-Script automatisch erstellt):

```env
DOCKER_BASE=/home/user/docker
POSTGRES_USER=heinzel
POSTGRES_PASSWORD=<generiert>
POSTGRES_DB=heinzel
MM_SITEURL=http://localhost:8001
JUPYTERHUB_CRYPT_KEY=<generiert>
```

`.env` wird nie eingecheckt.

---

## Entwicklung

Siehe [WORKFLOW.md](WORKFLOW.md) für Branch-Strategie und MVP-Workflow.
