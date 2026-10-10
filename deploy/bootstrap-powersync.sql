\set ON_ERROR_STOP on
\getenv source_password POWERSYNC_SOURCE_PASSWORD
SELECT 'CREATE ROLE powersync_role WITH REPLICATION BYPASSRLS LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powersync_role')
\gexec
ALTER ROLE powersync_role WITH REPLICATION BYPASSRLS LOGIN PASSWORD :'source_password';
GRANT USAGE ON SCHEMA public TO powersync_role;
GRANT SELECT ON TABLE public.books, public.shelves, public.tags, public.notes, public.annotations, public.bookmarks, public.book_shelves, public.book_tags, public.reading_goals, public.reading_activities, public.goal_periods, public.powersync_demo_items TO powersync_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO powersync_role;
SELECT 'CREATE PUBLICATION powersync' WHERE NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'powersync')
\gexec
ALTER PUBLICATION powersync SET TABLE public.books, public.shelves, public.tags, public.notes, public.annotations, public.bookmarks, public.book_shelves, public.book_tags, public.reading_goals, public.reading_activities, public.goal_periods, public.powersync_demo_items;
