#!/bin/sh
# Relance l'interface web en recréant son conteneur, pour prendre en compte une modification du .env
# (un simple `docker compose restart` garde les anciennes variables d'environnement).
#   scripts/restart.sh           recrée le conteneur `web` et attend qu'il réponde
#   scripts/restart.sh --build   reconstruit d'abord l'image (développement local, code modifié),
#                                puis supprime l'image remplacée une fois la nouvelle version démarrée
#
# En production, le .env (COMPOSE_FILE=…) fait utiliser la surcharge docker-compose.prod.yml.
# La base de données n'est pas redémarrée. Une analyse ou une recherche en cours est interrompue.

set -u
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
export PATH
TIMEOUT="${TIMEOUT:-120}"

cd "$PROJECT_DIR" || exit 1

BUILD=""
case "${1:-}" in
    "") ;;
    --build) BUILD="--build" ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage : $0 [--build]" >&2; exit 2 ;;
esac

# Image utilisée avant la reconstruction (supprimée si la nouvelle version démarre bien)
OLD_IMAGE=""
if [ -n "$BUILD" ] && OLD_CID="$(docker compose ps -q web 2>/dev/null)" && [ -n "$OLD_CID" ]; then
    OLD_IMAGE="$(docker inspect -f '{{.Image}}' "$OLD_CID" 2>/dev/null)"
fi

echo "→ Recréation du conteneur web…"
# --no-deps : ne touche pas à la base ; --force-recreate : relit le .env même si rien n'a changé
docker compose up -d --no-deps --force-recreate $BUILD web || exit 1

# Supprime l'image remplacée par --build (sans --force : refusé si un autre conteneur l'utilise encore).
# Seule l'ancienne image de l'application est concernée, jamais celles d'autres projets.
cleanup_old_image() {
    [ -n "$OLD_IMAGE" ] || return 0
    new_image="$(docker inspect -f '{{.Image}}' "$CID" 2>/dev/null)"
    [ -n "$new_image" ] && [ "$new_image" != "$OLD_IMAGE" ] || return 0
    if docker image rm "$OLD_IMAGE" >/dev/null 2>&1; then
        echo "Ancienne image supprimée."
    fi
}

CID="$(docker compose ps -q web)"
printf "→ Attente du démarrage"
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null)"
    if docker compose exec -T web python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" >/dev/null 2>&1; then
        echo " ✔"
        echo "Application relancée."
        cleanup_old_image
        exit 0
    fi
    case "$health" in
        exited|dead|unhealthy) break ;;
    esac
    printf "."
    sleep 2
    elapsed=$((elapsed + 2))
done

echo " ✘"
echo "L'application ne répond pas (état : ${health:-inconnu}). Dernières lignes du journal :" >&2
docker compose logs --tail 30 web >&2
exit 1
