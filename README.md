# Papyrus server

REST API server for Papyrus, a cross platform book management application.

## Getting started

Install dependencies:

```bash
uv sync --extra dev
```

Run the database:

```bash
docker compose up database
```

Run database migrations:

```bash
uv run alembic upgrade head
```

Run the server:

```bash
uv run uvicorn papyrus.main:app --reload
```

## Development

Run tests:

```bash
uv run pytest --cov --cov-report html
```
