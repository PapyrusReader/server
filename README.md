# Papyrus server

REST API server for Papyrus, a cross platform book management application.

## Getting started

Install dependencies:

```bash
uv sync --extra dev
```

Run the database:

```bash
docker compose up -d database mailpit powersync-storage powersync
```

Run database migrations:

```bash
uv run alembic upgrade head
```

Run the server:

```bash
uv run uvicorn papyrus.main:app --reload --port 8080
```

Generate local PowerSync keys for auth testing:

```bash
./scripts/generate_dev_powersync_keys.sh
```

Initialize the local PowerSync source role and publication after migrations:

```bash
./scripts/setup_local_powersync.sh
```

## Development

Run tests:

```bash
uv run pytest --cov --cov-report html
```

## Auth Testing

Local auth testing supports Mailpit for SMTP capture, a dev auth sandbox at `/__dev/auth-sandbox`, and opt-in provider smoke tests.

See [`docs/auth-testing.md`](docs/auth-testing.md) for the exact `.env` values, Google OAuth setup, and end-to-end test workflow.

For Flutter client integration guidance, see [`docs/flutter-auth-integration.md`](docs/flutter-auth-integration.md).

For the self-hosted PowerSync sandbox and sync validation workflow, see [`docs/powersync-sandbox.md`](docs/powersync-sandbox.md).
