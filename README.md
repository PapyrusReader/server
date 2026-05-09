# Papyrus server

REST API server for Papyrus, a cross platform book management application.

## Getting started

Install dependencies:

```bash
uv sync --extra dev
```

Run the database:

```bash
docker compose up database mailpit powersync-storage powersync
```

Run database migrations:

```bash
uv run alembic upgrade head
```

Run the server:

```bash
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
```

The `--host 0.0.0.0` flag is required for the Dockerized PowerSync service to
reach the backend JWKS endpoint through `host.docker.internal`.

Run the dev-pages asset server with live TS/SCSS reload:

```bash
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
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
