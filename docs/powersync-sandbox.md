# PowerSync Sandbox

This repo includes a debug-only PowerSync sandbox that validates the full local flow:

- Papyrus authentication
- Papyrus-issued PowerSync JWTs
- self-hosted PowerSync service
- client writes through the PowerSync upload queue
- replication back into another browser client

The sandbox uses a dedicated demo table, not the real books domain.

## What Gets Created

- source table: `powersync_demo_items`
- PowerSync debug page: `/__dev/powersync-sandbox`
- source snapshot API: `/__dev/powersync-demo/items`
- upload endpoint used by the PowerSync queue: `/__dev/powersync-demo/upload`

## Required Local Configuration

Make sure `.env` includes the PowerSync values from [`.env.example`](../.env.example), especially:

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

If the API runs inside Docker instead of on the host, point `POWERSYNC_JWKS_URI` at the container-to-container API URL instead of `host.docker.internal`.

## First-Time Setup

1. Generate local PowerSync signing keys:

```bash
./scripts/generate_dev_powersync_keys.sh
```

1. Start the local dependencies:

```bash
docker compose up -d database mailpit powersync-storage
```

1. Apply migrations:

```bash
uv run alembic upgrade head
```

1. Create the PowerSync replication role and publication:

```bash
./scripts/setup_local_powersync.sh
```

1. Start the backend on the host:

```bash
uv run uvicorn papyrus.main:app --reload --port 8080
```

1. Start the PowerSync service:

```bash
docker compose up -d powersync
```

## Useful Local URLs

- API index: `http://localhost:8080/`
- PowerSync sandbox: `http://localhost:8080/__dev/powersync-sandbox`
- Client one: `http://localhost:8080/__dev/powersync-sandbox?client=one`
- Client two: `http://localhost:8080/__dev/powersync-sandbox?client=two`
- Swagger UI: `http://localhost:8080/docs`
- Mailpit: `http://localhost:8025`
- PowerSync service: `http://localhost:8081`

## Manual Validation Flow

1. Open `client=one` and `client=two` in separate tabs.
1. Register or log in as the same user in both tabs.
1. In one tab, connect PowerSync.
1. In the other tab, connect PowerSync.
1. Create a demo item in either tab.
1. Confirm the item appears in:
   - the local synced list in the creating tab
   - the server source snapshot
   - the local synced list in the second tab without a manual refresh
1. Update the item from the second tab.
1. Confirm the first tab updates automatically.
1. Delete the item from either tab.
1. Confirm it disappears from both tabs and the source snapshot.

Repeat the same flow once after signing in through Google OAuth to confirm that Papyrus-issued PowerSync credentials work for provider-authenticated users too.

## What Proves The Integration

This sandbox is working correctly when all of the following are true:

- the page can authenticate through Papyrus
- `POST /v1/auth/powersync-token` returns a valid PowerSync JWT
- local item writes are visible in the source snapshot
- the second client receives replicated changes automatically
- updates and deletes also replicate back into the other client

## Resetting Local State

If you change the PowerSync config, replication setup, or local browser database and the sandbox gets into a bad state:

1. Stop the stack:

```bash
docker compose down
```

1. Remove local Docker volumes if needed:

```bash
docker compose down -v
```

1. Clear the browser data for the sandbox page, or use a different `?client=` name.
1. Re-run:
   - `docker compose up -d database mailpit powersync-storage`
   - `uv run alembic upgrade head`
   - `./scripts/setup_local_powersync.sh`
   - `uv run uvicorn papyrus.main:app --reload --port 8080`
   - `docker compose up -d powersync`

## Notes

- The source database table is managed with Alembic.
- The PowerSync publication and replication role are not managed with Alembic; they are initialized by [`scripts/setup_local_powersync.sh`](../scripts/setup_local_powersync.sh).
- The sandbox is debug-only and should not be exposed in production mode.
