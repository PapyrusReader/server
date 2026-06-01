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
./scripts/generate_dev_powersync_keys.sh
docker compose up -d database mailpit powersync-storage
uv run alembic upgrade head
./scripts/setup_local_powersync.sh
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
docker compose up -d powersync
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
docker compose up -d database mailpit powersync-storage
uv run alembic upgrade head
./scripts/setup_local_powersync.sh
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
npm --prefix frontend/dev-pages run dev
docker compose up -d powersync
```

Clear browser storage for `http://localhost:8080/__dev/powersync-sandbox`.
