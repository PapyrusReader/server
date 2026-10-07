# Reading tracking contract and rollout

Tracking schema version 2 is advertised additively by `GET /v1/sync/settings`.
Goals/session/statistics REST endpoints now return owned persisted records and
ledger-derived progress. Existing metric strings remain valid; `reading_days` is
additive. Goal definition, activity, and period payloads are validated in the same
transaction/owner lock used by ordinary PowerSync uploads.

`reading_goals`, `reading_activities`, and `goal_periods` store owner-scoped JSONB
payloads with UUID identities. Activity is immutable: correction/undo appends a
reversal referencing an owned original. Retries are idempotent. Shelf membership
and book-title snapshots survive book deletion. Activity/period deletion through
sync is rejected; deleting a goal retains rule/period history. Account deletion
still removes owned data.

Progress unions overlapping time intervals and per-book/day content coverage,
counts distinct confirmed books, and applies creation cutoffs, pauses, captured
IANA timezone, Monday weeks, and historical rule revisions. Period history holds
rules rather than mutable counters, allowing delayed corrections to reproject it.
Shared fixtures in `tests/fixtures/tracking_projections.json` match Dart tests.

## Deployment order

1. Apply Alembic revision `b5c6d7e8f901` (`uv run alembic upgrade head`). This is
   additive and preserves existing library tables/data.
2. Add the three tracking tables to the PostgreSQL PowerSync publication and grants.
   Updated `deploy/bootstrap-powersync.sql` and `scripts/setup_local_powersync.sh`
   include them. Existing deployments must run the updated bootstrap; creating
   database tables alone does not update an existing publication.
3. Deploy the updated PowerSync stream configuration and server capability.
4. Release the integrated client after verifying the capability and transport.

Older servers are supported by client local staging; unsupported tracking never
blocks ordinary library uploads. Versions remain unchanged in these development
PRs. Run server tests only against a separate test database; the fixtures recreate
tables. The tracking migration test verifies existing books survive and Alembic
metadata matches the upgraded schema.

## Selected books (schema v2)

A goal with `scope: "book"` may include `book_ids`, a canonical list of selected
book UUIDs. `scope_id` remains a selected book for legacy payload compatibility.
Omitting `book_ids` retains the original single-book meaning. Every selected book
is ownership-checked; changing the selection requires a replacement goal.
Completion targets cannot exceed the number of distinct selected books.

The client omits the additive field for single-book goals and stages multiple-book
goals and their period snapshots separately on v1 servers. Ordinary tracking and
library uploads continue. Staged records promote when v2 is discovered. Deploy
the v2 server before releasing this client; use the updated client on all devices
to calculate multiple-book progress. Existing JSONB and SQLite payload columns
carry the selection; no additional database migration or stream changes are needed.
