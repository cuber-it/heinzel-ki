# MVP-00: Basis-Infrastruktur

Dieser MVP richtet den Docker-Stack ein, auf dem alle Heinzel-Services laufen.

---

## Enthaltene Services

| Service    | Port  | Beschreibung                        |
|------------|-------|-------------------------------------|
| PostgreSQL | 12001 | Zentrale Datenbank                  |
| Mattermost | 12002 | Chat-UI für Heinzel-Kommunikation   |
| JupyterHub | 12003 | Notebooks für Entwicklung & Analyse |
| Caddy      | 12004 | Reverse Proxy / File Server         |
| Portainer  | 12005 | Docker-Management UI                |
| Gitea      | 12006 | Interner Git-Server                 |

Vollständiges Port-Schema: [`config/ports.yaml`](../config/ports.yaml)

---

## Erstes Setup

```bash
# 1. Umgebungsvariablen setzen
cp .env.example .env
# .env öffnen: Passwörter + DOCKER_BASE anpassen
# POSTGRES_PASSWORD und JUPYTERHUB_CRYPT_KEY: openssl rand -hex 32

# 2. Verzeichnisse + Secrets anlegen
bash scripts/setup.sh

# 3. Infra-Stack starten
docker compose -f docker/docker-compose.yml up -d
```

---

## Optionale Services starten (Provider / Frontend)

```bash
# OpenAI Provider
docker compose -f docker/docker-compose.yml --profile provider-openai up -d

# Chainlit Frontend
docker compose -f docker/docker-compose.yml --profile frontend up -d

# Alles auf einmal
docker compose -f docker/docker-compose.yml \
  --profile provider-openai --profile frontend up -d
```

Provider können auch einzeln gestartet werden:

```bash
docker compose -f docker/llm-provider/compose.openai.yml up --build -d
docker compose -f docker/llm-provider/compose.anthropic.yml up --build -d
docker compose -f docker/llm-provider/compose.google.yml up --build -d
```

---

## Netzwerk

Alle Services laufen im Docker-Netzwerk `heinzel`. Es wird beim ersten Start automatisch angelegt. Manuell:

```bash
docker network create heinzel
```
