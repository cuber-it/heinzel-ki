# H.E.I.N.Z.E.L.

**H**ighly **E**volved **I**ntelligent **N**ode with **Z**ero-overhead **E**xecution **L**ayer

Ein modulares Multi-Agent-System auf Basis von LLMs. Heinzel sind autonome KI-Agenten mit genetisch inspirierten Persönlichkeitsprofilen — sie denken, lernen, kommunizieren und entwickeln sich weiter.

---

## Was ist H.E.I.N.Z.E.L.?

H.E.I.N.Z.E.L. ist eine selbst-gehostete Plattform für KI-Agenten die auf eigener Hardware laufen, mit eigenen Daten arbeiten und über Mattermost kommunizieren. Von einem einfachen LLM-Provider bis hin zu koordinierten Multi-Agenten-Teams — Schritt für Schritt aufgebaut.

---

## Schnellstart

```bash
git clone git@github.com:cuber-it/heinzel-ki.git
cd heinzel-ki

# Setup: .env anlegen, Secrets generieren, Verzeichnisse erstellen
bash scripts/setup.sh

# Infra-Stack starten
docker compose -f docker/docker-compose.yml --env-file .env up -d

# Mattermost-Datenbank anlegen (einmalig)
docker exec heinzel-postgres psql -U heinzel -c 'CREATE DATABASE mattermost;'
docker restart heinzel-mattermost
```

Danach Accounts manuell einrichten — siehe [docs/mvp-00.md](docs/mvp-00.md).

---

## Repository-Struktur

```
src/
├── llm-provider/   LLM Provider Gateway (OpenAI, Anthropic, Google)
└── frontend/       CLI + Chainlit Web-UI

docker/
├── docker-compose.yml   Zentrales Compose (Infra + optionale Profile)
├── caddy/
├── frontend/            Dockerfile + compose.yml
├── gitea/
├── jupyterhub/          Dockerfile + compose.yml
├── llm-provider/        Dockerfile + compose per Provider
├── mattermost/
├── portainer/
└── postgres/

config/              Provider-Configs, Port-Vergabe
docs/                Dokumentation pro MVP
scripts/             Setup-Scripts
test/                Test-Suite
```

---

## Services & Ports

| Service      | Port  | Beschreibung                        |
|--------------|-------|-------------------------------------|
| PostgreSQL   | 12001 | Zentrale Datenbank                  |
| Mattermost   | 12002 | Chat-UI für Heinzel-Kommunikation   |
| JupyterHub   | 12003 | Notebooks für Entwicklung & Analyse |
| Caddy        | 12004 | Reverse Proxy / File Server         |
| Portainer    | 12005 | Docker-Management UI (HTTPS)        |
| Gitea        | 12006 | Interner Git-Server                 |
| Provider OAI | 12101 | OpenAI Provider Gateway             |
| Provider ANT | 12102 | Anthropic Provider Gateway          |
| Provider GOG | 12103 | Google Provider Gateway             |
| Chainlit     | 12201 | Web-Frontend                        |

Vollständige Port-Liste: [`config/ports.yaml`](config/ports.yaml)

---

## MVP-Übersicht

| MVP | Inhalt | Doku |
|-----|--------|------|
| mvp-00 | Basis-Infrastruktur (Docker-Stack) | [docs/mvp-00.md](docs/mvp-00.md) |
| mvp-01 | LLM Provider Gateway + Frontends   | [docs/mvp-01.md](docs/mvp-01.md) |

---

## Voraussetzungen

- Docker + Docker Compose
- Linux-Host (empfohlen: 16 GB+ RAM)
- API-Key für mindestens einen LLM-Provider (OpenAI / Anthropic / Google)

---

## Lizenz

*(folgt)*
