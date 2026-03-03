# Papyrus server

REST API server for Papyrus, a cross platform book management application.

## Installation

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
uv run pytest
uv run pytest --cov
```
