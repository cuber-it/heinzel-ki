# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.  
Format: `[MVP-XX] — Datum — Kurzbeschreibung`, Details darunter.

---

## [MVP-00] — 2026-02-19 — Infrastruktur

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
- chown via Alpine-Container — kein sudo erforderlich (heinzel-user in docker group)
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
