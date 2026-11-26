#!/bin/sh
# Déploiement sur le VPS, lancé par GitHub Actions (ou à la main) depuis le dossier du projet :
#   deploy/deploy.sh [tag]        tag de l'image GHCR (défaut : latest)
#
# Pré-requis : .env contenant COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml (et
# COMPOSE_PROFILES=prod seulement si Caddy sert de proxy HTTPS ; cf. docker-compose.prod.yml).
# La base et les données ne sont pas touchées ; les mises à niveau du schéma sont appliquées au
# démarrage de l'application.
set -eu

TAG="${1:-latest}"
cd "$(dirname "$0")/.."
export GETIAJOB_TAG="$TAG"

echo "→ Récupération de l'image ($TAG)"
docker compose pull web app

echo "→ Redémarrage des services"
# Caddy (service « proxy ») seulement si le profil prod est actif (COMPOSE_PROFILES=prod) ;
# derrière un Apache/Nginx déjà installé sur le serveur, on ne lance que db et web.
services="db web"
if docker compose config --services | grep -qx proxy; then
    services="$services proxy"
fi
# shellcheck disable=SC2086
docker compose up -d --remove-orphans $services

echo "→ Attente de l'application"
container="$(docker compose ps -q web)"
i=0
while [ "$i" -lt 30 ]; do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo unknown)"
    if [ "$status" = "healthy" ]; then
        echo "✔ Application en ligne ($TAG)"
        # « latest » local = version déployée : un redémarrage sans GETIAJOB_TAG (scripts/restart.sh,
        # docker compose up) relance cette version, et non une ancienne image restée en cache.
        image="$(docker inspect -f '{{.Config.Image}}' "$container")"
        if [ "${image##*:}" != "latest" ]; then
            docker tag "$image" "${image%:*}:latest"
        fi
        docker image prune -f >/dev/null
        exit 0
    fi
    i=$((i + 1))
    sleep 5
done

echo "✘ L'application n'est pas « healthy » après 150 s — derniers journaux :" >&2
docker compose logs --tail 50 web >&2
exit 1
