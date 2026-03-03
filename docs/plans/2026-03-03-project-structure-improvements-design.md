# Project Structure Improvements Design

## Context

Papyrus Server is a self-hosted REST API for the Papyrus cross-platform Flutter book management app. It is an optional component providing book file management, metadata management, and app state sync. Authentication is handled locally with email/password and JWT. The database is standalone PostgreSQL. Google OAuth is handled separately and out of scope here.

## Section 1: Dependencies & Package Management

- Replace `python-jose` with `PyJWT` — `python-jose` has known CVEs and is unmaintained
- Replace `passlib` with `pwdlib` (bcrypt backend) or `bcrypt` directly — `passlib` is in maintenance mode
- Migrate from `pip` to `uv` — faster installs, produces a lockfile (`uv.lock`)
- Move `httpx` out of regular dependencies into dev-only — it is only used for testing
- Add `slowapi` for rate limiting — currently configured in settings but never wired
- Add `structlog` for structured JSON logging — no logging is currently configured

## Section 2: Project Structure & Layering

Add two missing architectural layers:

```
papyrus/
├── api/
│   ├── deps.py
│   └── routes/
├── config.py
├── core/
│   ├── database.py
│   ├── exceptions.py
│   └── security.py
├── models/          ← new: SQLAlchemy ORM models
├── schemas/         ← existing: Pydantic request/response models
├── services/        ← new: business logic
└── main.py
```

**`papyrus/models/`** — SQLAlchemy ORM models, one file per domain entity. Required before Alembic migrations can be written.

**`papyrus/services/`** — business logic layer between routes and database. Route handlers become thin: validate input, call a service, return a response. Database interactions and cross-entity logic live here.

**Slim down `schemas/__init__.py`** — the current 207-line re-export file pulls all schemas into a single flat namespace. Remove it or trim it to nothing; import directly from submodules instead.

**Test structure mirrors source:**
```
tests/
├── api/
│   └── routes/
│       ├── test_books.py
│       └── ...
├── services/
│   └── test_book_service.py
└── conftest.py
```

## Section 3: Infrastructure

**Docker Compose + Dockerfile** — a `docker-compose.yml` that starts the server and PostgreSQL together, a multi-stage `Dockerfile` for a lean runtime image, and a `.dockerignore`.

**Alembic setup** — initialize `alembic/` directory and `alembic.ini`. Wire `alembic/env.py` to import `Base` from `papyrus/models/` and read `DATABASE_URL` from app settings.

**Pre-commit hooks** — `.pre-commit-config.yaml` running `ruff` (lint + format) and `mypy` on commit.

**GitHub Actions CI** — single workflow on push/PR: install with `uv`, run `ruff`, `mypy`, `pytest`.

**`.env.example`** — documents every required environment variable for self-hosted users.

## Section 4: Configuration & Tooling

- Add a `run()` function to `main.py` — `pyproject.toml` declares `papyrus-server = "papyrus.main:run"` but no such function exists
- Add `ruff format` config to `pyproject.toml` — currently only lint is configured; line length should match the lint setting (120)
- Tighten default CORS — `cors_origins` defaults to `["*"]`; make the default restrictive and mark it clearly in `.env.example`
- Remove the hardcoded `secret_key` default — the server should refuse to start if `SECRET_KEY` is not set via environment variable
- Add `tests/` to mypy overrides with relaxed strictness — keeps strict mode on application code without blocking CI on test files

## Section 5: Testing

- Switch from `TestClient` to `httpx.AsyncClient` with `pytest-asyncio` — tests the actual async code paths
- Add a test database fixture — wraps each test in a transaction rolled back after, keeping tests isolated and fast
- Add `polyfactory` for test data generation — generates valid Pydantic model instances automatically, reducing per-test boilerplate
