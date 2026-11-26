#!/usr/bin/env bash

set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s\n' "${0##*/}"
  printf 'Choose the environment that will replace the local database and uploaded files.\n'
  printf 'PowerSync rebuilds its local cache after the restore.\n'
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  usage
  exit 0
fi

if (( $# > 0 )); then
  usage >&2
  exit 2
fi

printf 'This replaces the local database and uploaded files.\nSource environment\n  1) production\nSelect an environment: ' >&2
read -r selection
case "$selection" in
  1|production)
    environment=production
    source_host=hetzner
    source_database=papyrus-production-database-1
    source_api=papyrus-production-api-1
    source_media_root=/var/lib/papyrus/media
    ;;
  *)
    printf 'Unknown source environment.\n' >&2
    exit 2
    ;;
esac

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
docker_endpoint=$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')

if [[ $docker_endpoint != unix:///* ]]; then
  printf 'Select a Docker context using a local Unix socket.\n' >&2
  exit 1
fi

export DOCKER_HOST="$docker_endpoint"
unset DOCKER_CONTEXT

postgres() {
  local service=$1
  shift
  docker compose exec -T "$service" sh -ec '
    command=$1
    shift
    export PGPASSWORD="$POSTGRES_PASSWORD"
    exec "$command" -w -U "$POSTGRES_USER" "$@"
  ' sh "$@"
}

source_ssh() {
  ssh -T -o BatchMode=yes -o ConnectTimeout=10 -- "$source_host" "$@"
}

set_database_connections() {
  postgres database psql -X -q -v ON_ERROR_STOP=1 -d postgres \
    -v database="$target_db" -v allow="$1" <<'SQL'
ALTER DATABASE :"database" WITH ALLOW_CONNECTIONS :allow;
SELECT pg_terminate_backend(pid, 5000)
FROM pg_stat_activity
WHERE datname = :'database' AND NOT :allow
\g /dev/null
SQL
}

cleanup() {
  if [[ $replaced == false ]]; then
    if [[ $media_installed == true ]]; then
      mv -- "$media_root" "$work_dir/failed-media"
    fi

    if [[ -e $work_dir/previous-media ]]; then
      mv -- "$work_dir/previous-media" "$media_root" || return
    fi

    if [[ $connections_blocked == true ]]; then
      set_database_connections true || true
    fi
  fi

  if [[ $staging_created == true ]]; then
    postgres database dropdb --if-exists --force "$staging_db" || true
  fi

  rm -rf -- "$work_dir"

  if [[ $services_stopped == true ]]; then
    docker compose up -d --no-deps "${services[@]}"
  fi
}

dump_database() {
  source_ssh "docker exec -i $source_database sh -es" <<'REMOTE'
export PGPASSWORD="$POSTGRES_PASSWORD"
export PGOPTIONS="-c default_transaction_read_only=on"
exec pg_dump --no-password --format=custom --lock-wait-timeout=10s \
  --no-privileges --no-publications --no-subscriptions \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"
REMOTE
}

media_paths() {
  local checksum path
  while IFS=' ' read -r checksum path; do
    if [[ ! $checksum =~ ^[0-9a-f]{64}$ || ! $path =~ ^[0-9a-f-]+/[0-9a-f-]+/[0-9a-f-]+\.[a-z0-9]+$ ]]; then
      printf 'Invalid media metadata in the restored database.\n' >&2
      return 1
    fi

    printf '%s\0' "$path"
  done
}

settings=$(uv run --locked python - <<'PYTHON'
from pathlib import Path

from papyrus.config import get_settings

settings = get_settings()
media_root = Path(settings.media_storage_root).resolve()
if media_root in (Path.cwd(), *Path.cwd().parents):
    raise SystemExit("MEDIA_STORAGE_ROOT must identify the local uploads directory.")
if not settings.powersync_storage_db:
    raise SystemExit("POWERSYNC_STORAGE_DB must be configured.")
if settings.postgres_host not in ("127.0.0.1", "::1", "localhost"):
    raise SystemExit("POSTGRES_HOST must point to the local Docker database.")

print(settings.postgres_db)
print(settings.postgres_port)
print(settings.powersync_storage_db)
print(media_root)
PYTHON
)
{
  IFS= read -r target_db
  IFS= read -r target_port
  IFS= read -r storage_db
  IFS= read -r media_root
} <<< "$settings"

if [[ $target_db == postgres || $target_db == template* || $storage_db == postgres || $storage_db == template* ]]; then
  printf 'Select application databases for the local API and PowerSync.\n' >&2
  exit 1
fi

docker compose up -d --wait database powersync-storage
published_address=$(docker compose port database 5432)

if [[ ${published_address##*:} != "$target_port" ]]; then
  printf 'The API PostgreSQL port does not match the local Compose database.\n' >&2
  exit 1
fi

media_parent=$(dirname -- "$media_root")
mkdir -p -- "$media_parent"
work_dir="$media_parent/.papyrus-clone.work"
staged_media="$work_dir/media"
media_manifest="$work_dir/media.sha256"
staging_db="papyrus_clone_$(date -u +%Y%m%d%H%M%S)_$$"
previous_db="${staging_db}_previous"
staging_created=false
connections_blocked=false
services_stopped=false
media_installed=false
replaced=false
services=(powersync)

if ! mkdir -- "$work_dir"; then
  printf 'Could not reserve the clone workspace at %s.\n' "$work_dir" >&2
  exit 1
fi

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -- "$staged_media"
postgres database createdb "$staging_db"
staging_created=true

printf 'Restoring the %s database into local PostgreSQL.\n' "$environment" >&2
dump_database | postgres database pg_restore \
  --single-transaction --no-owner --no-privileges --no-tablespaces --dbname="$staging_db"

postgres database psql -XAt -v ON_ERROR_STOP=1 -d "$staging_db" \
  -c "SELECT sha256 || '  ' || storage_path FROM public.media_assets ORDER BY storage_path" > "$media_manifest"
media_count=$(wc -l < "$media_manifest")
printf 'Copying %d uploaded files from %s.\n' "$media_count" "$environment" >&2

if [[ -s $media_manifest ]]; then
  media_paths < "$media_manifest" \
    | source_ssh "docker exec -i $source_api tar --no-recursion --null -C $source_media_root -cf - -T -" \
    | tar --no-same-owner --no-same-permissions -xf - -C "$staged_media"

  if ! (cd -- "$staged_media" && shasum -a 256 --check --status "$media_manifest"); then
    printf 'Uploaded files did not match the restored database checksums.\n' >&2
    exit 1
  fi
fi

POSTGRES_DB="$staging_db" uv run --locked alembic upgrade head
./scripts/setup_local_powersync.sh "$staging_db"

if [[ -n $(docker compose ps --status running --quiet server) ]]; then
  services+=(server)
fi

printf 'Replacing the local database and media, then rebuilding PowerSync.\n' >&2
services_stopped=true
docker compose stop "${services[@]}"
connections_blocked=true
set_database_connections false

if [[ -e $media_root ]]; then
  mv -- "$media_root" "$work_dir/previous-media"
fi

mv -- "$staged_media" "$media_root"
media_installed=true
postgres database psql -X -q -v ON_ERROR_STOP=1 -d postgres \
  -v target="$target_db" -v staging="$staging_db" -v previous="$previous_db" <<'SQL'
BEGIN;
ALTER DATABASE :"target" RENAME TO :"previous";
ALTER DATABASE :"staging" RENAME TO :"target";
COMMIT;
SQL
replaced=true
staging_created=false
postgres database dropdb --force "$previous_db"

postgres powersync-storage dropdb --force "$storage_db"
postgres powersync-storage createdb "$storage_db"
touch papyrus/config.py
docker compose up -d --wait --wait-timeout 60 --no-deps "${services[@]}"
services_stopped=false

printf 'The local API and PowerSync now use %s data with %d verified uploaded files.\n' "$environment" "$media_count"
