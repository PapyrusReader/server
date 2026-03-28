#!/usr/bin/env sh

set -eu

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POWERSYNC_SOURCE_ROLE:?POWERSYNC_SOURCE_ROLE is required}"
: "${POWERSYNC_SOURCE_PASSWORD:?POWERSYNC_SOURCE_PASSWORD is required}"

docker compose exec -T database env PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${POWERSYNC_SOURCE_ROLE}') THEN
    EXECUTE format(
      'CREATE ROLE %I WITH REPLICATION BYPASSRLS LOGIN PASSWORD %L',
      '${POWERSYNC_SOURCE_ROLE}',
      '${POWERSYNC_SOURCE_PASSWORD}'
    );
  ELSE
    EXECUTE format('ALTER ROLE %I WITH REPLICATION BYPASSRLS LOGIN PASSWORD %L', '${POWERSYNC_SOURCE_ROLE}', '${POWERSYNC_SOURCE_PASSWORD}');
  END IF;
END
\$\$;

GRANT USAGE ON SCHEMA public TO "${POWERSYNC_SOURCE_ROLE}";
GRANT SELECT ON TABLE public.powersync_demo_items TO "${POWERSYNC_SOURCE_ROLE}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "${POWERSYNC_SOURCE_ROLE}";

DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'powersync') THEN
    CREATE PUBLICATION powersync FOR TABLE public.powersync_demo_items;
  ELSIF NOT EXISTS (
    SELECT 1
    FROM pg_publication_tables
    WHERE pubname = 'powersync' AND schemaname = 'public' AND tablename = 'powersync_demo_items'
  ) THEN
    ALTER PUBLICATION powersync ADD TABLE public.powersync_demo_items;
  END IF;
END
\$\$;
SQL
