#!/bin/sh
# Supervision planifiée : détecte les problèmes et prévient les administrateurs par e-mail.
#
# Exemple de crontab (toutes les 10 minutes) — `crontab -e` :
#   */10 * * * * /chemin/vers/getiajob/scripts/cron-monitor.sh
#
# Journal : data/logs/monitor.log (seulement quand une alerte s'ouvre, est rappelée ou se résout).
exec "$(dirname "$0")/cron-run.sh" monitor monitor --quiet "$@"
