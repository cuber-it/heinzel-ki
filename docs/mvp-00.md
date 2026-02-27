# MVP-00: Basis-Infrastruktur

Dieser MVP richtet den Docker-Stack ein, auf dem alle Heinzel-Services laufen.

---

## Enthaltene Services

| Service | Port | Beschreibung |
|---------|------|--------------|
| PostgreSQL | 5432 | Zentrale Datenbank |
| Mattermost | 8001 | Chat-UI für Heinzel-Kommunikation |
| JupyterHub | 8888 | Notebooks für Entwicklung & Analyse |
| Caddy | 8000 | Reverse Proxy / File Server |
| Portainer | 9000 | Docker-Management UI |
| Gitea | 3000 | Interner Git-Server |

---

## Erstes Setup

```bash
# 1. Verzeichnisse anlegen
bash scripts/setup.sh

# 2. Umgebungsvariablen setzen
cp .env.example .env
# .env öffnen und Passwörter eintragen

# 3. Stack starten
docker compose up -d
```

---

## Einzelne Services starten

Jeder Service hat seine eigene Compose-Datei unter `docker/<service>/compose.yml`:

```bash
cd docker/postgres   && docker compose up -d
cd docker/mattermost && docker compose up -d
cd docker/jupyterhub && docker compose up -d
cd docker/caddy      && docker compose up -d
cd docker/portainer  && docker compose up -d
cd docker/gitea      && docker compose up -d
```

---

## Mattermost-Datenbank anlegen

Nach dem ersten Start von Postgres:

```bash
bash docker/postgres/code/init-mattermost.sh
```

---

## Netzwerk

Alle Services laufen im Docker-Netzwerk `heinzel`. Es wird beim ersten Start automatisch angelegt.

```bash
docker network create heinzel
```
