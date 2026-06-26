#!/usr/bin/env sh

set -eu

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

if [ ! -f "${POWERSYNC_JWT_PRIVATE_KEY_FILE:-.local/powersync/private.pem}" ] ||
   [ ! -f "${POWERSYNC_JWT_PUBLIC_KEY_FILE:-.local/powersync/public.pem}" ]; then
  ./scripts/generate_dev_powersync_keys.sh
fi

docker compose up -d --wait database powersync-storage mailpit
uv run alembic upgrade head
./scripts/setup_local_powersync.sh
docker compose up -d --build --wait server powersync

printf '%s\n' "Papyrus API: http://localhost:${PORT:-8080}"
printf '%s\n' "PowerSync: http://localhost:${POWERSYNC_SERVICE_PORT:-8081}"
printf '%s\n' "Mailpit: http://localhost:8025"
