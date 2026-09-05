# PowerSync Sandbox

Use this runbook to validate local Papyrus auth, PowerSync JWT minting, upload
handling, and two-client replication.

The sandbox writes to `powersync_demo_items`.

## Setup

Set the PowerSync values in `.env`:

```dotenv
POWERSYNC_JWT_PRIVATE_KEY_FILE=.local/powersync/private.pem
POWERSYNC_JWT_PUBLIC_KEY_FILE=.local/powersync/public.pem
POWERSYNC_JWT_KEY_ID=papyrus-powersync-dev
POWERSYNC_JWT_AUDIENCE=powersync-dev
POWERSYNC_SERVICE_URL=http://localhost:8081
POWERSYNC_JWKS_URI=http://host.docker.internal:8080/v1/auth/jwks
POWERSYNC_SOURCE_ROLE=powersync_role
POWERSYNC_SOURCE_PASSWORD=powersync_dev_password
POWERSYNC_STORAGE_DB=powersync_storage
POWERSYNC_STORAGE_USER=powersync_storage_user
POWERSYNC_STORAGE_PASSWORD=powersync_storage_password
```

Run from `server/`:

```bash
uv sync --extra dev
./scripts/bootstrap_local.sh
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
```

Use `--host 0.0.0.0` so the PowerSync container reaches the JWKS endpoint at
`host.docker.internal:8080`.

## URLs

- Sandbox: `http://localhost:8080/__dev/powersync-sandbox`
- Client one: `http://localhost:8080/__dev/powersync-sandbox?client=one`
- Client two: `http://localhost:8080/__dev/powersync-sandbox?client=two`
- Source snapshot: `http://localhost:8080/__dev/powersync-demo/items`
- PowerSync service: `http://localhost:8081`
- Mailpit inbox: `http://localhost:8025`

## Validation

1. Open `client=one` and `client=two` in separate tabs.
2. Register or log in as the same user in both tabs.
3. Connect PowerSync in both tabs.
4. Create a demo item in `client=one`.
5. Confirm the item appears in `client=one`, the source snapshot, and
   `client=two`.
6. Update the item in `client=two`.
7. Confirm the updated item appears in `client=one`.
8. Delete the item.
9. Confirm the item disappears from both clients and the source snapshot.

Passing validation proves:

- Papyrus login works.
- `POST /v1/auth/powersync-token` returns a usable PowerSync JWT.
- PowerSync uploads reach Postgres.
- Replication delivers committed changes to another client.

## Reset

Run from `server/`:

```bash
docker compose down -v
./scripts/bootstrap_local.sh
npm --prefix frontend/dev-pages run dev
```

Clear browser storage for `http://localhost:8080/__dev/powersync-sandbox`.

## Library sync

The production Flutter library uses `POST /v1/sync/powersync-upload`. Its automatic,
owner-filtered streams contain `books`, `shelves`, `tags`, `notes`, `annotations`,
`book_shelves`, and `book_tags`. The demo stream and sandbox remain available.
The other library REST routers are placeholders; use PowerSync uploads for these
persisted domains.

Apply revision `dcd3b384e6a4` before starting an API or PowerSync version that uses
these streams, then refresh the source publication and restart PowerSync:

```bash
uv run alembic upgrade head
./scripts/setup_local_powersync.sh
docker compose restart powersync
```

Roll out the server migration and upload handlers first, refresh the publication,
then activate the expanded sync configuration before updating the client. A
running container alone does not prove replication is ready. Inspect startup
logs for activation and subsequent checkpoint progress:

```bash
docker compose logs --since 5m powersync
```

Confirm the new sync configuration becomes active, then make a disposable test
write and confirm a later replication checkpoint and delivery to another client.
`PSYNC_S2302: No sync config available` indicates that configuration has not
become active; inspect replication/configuration errors before changing keys.
This upgrade requires no key regeneration or database reset. The reset procedure
above is destructive and is only for intentionally discarding a local sandbox.

