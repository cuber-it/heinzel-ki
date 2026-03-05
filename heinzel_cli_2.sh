#!/usr/bin/env bash
# heinzel_cli_2.sh — Heinzel CLI 2 mit venv starten
#
# Verwendung:
#   ./heinzel_cli_2.sh
#   ./heinzel_cli_2.sh --config config/riker.yaml
#   ./heinzel_cli_2.sh --provider http://thebrain:12102

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
CLI="$SCRIPT_DIR/src/frontend/heinzel_cli_2.py"
DEFAULT_CONFIG="$SCRIPT_DIR/config/heinzel_cli_2.yaml"

# venv prüfen
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "❌ venv nicht gefunden: $VENV"
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Abhängigkeiten sicherstellen (schnell wenn alles schon da)
if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
fi

# data/ und logs/ anlegen falls nötig
mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs/dialogs"

# Default-Config vorbelegen wenn kein --config übergeben
ARGS=("$@")
if [[ ! " ${ARGS[*]} " =~ " --config " ]] && [[ ! " ${ARGS[*]} " =~ " -c " ]]; then
    ARGS=("--config" "$DEFAULT_CONFIG" "${ARGS[@]}")
fi

exec "$VENV/bin/python" "$CLI" "${ARGS[@]}"
