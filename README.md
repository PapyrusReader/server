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
