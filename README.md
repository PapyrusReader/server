# Papyrus Server

FastAPI services for authentication, book metadata, media, OPDS relay and
PowerSync-backed synchronization. PostgreSQL stores account-scoped library data;
the pinned PowerSync service replicates it to client SQLite databases.

## Local setup

Run from this repository using Python 3.12 and Docker Compose:

```bash
uv sync --locked --extra dev
./scripts/bootstrap_local.sh
npm --prefix frontend/dev-pages ci
npm --prefix frontend/dev-pages run dev
```

Bootstrap creates missing development keys, starts the local databases, applies
Alembic migrations and configures PowerSync replication. Read `.env.example` and
`scripts/bootstrap_local.sh` before running it against an existing environment.
The Vite command is optional and serves the development sandbox assets.

## Auth and sync contracts

The API prefix is configured by `API_PREFIX`; `.env.example` uses `/v1`.

| Operation | Route under the configured prefix |
| --- | --- |
| Register / sign in | `POST /auth/register`, `POST /auth/login` |
| Start Google sign-in | `GET /auth/oauth/google/start` |
| Exchange the browser auth code | `POST /auth/exchange-code` |
| Refresh the session | `POST /auth/refresh` |
| Get PowerSync credentials | `POST /auth/powersync-token` |
| Upload offline changes | `POST /sync/powersync-upload` |

Contracts are implemented in [auth routes](papyrus/api/routes/auth.py),
[sync routes](papyrus/api/routes/sync.py) and
[PowerSync replication rules](powersync/sync-config.yaml). The server validates
ownership and tombstones and commits mixed upload batches atomically. Media
removal happens after commit. Client token refresh and profile-scoped persistence
remain client responsibilities.

With `DEBUG=true`, `/__dev/auth-sandbox` and `/__dev/powersync-sandbox` provide
local development tools. They are excluded from the public schema. Mailpit in
Compose captures local SMTP. See [.env.example](.env.example) and
[auth smoke tests](tests/integration/test_auth_smoke.py) for provider setup;
provider-backed tests require explicit SMTP/Google configuration.

[Managed acquisition routes](papyrus/api/routes/acquisition.py) use owner-scoped
endpoints, jobs and rules. Provider adapters, job lifecycle and release tokens
live in [the acquisition service](papyrus/services/acquisition/). The monitor
imports selected media and handles retry/cancellation. [OPDS relay setup](docs/opds-relay.md)
describes catalog transport and origin restrictions.

## Verification

Inside the Papyrus workspace, run:

```bash
../tools/papyrus check server
../tools/papyrus test server
```

The CLI excludes provider `auth_smoke` tests and checks for a separate local test
database. Pytest fixtures drop/recreate tables, including most route tests without
an `integration` marker. Direct pytest requires a distinct `*_test` database;
do not run concurrent suites on it. Read [test fixtures](tests/conftest.py) before
setting test database variables. Run provider smoke tests separately once configured.

For schema changes, run `uv run --locked alembic current` and `heads`, apply the
reviewed migrations, then verify `current` and `check`. Keep migrations separate
from structural refactors.

## Public API documentation

The docs repository renders a generated schema rather than maintaining copied
endpoint definitions. Export with deterministic documentation settings:

```bash
uv run --locked python scripts/export_openapi.py ../docs/_static/openapi.json
uv run --locked python scripts/export_openapi.py ../docs/_static/openapi.json --check
```

Export does not load `.env`, start services or connect to a database. Set the docs
workflow's server revision to the source commit used for the snapshot. The runtime
`/openapi.json` remains the specification for a configured deployment.
