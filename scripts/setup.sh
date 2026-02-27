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

if [[ "${GITEA_ADMIN_PASSWORD:-changeme}" == "changeme" ]]; then
    GITEA_PASS=$(openssl rand -base64 18 | tr -d '/=+' | head -c 24)
    sed -i "s/^GITEA_ADMIN_PASSWORD=.*/GITEA_ADMIN_PASSWORD=$GITEA_PASS/" "$PROJECT_DIR/.env"
    echo "  ✓ GITEA_ADMIN_PASSWORD generiert"
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

for SERVICE in postgres mattermost jupyterhub caddy portainer gitea; do
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

docker exec heinzel-postgres psql -U "$POSTGRES_USER" \
    -c "CREATE DATABASE gitea;" 2>/dev/null \
    && echo "  ✓ Datenbank 'gitea' angelegt" \
    || echo "  ℹ Datenbank 'gitea' bereits vorhanden"
echo ""

# ─── Gitea: Auto-Setup ───────────────────────────────────────────────────────
echo "Gitea Auto-Setup..."
docker compose up -d gitea 2>/dev/null
echo "  Warte auf Gitea..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3000 | grep -q "Installation"; then
        break
    fi
    sleep 2
done

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:3000 \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "db_type=postgres" \
    --data-urlencode "db_host=postgres:5432" \
    --data-urlencode "db_name=gitea" \
    --data-urlencode "db_user=${POSTGRES_USER}" \
    --data-urlencode "db_passwd=${POSTGRES_PASSWORD}" \
    --data-urlencode "db_schema=" \
    --data-urlencode "ssl_mode=disable" \
    --data-urlencode "app_name=H.E.I.N.Z.E.L. Gitea" \
    --data-urlencode "repo_root_path=/data/gitea/repositories" \
    --data-urlencode "lfs_root_path=/data/gitea/lfs" \
    --data-urlencode "run_user=git" \
    --data-urlencode "domain=localhost" \
    --data-urlencode "ssh_port=2222" \
    --data-urlencode "http_port=3000" \
    --data-urlencode "app_url=http://localhost:3000" \
    --data-urlencode "log_root_path=/data/gitea/log" \
    --data-urlencode "offline_mode=on" \
    --data-urlencode "disable_gravatar=on" \
    --data-urlencode "enable_federated_avatar=" \
    --data-urlencode "enable_open_id_sign_in=" \
    --data-urlencode "enable_open_id_sign_up=" \
    --data-urlencode "disable_registration=on" \
    --data-urlencode "allow_only_external_registration=" \
    --data-urlencode "enable_captcha=" \
    --data-urlencode "require_sign_in_view=on" \
    --data-urlencode "default_keep_email_private=on" \
    --data-urlencode "default_allow_create_organization=on" \
    --data-urlencode "default_enable_timetracking=on" \
    --data-urlencode "enable_update_checker=" \
    --data-urlencode "admin_name=${GITEA_ADMIN_USER}" \
    --data-urlencode "admin_passwd=${GITEA_ADMIN_PASSWORD}" \
    --data-urlencode "admin_confirm_passwd=${GITEA_ADMIN_PASSWORD}" \
    --data-urlencode "admin_email=${GITEA_ADMIN_EMAIL}")

if [[ "$HTTP_STATUS" == "302" || "$HTTP_STATUS" == "200" ]]; then
    echo "  ✓ Gitea eingerichtet (Admin: ${GITEA_ADMIN_USER})"
    echo "  ℹ Passwort liegt in .env (GITEA_ADMIN_PASSWORD)"
else
    echo "  ℹ Gitea bereits eingerichtet oder manuelles Setup erforderlich (HTTP $HTTP_STATUS)"
fi
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
echo "  Gitea       → http://localhost:3000"
echo "  PostgreSQL  → localhost:5432"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
