#!/usr/bin/env bash
# H.E.I.N.Z.E.L. — Setup MVP-00
#
# Legt persistente Verzeichnisse an und bereitet die Umgebung vor.
# Aufruf: bash scripts/setup.sh <targetpath>
# Beispiel: bash scripts/setup.sh /home/heinzel-user/docker/heinzel

set -euo pipefail

# ─── Argumente ───────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Fehler: Kein Zielpfad angegeben."
    echo "Aufruf: bash scripts/setup.sh <targetpath>"
    echo "Beispiel: bash scripts/setup.sh /home/heinzel-user/docker/heinzel"
    exit 1
fi

DOCKER_BASE="$1"

# ─── .env prüfen ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "Fehler: .env nicht gefunden."
    echo "Bitte zuerst: cp .env.example .env && .env anpassen"
    exit 1
fi

source "$PROJECT_DIR/.env"

# ─── Verzeichnisse anlegen ───────────────────────────────────────────────────
echo "Lege persistente Verzeichnisse an unter: $DOCKER_BASE"
echo ""

for SERVICE in postgres mattermost jupyterhub caddy portainer; do
    for DIR in config data logs code; do
        mkdir -p "$DOCKER_BASE/$SERVICE/$DIR"
    done
    echo "  ✓ $SERVICE"
done

# ─── Mattermost: Berechtigungen ──────────────────────────────────────────────
echo ""
echo "Setze Mattermost-Berechtigungen (UID 2000)..."
if chown -R 2000:2000 "$DOCKER_BASE/mattermost" 2>/dev/null; then
    echo "  ✓ Mattermost chown gesetzt"
else
    echo "  ⚠ chown fehlgeschlagen — bitte manuell als root:"
    echo "    sudo chown -R 2000:2000 $DOCKER_BASE/mattermost"
fi

# ─── Caddy: Config kopieren ──────────────────────────────────────────────────
echo ""
echo "Kopiere Caddy-Config..."
cp "$PROJECT_DIR/docker/caddy/Caddyfile" "$DOCKER_BASE/caddy/config/Caddyfile"
echo "  ✓ Caddyfile → $DOCKER_BASE/caddy/config/Caddyfile"

# ─── JupyterHub: Config kopieren ─────────────────────────────────────────────
echo "Kopiere JupyterHub-Config..."
cp "$PROJECT_DIR/docker/jupyterhub/jupyterhub_config.py" "$DOCKER_BASE/jupyterhub/config/jupyterhub_config.py"
echo "  ✓ jupyterhub_config.py → $DOCKER_BASE/jupyterhub/config/"

# ─── Mattermost DB anlegen ───────────────────────────────────────────────────
echo ""
echo "Starte PostgreSQL kurz um Mattermost-DB anzulegen..."
cd "$PROJECT_DIR"
docker compose up -d postgres
echo "  Warte auf PostgreSQL..."
until docker exec heinzel-postgres pg_isready -U "$POSTGRES_USER" &>/dev/null; do
    sleep 2
done
echo "  ✓ PostgreSQL bereit"

docker exec heinzel-postgres psql -U "$POSTGRES_USER" \
    -c "CREATE DATABASE mattermost;" 2>/dev/null \
    && echo "  ✓ Datenbank 'mattermost' angelegt" \
    || echo "  ℹ Datenbank 'mattermost' bereits vorhanden"

# ─── Fertig ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Setup abgeschlossen."
echo ""
echo "Nächster Schritt:"
echo "  cd $PROJECT_DIR && docker compose up -d"
echo ""
echo "Services:"
echo "  Mattermost  → http://localhost:8001"
echo "  JupyterHub  → http://localhost:8888"
echo "  Caddy       → http://localhost:8000"
echo "  Portainer   → http://localhost:9000"
echo "  PostgreSQL  → localhost:5432"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
