#!/usr/bin/env bash
# Legt die Mattermost-Datenbank an — einmalig nach erstem postgres-Start
docker exec heinzel-postgres psql -U heinzel \
    -c "CREATE DATABASE mattermost;" 2>/dev/null \
    && echo "✓ mattermost DB angelegt" \
    || echo "⚠ Bereits vorhanden"
