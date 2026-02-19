#!/usr/bin/env bash
# H.E.I.N.Z.E.L. — Setup MVP-00
#
# Legt persistente Verzeichnisse an und bereitet die Umgebung vor.
# Aufruf: bash scripts/setup.sh [targetpath]
# Default: ~/docker — Beispiel: bash scripts/setup.sh /srv/heinzel

set -uo pipefail

DOCKER_BASE="${1:-$HOME/docker}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "H.E.I.N.Z.E.L. Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─── .env anlegen falls nicht vorhanden ──────────────────────────────────────
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "Keine .env gefunden — erstelle aus .env.example..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "  ✓ .env angelegt"
fi

source "$PROJECT_DIR/.env"

# ─── Passwörter generieren falls noch auf Default ────────────────────────────
CHANGED=false

if [[ "${POSTGRES_PASSWORD:-changeme}" == "changeme" ]]; then
    PG_PASS=$(openssl rand -base64 24 | tr -d '/=+' | head -c 32)
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PG_PASS/" "$PROJECT_DIR/.env"
    echo "  ✓ POSTGRES_PASSWORD generiert"
    CHANGED=true
fi

if [[ "${JUPYTERHUB_CRYPT_KEY:-changeme}" == "changeme" ]]; then
    JH_KEY=$(openssl rand -hex 32)
    sed -i "s/^JUPYTERHUB_CRYPT_KEY=.*/JUPYTERHUB_CRYPT_KEY=$JH_KEY/" "$PROJECT_DIR/.env"
    echo "  ✓ JUPYTERHUB_CRYPT_KEY generiert"
    CHANGED=true
fi

if [[ "$CHANGED" == "true" ]]; then
    echo "  ℹ Secrets in .env gespeichert — bitte sichern!"
    source "$PROJECT_DIR/.env"
    echo ""
fi

# ─── Docker Netzwerk ─────────────────────────────────────────────────────────
echo "Prüfe Docker-Netzwerk 'heinzel'..."
if ! docker network inspect heinzel &>/dev/null; then
    docker network create heinzel
    echo "  ✓ Netzwerk 'heinzel' angelegt"
else
    echo "  ℹ Netzwerk 'heinzel' bereits vorhanden"
fi
echo ""

# ─── Verzeichnisse anlegen ───────────────────────────────────────────────────
echo "Lege persistente Verzeichnisse an unter: $DOCKER_BASE"
echo ""

for SERVICE in postgres mattermost jupyterhub caddy portainer; do
    for DIR in config data logs code; do
        mkdir -p "$DOCKER_BASE/$SERVICE/$DIR"
    done
    echo "  ✓ $SERVICE"
done
echo ""

# ─── Mattermost: Berechtigungen ──────────────────────────────────────────────
echo "Setze Mattermost-Berechtigungen (UID 2000)..."
docker run --rm -v "$DOCKER_BASE/mattermost:/mnt" alpine chown -R 2000:2000 /mnt
echo "  ✓ Mattermost chown gesetzt"
echo ""

# ─── Caddy: Config kopieren ──────────────────────────────────────────────────
echo "Kopiere Configs..."
cp "$PROJECT_DIR/docker/caddy/Caddyfile" "$DOCKER_BASE/caddy/config/Caddyfile"
echo "  ✓ Caddyfile"
cp "$PROJECT_DIR/docker/jupyterhub/jupyterhub_config.py" "$DOCKER_BASE/jupyterhub/config/jupyterhub_config.py"
echo "  ✓ jupyterhub_config.py"
echo ""

# ─── Mattermost DB anlegen ───────────────────────────────────────────────────
echo "Starte PostgreSQL und lege Mattermost-DB an..."
cd "$PROJECT_DIR"
docker compose up -d postgres
until docker exec heinzel-postgres pg_isready -U "$POSTGRES_USER" &>/dev/null; do
    sleep 2
done
echo "  ✓ PostgreSQL bereit"

docker exec heinzel-postgres psql -U "$POSTGRES_USER" \
    -c "CREATE DATABASE mattermost;" 2>/dev/null \
    && echo "  ✓ Datenbank 'mattermost' angelegt" \
    || echo "  ℹ Datenbank 'mattermost' bereits vorhanden"
echo ""

# ─── Fertig ──────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Setup abgeschlossen."
echo ""
echo "Starten: docker compose up -d"
echo ""
echo "Services:"
echo "  Mattermost  → http://localhost:8001"
echo "  JupyterHub  → http://localhost:8888"
echo "  Caddy       → http://localhost:8000"
echo "  Portainer   → http://localhost:9000"
echo "  PostgreSQL  → localhost:5432"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
