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
./scripts/bootstrap_local.sh
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
```

The bootstrap is idempotent: it creates development keys when missing, starts
the databases, applies Alembic migrations, configures logical replication, and
starts the healthy server and pinned PowerSync services.

## Checks

```bash
uv run pytest --cov --cov-report html
```
