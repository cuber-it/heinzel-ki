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
| Portainer  | 12005 | Docker-Management UI (HTTPS)        |
| Gitea      | 12006 | Interner Git-Server                 |

Vollständiges Port-Schema: [`config/ports.yaml`](../config/ports.yaml)

---

## Ersteinrichtung

### 1. Setup ausführen

```bash
bash scripts/setup.sh
```

Das Script legt `.env` aus `.env.example` an, generiert alle Secrets automatisch
und erstellt die persistenten Verzeichnisse unter `~/docker/`.

> **Wichtig:** Die generierten Secrets nur in `.env` — niemals in Git einchecken.

### 2. Stack starten

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

### 3. Mattermost-Datenbank anlegen

Muss einmalig nach dem ersten Postgres-Start gemacht werden:

```bash
docker exec heinzel-postgres psql -U heinzel -c 'CREATE DATABASE mattermost;'
docker restart heinzel-mattermost
```

### 4. Accounts einrichten (manuell)

#### Mattermost

Aufruf: `http://<host>:12002`

- Beim ersten Start auf **"Create account"** klicken
- Den ersten angelegten Account als Team-Admin verwenden
- Ein Team anlegen (z.B. `heinzel`)
- Bot-Accounts für die Heinzel-Agenten später über die Admin-Konsole anlegen

#### Gitea

Aufruf: `http://<host>:12006`

- Beim ersten Start erscheint die Installations-Seite
- Datenbank-Einstellungen:
  - Type: **PostgreSQL**
  - Host: `postgres:5432`
  - Database: `gitea`
  - User/Password: aus `.env` (POSTGRES_USER / POSTGRES_PASSWORD)
- Admin-Account anlegen (Credentials aus `.env`: GITEA_ADMIN_USER / GITEA_ADMIN_PASSWORD)
- Nach der Installation: Organisation `heinzel` anlegen

> Gitea-Datenbank muss vorher angelegt sein:
> ```bash
> docker exec heinzel-postgres psql -U heinzel -c 'CREATE DATABASE gitea;'
> ```

#### Portainer

Aufruf: `https://<host>:12005` (selbstsigniertes Zertifikat — Browser-Warnung bestätigen)

- Beim ersten Start Admin-Passwort setzen
- **"Get Started"** → lokale Docker-Umgebung auswählen

#### JupyterHub

Aufruf: `http://<host>:12003`

- Standard-Authenticator: beliebiger Benutzername / Passwort (DummyAuthenticator)
- Für Produktion: Authenticator in `~/docker/jupyterhub/config/jupyterhub_config.py` anpassen

---

## Optionale Services starten

Provider und Frontend sind optional und werden per `--profile` aktiviert:

```bash
# OpenAI Provider
docker compose -f docker/docker-compose.yml --profile provider-openai up -d

# Anthropic Provider
docker compose -f docker/docker-compose.yml --profile provider-anthropic up -d

# Chainlit Frontend
docker compose -f docker/docker-compose.yml --profile frontend up -d

# Alles auf einmal
docker compose -f docker/docker-compose.yml \
  --profile provider-openai --profile frontend up -d
```

Provider können auch einzeln gestartet werden (mit eigenem Build):

```bash
docker compose -f docker/llm-provider/compose.openai.yml up --build -d
docker compose -f docker/llm-provider/compose.anthropic.yml up --build -d
docker compose -f docker/llm-provider/compose.google.yml up --build -d
```

API-Keys für Provider in `docker/llm-provider/.env` eintragen (Vorlage: `.env.example`).

---

## Stack verwalten

```bash
# Status
docker compose -f docker/docker-compose.yml ps

# Logs eines Services
docker logs heinzel-mattermost -f

# Stack stoppen
docker compose -f docker/docker-compose.yml down

# Stack stoppen + Netzwerk entfernen
docker compose -f docker/docker-compose.yml down --remove-orphans
```

---

## Netzwerk

Alle Services laufen im Docker-Netzwerk `heinzel` (wird beim ersten Start automatisch angelegt).

---

## Fehlerbehebung

**Mattermost startet nicht:**
→ Datenbank `mattermost` in PostgreSQL anlegen (siehe Schritt 3).

**Portainer zeigt Zertifikatsfehler:**
→ Normal — selbstsigniertes Zertifikat. Im Browser "Trotzdem fortfahren" wählen.

**Gitea zeigt Installationsseite nach Neustart:**
→ Bind-Mount für `/data` prüfen: `ls ~/docker/gitea/data/`

**Container unhealthy obwohl erreichbar:**
→ `docker logs <container>` prüfen. Healthcheck-Fehler sind oft harmlos beim ersten Start (start_period abwarten).
