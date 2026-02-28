#!/usr/bin/env bash
# H.E.I.N.Z.E.L. — Setup MVP-00
#
# Legt persistente Verzeichnisse an, generiert Secrets und bereitet
# das Docker-Netzwerk vor. Startet keine Container.
#
# Aufruf: bash scripts/setup.sh
# Optional: DOCKER_BASE=/custom/path bash scripts/setup.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "H.E.I.N.Z.E.L. Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─── .env anlegen ─────────────────────────────────────────────────────────────
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "Keine .env gefunden — erstelle aus .env.example..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "  ✓ .env angelegt"
fi

source "$PROJECT_DIR/.env"

DOCKER_BASE="${DOCKER_BASE:-$HOME/docker}"

# ─── Secrets generieren falls noch auf Default ────────────────────────────────
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

if [[ "${GITEA_ADMIN_PASSWORD:-changeme}" == "changeme" ]]; then
    GITEA_PASS=$(openssl rand -base64 18 | tr -d '/=+' | head -c 24)
    sed -i "s/^GITEA_ADMIN_PASSWORD=.*/GITEA_ADMIN_PASSWORD=$GITEA_PASS/" "$PROJECT_DIR/.env"
    echo "  ✓ GITEA_ADMIN_PASSWORD generiert"
    CHANGED=true
fi

# DOCKER_BASE in .env setzen
sed -i "s|^DOCKER_BASE=.*|DOCKER_BASE=$DOCKER_BASE|" "$PROJECT_DIR/.env"

if [[ "$CHANGED" == "true" ]]; then
    echo ""
    echo "  ⚠  Secrets in .env gespeichert — bitte sichern!"
    echo "     cat $PROJECT_DIR/.env"
fi
echo ""

# ─── Docker-Netzwerk ──────────────────────────────────────────────────────────
echo "Prüfe Docker-Netzwerk 'heinzel'..."
if ! docker network inspect heinzel &>/dev/null; then
    docker network create heinzel
    echo "  ✓ Netzwerk 'heinzel' angelegt"
else
    echo "  ℹ Netzwerk 'heinzel' bereits vorhanden"
fi
echo ""

# ─── Verzeichnisse anlegen ────────────────────────────────────────────────────
echo "Lege persistente Verzeichnisse an unter: $DOCKER_BASE"
echo ""

for SERVICE in postgres mattermost jupyterhub caddy portainer gitea llm-provider; do
    for DIR in config data logs code; do
        mkdir -p "$DOCKER_BASE/$SERVICE/$DIR"
    done
    echo "  ✓ $SERVICE"
done
echo ""

# ─── Mattermost: Berechtigungen ───────────────────────────────────────────────
echo "Setze Mattermost-Berechtigungen (UID 2000)..."
docker run --rm -v "$DOCKER_BASE/mattermost:/mnt" alpine chown -R 2000:2000 /mnt 2>/dev/null \
    && echo "  ✓ Mattermost chown gesetzt" \
    || echo "  ⚠ chown fehlgeschlagen — ggf. manuell: sudo chown -R 2000:2000 $DOCKER_BASE/mattermost"
echo ""

# ─── Caddy Config ─────────────────────────────────────────────────────────────
if [[ ! -f "$DOCKER_BASE/caddy/config/Caddyfile" ]]; then
    cp "$PROJECT_DIR/docker/caddy/config/Caddyfile" "$DOCKER_BASE/caddy/config/Caddyfile"
    echo "  ✓ Caddyfile kopiert"
fi

if [[ ! -f "$DOCKER_BASE/jupyterhub/config/jupyterhub_config.py" ]]; then
    cp "$PROJECT_DIR/docker/jupyterhub/config/jupyterhub_config.py" \
       "$DOCKER_BASE/jupyterhub/config/jupyterhub_config.py"
    echo "  ✓ jupyterhub_config.py kopiert"
fi
echo ""

# ─── Fertig ───────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Setup abgeschlossen."
echo ""
echo "Nächste Schritte:"
echo "  1. Stack starten:"
echo "     docker compose -f docker/docker-compose.yml --env-file .env up -d"
echo ""
echo "  2. Mattermost-Datenbank anlegen (nach erstem Postgres-Start):"
echo "     docker exec heinzel-postgres psql -U heinzel -c 'CREATE DATABASE mattermost;'"
echo ""
echo "  3. Accounts manuell einrichten — siehe docs/mvp-00.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
