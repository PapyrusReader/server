#!/usr/bin/env sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
python3 validate_config.py
compose() { docker compose --env-file production.env -f compose.yml "$@"; }
compose config --quiet
docker network inspect papyrus-edge >/dev/null
compose pull
compose up -d --wait database powersync-storage
compose stop api powersync web
compose run --rm migrate
# shellcheck disable=SC2016
compose exec -T database sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /bootstrap-powersync.sql'
compose up -d --wait api powersync web