The additive migration promotes legacy book metadata to columns and leaves the
original `custom_metadata` envelope intact. Invalid historical field values stay
in that envelope and do not abort the backfill. PostgreSQL 17 is the supported
local baseline. Downgrading removes the new library tables, tombstones, and
promoted columns; back up library data before a downgrade.

Each upload retains the existing transaction envelope:

```json
{
  "batch": [
    {
      "type": "shelves",
      "op": "PUT",
      "id": "00c7dcac-8fc2-40d7-8558-9b5c55b20f25",
      "data": {"name": "Reading", "sort_order": 0}
    }
  ]
}
```

Entity IDs are UUIDs. Membership IDs are canonical lowercase
`<book_uuid>:<shelf_uuid>` or `<book_uuid>:<tag_uuid>` strings; reference fields must
match that pair. Book-shelf memberships carry `added_at` and `sort_order`;
book-tag memberships carry `created_at`. Membership removal is a hard delete,
and adding the pair again is supported.

Payloads use snake_case. Shelf icons carry `icon_code_point`,
`icon_font_family`, `icon_font_package`, and `icon_match_text_direction`.
Notes and annotations use a `location` object containing `page_number`, optional
`chapter`, optional `chapter_title`, and optional `percentage`. A note location
can be null. Annotation colors are `yellow`, `green`, `blue`, `pink`, `purple`,
and `orange`. Note tags remain a list of free-text strings.

Book columns additionally contain `publication_date`, `file_format`, `file_size`,
`file_hash`, `is_physical`, `physical_location`, `lent_to`, `lent_at`, `series_id`,
`series_name`, `series_number`, `started_at`, `completed_at`, and `last_read_at`.
`series_id` is a text descriptor, not a foreign key. New clients retain user
metadata as `{"custom_metadata": {"custom_metadata": {"key": "value"}}}` in upload
data. Legacy queued envelopes are also accepted, with explicitly supplied
promoted fields taking precedence. Local file paths are not part of the contract;
media references use the existing media upload and download endpoints.

The server derives ownership from authentication and controls `updated_at`.
Timestamps are stored as UTC instants; uploads accept ISO 8601 strings and treat
legacy timestamps without an offset as UTC. PUT upserts only supplied fields;
PATCH updates only supplied fields and accepts explicit null for nullable fields.
A PATCH for an absent entity is acknowledged without creating a placeholder.
Unknown fields, invalid values, missing live references, foreign references, and
shelf cycles reject the transaction; all earlier mutations in that batch roll
back. Transactions for one owner serialize to preserve unrelated concurrent
field changes.

Entity deletion wins over stale offline writes through durable, server-only
`sync_tombstones`. Deleting a book removes its notes, annotations, memberships,
and existing media; physical files are removed only after commit. Deleting a
shelf reparents its immediate children to the root and removes its memberships.
Deleting a tag removes its memberships. Delayed entity writes and writes with
a tombstoned parent are acknowledged as no-ops so a device can drain its queue.
Do not purge tombstones while offline clients may still upload old changes.

For two-client library validation, use the same account in two independent
Flutter browser profiles. Create a shelf, tag, note, annotation, and memberships
on one client, and confirm all appear on the other. Disconnect one client, edit
an unrelated field on each client, reconnect, and verify both changes survive.
Repeat with deletion on the connected client and a stale edit on the offline
client; the deleted entity must stay absent. A separate account must never see
or modify the first account's library.

The automated live check creates two independent native PowerSync databases for
one disposable account and a third database for another account. It exercises
all synchronized domains, queued writes across restart, different-field merges,
server-order conflicts, null clearing, and deletion against stale offline edits.
It removes its domain records and disables its disposable accounts afterward.
With the local API on port 8080 and PowerSync running, execute from `client/app/`:

```bash
PAPYRUS_LIVE_SYNC=1 flutter test test/powersync/library_live_sync_test.dart --reporter expanded
```

Client schema expansion preserves existing book databases and queued uploads.
A one-time local migration promotes only compatible legacy metadata values;
explicit column nulls remain cleared. Guest tables are local-only, and switching
account or server invalidates the old repository handles and clears library
views before loading the selected database. Previously memory-only shelves,
topics, notes, and annotations are not automatically assigned to any account.
