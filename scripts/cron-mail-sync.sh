#!/bin/sh
# Relève des alertes e-mail (APEC) pour une tâche planifiée (cron).
#
# Exemple de crontab (toutes les 15 minutes) — `crontab -e` :
#   */15 * * * * /chemin/vers/getiajob/scripts/cron-mail-sync.sh
#
# Journal : data/logs/mail-sync.log (seulement quand il se passe quelque chose, et les erreurs).
exec "$(dirname "$0")/cron-run.sh" mail-sync mail-sync --quiet "$@"
