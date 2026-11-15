#!/bin/sh
# Lance une commande `getiajob` depuis cron et journalise sa sortie.
#   scripts/cron-run.sh <nom-du-journal> <commande> [options…]
# Exemple : scripts/cron-run.sh monitor monitor --quiet   →  data/logs/monitor.log
#
# La commande s'exécute dans le conteneur `web` déjà démarré (docker compose up -d web) ; à défaut,
# dans un conteneur `app` éphémère. Le journal est limité à 2 000 lignes.

set -u
if [ "$#" -lt 2 ]; then
    echo "usage : $0 <nom-du-journal> <commande> [options…]" >&2
    exit 2
fi
NAME="$1"
shift
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# cron démarre avec un PATH minimal : on ajoute les emplacements usuels de docker (Mac, Linux).
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
export PATH

LOG_DIR="$PROJECT_DIR/data/logs"
LOG_FILE="$LOG_DIR/$NAME.log"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

if docker compose ps --status running --services 2>/dev/null | grep -qx web; then
    docker compose exec -T web getiajob "$@" >>"$LOG_FILE" 2>&1
else
    docker compose run --rm -T app "$@" >>"$LOG_FILE" 2>&1
fi
status=$?
if [ "$status" -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ✘ échec de « getiajob $1 » (code $status)" >>"$LOG_FILE"
fi

if [ "$(wc -l <"$LOG_FILE")" -gt 2000 ]; then
    tail -n 2000 "$LOG_FILE" >"$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
exit "$status"
