#!/bin/bash
# setup-mvp00.sh — Persistente Verzeichnisse für alle MVP-00 Container anlegen
# Aufruf: bash scripts/setup-mvp00.sh

set -e

DOCKER_BASE="$HOME/docker"

echo "Lege Verzeichnisse an unter $DOCKER_BASE ..."

for SERVICE in postgres mattermost jupyterhub webserver; do
    for DIR in config data logs code; do
        mkdir -p "$DOCKER_BASE/$SERVICE/$DIR"
    done
    echo "  ✓ $SERVICE"
done

# Mattermost braucht zusätzlich plugins und client
mkdir -p "$DOCKER_BASE/mattermost/plugins"
mkdir -p "$DOCKER_BASE/mattermost/client"

# Berechtigungen Mattermost (läuft als UID 2000)
chown -R 2000:2000 "$DOCKER_BASE/mattermost" 2>/dev/null || \
    echo "  ⚠ Mattermost: chown fehlgeschlagen (ggf. als root ausführen)"

echo ""
echo "Fertig. Verzeichnisstruktur:"
tree "$DOCKER_BASE" -d 2>/dev/null || find "$DOCKER_BASE" -type d | sort
