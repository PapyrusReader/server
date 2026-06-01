# Papyrus Server

FastAPI backend for Papyrus authentication, metadata, file storage, and
PowerSync-backed synchronization.

## Auth And Sync

- Email/password auth uses `POST /v1/auth/register` and
  `POST /v1/auth/login`.
- Google auth starts at `GET /v1/auth/oauth/google/start` and finishes through
  `POST /v1/auth/exchange-code`.
- PowerSync credentials come from `POST /v1/auth/powersync-token`.
- PowerSync uploads use `POST /v1/sync/powersync-upload`.

Read the focused guides:

- [Flutter auth and PowerSync integration](docs/flutter-auth-integration.md)
- [Authentication testing](docs/auth-testing.md)
- [PowerSync sandbox](docs/powersync-sandbox.md)

## Local Setup

Run from `server/`:

```bash
uv sync --extra dev
./scripts/generate_dev_powersync_keys.sh
docker compose up -d database mailpit powersync-storage
uv run alembic upgrade head
./scripts/setup_local_powersync.sh
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
docker compose up -d powersync
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
```

Use `--host 0.0.0.0` so the PowerSync container reaches the JWKS endpoint at
`host.docker.internal:8080`.

## Checks

```bash
uv run pytest --cov --cov-report html
```
