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
GRANT SELECT ON TABLE public.books TO "${POWERSYNC_SOURCE_ROLE}";
GRANT SELECT ON TABLE public.annotations TO "${POWERSYNC_SOURCE_ROLE}";
GRANT SELECT ON TABLE public.reading_sessions TO "${POWERSYNC_SOURCE_ROLE}";
GRANT SELECT ON TABLE public.powersync_demo_items TO "${POWERSYNC_SOURCE_ROLE}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "${POWERSYNC_SOURCE_ROLE}";

DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'powersync') THEN
    CREATE PUBLICATION powersync FOR TABLE public.books, public.annotations, public.reading_sessions, public.powersync_demo_items;
  ELSE
    IF NOT EXISTS (
      SELECT 1
      FROM pg_publication_tables
      WHERE pubname = 'powersync' AND schemaname = 'public' AND tablename = 'books'
    ) THEN
      ALTER PUBLICATION powersync ADD TABLE public.books;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_publication_tables
      WHERE pubname = 'powersync' AND schemaname = 'public' AND tablename = 'annotations'
    ) THEN
      ALTER PUBLICATION powersync ADD TABLE public.annotations;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_publication_tables
      WHERE pubname = 'powersync' AND schemaname = 'public' AND tablename = 'reading_sessions'
    ) THEN
      ALTER PUBLICATION powersync ADD TABLE public.reading_sessions;
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_publication_tables
      WHERE pubname = 'powersync' AND schemaname = 'public' AND tablename = 'powersync_demo_items'
    ) THEN
      ALTER PUBLICATION powersync ADD TABLE public.powersync_demo_items;
    END IF;
  END IF;
END
\$\$;
SQL
