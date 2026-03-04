# Automated Test Database Setup

## Problem

Running integration tests requires manually creating the `papyrus_test` database and `papyrus` role inside the running postgres container before `pytest` can be invoked.

## Decision

Add a session-scoped autouse fixture to `conftest.py` that provisions the test database automatically on every `pytest` run. Assumes docker-compose is already running.

## Design

### `setup_test_db` fixture

- Scope: `session`, `autouse=True`
- Reads superuser credentials from env vars: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
- Connects directly to the `postgres` maintenance database via `asyncpg` (raw connection, not SQLAlchemy — `CREATE DATABASE` cannot run inside a transaction)
- Creates the `papyrus` role if it does not exist
- Creates the `papyrus_test` database if it does not exist
- Grants all privileges on `papyrus_test` to `papyrus`

### `db_session` fixture

- Declares `setup_test_db` as a dependency to guarantee ordering
- No other changes

### `TEST_DATABASE_URL`

- Replace the hardcoded `postgresql+asyncpg://papyrus:papyrus@localhost:5432/papyrus_test` with a value derived from the same env vars used by `setup_test_db`
- Keeps credentials in one place (`.env`)

## Constraints

- No new dependencies — `asyncpg` is already a project dependency
- Docker Compose must be running before `pytest` is invoked
