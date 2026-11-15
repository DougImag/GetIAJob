#!/bin/sh
# Sauvegarde de la base PostgreSQL (pg_dump, format compressé) dans data/backups/.
# Garde les KEEP (14 par défaut) dernières sauvegardes et l'enregistre pour la supervision.
#
# Exemple de crontab (chaque nuit à 3 h) — `crontab -e` :
#   0 3 * * * /chemin/vers/getiajob/scripts/backup-db.sh
#
# Restauration : docker compose exec -T db pg_restore -U getiajob -d getiajob --clean < data/backups/<fichier>

set -u
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
export PATH
KEEP="${KEEP:-14}"
DB_USER="${POSTGRES_USER:-getiajob}"
DB_NAME="${POSTGRES_DB:-getiajob}"

cd "$PROJECT_DIR" || exit 1
BACKUP_DIR="$PROJECT_DIR/data/backups"
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
FILE="getiajob-$(date '+%Y%m%d-%H%M%S').dump"
LOG_FILE="$LOG_DIR/backup.log"

if docker compose exec -T db pg_dump -U "$DB_USER" -Fc "$DB_NAME" >"$BACKUP_DIR/$FILE" 2>>"$LOG_FILE" \
    && [ -s "$BACKUP_DIR/$FILE" ]; then
    SIZE=$(wc -c <"$BACKUP_DIR/$FILE" | tr -d ' ')
    echo "$(date '+%Y-%m-%d %H:%M:%S') ✔ $FILE ($SIZE octets)" >>"$LOG_FILE"
    "$PROJECT_DIR/scripts/cron-run.sh" backup record-backup "$FILE" --size "$SIZE"
    # Rotation : on ne garde que les KEEP sauvegardes les plus récentes
    ls -1t "$BACKUP_DIR"/getiajob-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
        rm -f "$old"
    done
    status=0
else
    rm -f "$BACKUP_DIR/$FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') ✘ sauvegarde impossible" >>"$LOG_FILE"
    "$PROJECT_DIR/scripts/cron-run.sh" backup record-backup "$FILE" --failed --detail "pg_dump en échec"
    status=1
fi
if [ "$(wc -l <"$LOG_FILE")" -gt 2000 ]; then
    tail -n 2000 "$LOG_FILE" >"$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
exit "$status"
