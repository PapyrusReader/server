# Project Structure Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Modernize the project's tooling, dependencies, layering, infrastructure, and test setup without implementing any business logic.

**Architecture:** FastAPI + async SQLAlchemy + PostgreSQL, self-hosted via Docker Compose. A new `models/` layer holds ORM models, a new `services/` layer holds business logic (stubs only here). Routes stay thin. Tests mirror source structure.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, uv, PyJWT, pwdlib, structlog, slowapi, polyfactory, pytest-asyncio, Docker.

**Design doc:** `docs/plans/2026-03-03-project-structure-improvements-design.md`

---

### Task 1: Migrate to uv

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`

**Step 1: Install uv**

```bash
curl -Ls https://astral.sh/uv/install.sh | sh
# or: pip install uv
```

**Step 2: Create virtual environment and sync dependencies**

```bash
cd /path/to/server
uv venv
uv sync --extra dev
```

This generates `uv.lock`. Commit both.

**Step 3: Update README install instructions**

Replace:
```bash
pip install -e ".[dev]"
```
With:
```bash
uv sync --extra dev
```

Replace run instruction:
```bash
uvicorn papyrus.main:app --reload
```
With:
```bash
uv run uvicorn papyrus.main:app --reload
```

Replace test instruction:
```bash
pytest
pytest --cov
```
With:
```bash
uv run pytest
uv run pytest --cov
```

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock README.md
git commit -m "chore: migrate to uv package manager"
```

---

### Task 2: Replace deprecated packages and update dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Update `[project.dependencies]`**

Remove `python-jose[cryptography]` and `passlib[bcrypt]`.
Add `PyJWT[crypto]>=2.10.0` and `pwdlib[bcrypt]>=0.2.0`.
Remove `httpx` from regular dependencies (it belongs in dev only).
Add `slowapi>=0.1.9` and `structlog>=25.0.0`.

Final dependencies block:
```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic[email]>=2.10.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "PyJWT[crypto]>=2.10.0",
    "pwdlib[bcrypt]>=0.2.0",
    "python-multipart>=0.0.18",
    "aiofiles>=24.1.0",
    "slowapi>=0.1.9",
    "structlog>=25.0.0",
]
```

**Step 2: Update `[project.optional-dependencies]`**

Add `polyfactory>=2.18.0`. `httpx` stays here (already present).

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
    "polyfactory>=2.18.0",
]
```

**Step 3: Sync**

```bash
uv sync --extra dev
```

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: replace python-jose/passlib with PyJWT/pwdlib, add slowapi and structlog"
```

---

### Task 3: Update pyproject.toml tooling configuration

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add ruff format config**

Add after `[tool.ruff.lint]`:
```toml
[tool.ruff.format]
line-length = 120
```

**Step 2: Update mypy overrides**

Replace the existing overrides block with:
```toml
[[tool.mypy.overrides]]
module = ["jose.*", "passlib.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["tests.*"]
ignore_errors = true
```

Remove the `jose.*` and `passlib.*` entry after Task 5 replaces those packages. Leave it for now since the code still imports them.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add ruff format config and mypy test override"
```

---

### Task 4: Fix the broken `papyrus-server` entrypoint

**Files:**
- Modify: `papyrus/main.py`
- Test: `tests/test_entrypoint.py`

**Step 1: Write the failing test**

Create `tests/test_entrypoint.py`:
```python
from papyrus.main import run

def test_run_is_callable():
    assert callable(run)
```

**Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_entrypoint.py -v
```
Expected: `ImportError: cannot import name 'run'`

**Step 3: Add `run()` to `main.py`**

Replace the `if __name__ == "__main__":` block at the bottom of `main.py` with:
```python
def run() -> None:
    import uvicorn
    uvicorn.run(
        "papyrus.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
```

**Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_entrypoint.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add papyrus/main.py tests/test_entrypoint.py
git commit -m "fix: add missing run() entrypoint"
```

---

### Task 5: Enforce required SECRET_KEY at startup

**Files:**
- Modify: `papyrus/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:
```python
import pytest
from pydantic import ValidationError

def test_rejects_default_secret_key():
    from papyrus.config import Settings
    with pytest.raises(ValidationError):
        Settings(secret_key="change-me-in-production-use-openssl-rand-hex-32")
```

**Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_config.py -v
```
Expected: FAIL — Settings accepts the placeholder without raising.

**Step 3: Add a validator to `Settings`**

In `config.py`, add a field validator:
```python
from pydantic import field_validator

class Settings(BaseSettings):
    # ... existing fields ...
    secret_key: str

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_set(cls, v: str) -> str:
        if v == "change-me-in-production-use-openssl-rand-hex-32" or len(v) < 32:
            raise ValueError("SECRET_KEY must be set to a secure random value of at least 32 characters")
        return v
```

Remove the default value from `secret_key`.

**Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_config.py -v
```
Expected: PASS

**Step 5: Run full test suite — expect some failures**

```bash
uv run pytest -v
```

Other tests that create a `Settings()` without providing a `secret_key` will now fail. Fix `conftest.py` and any other test setup to provide a valid secret key via environment variable or monkeypatch:
```python
import os
os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
```
Add this at the top of `tests/conftest.py` before any app imports.

**Step 6: Run full suite again**

```bash
uv run pytest -v
```
Expected: all previously passing tests still pass.

**Step 7: Commit**

```bash
git add papyrus/config.py tests/test_config.py tests/conftest.py
git commit -m "fix: require SECRET_KEY to be set via environment variable"
```

---

### Task 6: Tighten default CORS configuration

**Files:**
- Modify: `papyrus/config.py`

**Step 1: Change the default**

In `Settings`, update:
```python
cors_origins: list[str] = []
```

**Step 2: Update `main.py` CORS middleware**

The `CORSMiddleware` is already reading from `settings.cors_origins`. No change needed there. Confirm the middleware block reads:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 3: Commit**

```bash
git add papyrus/config.py
git commit -m "fix: default CORS origins to empty list instead of wildcard"
```

---

### Task 7: Update `security.py` for PyJWT and pwdlib

**Files:**
- Modify: `papyrus/core/security.py`
- Modify: `pyproject.toml` (remove jose mypy override after this)

**Step 1: Rewrite `security.py`**

```python
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from papyrus.config import get_settings

settings = get_settings()

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(days=settings.refresh_token_expire_days))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError:
        return None
```

**Step 2: Remove the `jose.*` and `passlib.*` mypy overrides from `pyproject.toml`**

They are no longer imported.

**Step 3: Run the tests**

```bash
uv run pytest -v
```
Expected: all passing.

**Step 4: Commit**

```bash
git add papyrus/core/security.py pyproject.toml uv.lock
git commit -m "refactor: replace python-jose/passlib with PyJWT/pwdlib"
```

---

### Task 8: Add `models/` layer

**Files:**
- Create: `papyrus/models/__init__.py`
- Modify: `papyrus/core/database.py`

**Step 1: Create `papyrus/models/__init__.py`**

```python
from papyrus.core.database import Base

__all__ = ["Base"]
```

**Step 2: Verify `Base` is still importable from `core/database.py`**

No change needed there — `Base` stays defined in `core/database.py`. The `models/` package re-exports it as the canonical import location for Alembic and future model files.

**Step 3: Commit**

```bash
git add papyrus/models/
git commit -m "chore: add models layer"
```

---

### Task 9: Add `services/` layer

**Files:**
- Create: `papyrus/services/__init__.py`

**Step 1: Create `papyrus/services/__init__.py`**

```python
```

Empty for now. Services will be added domain by domain as business logic is implemented.

**Step 2: Commit**

```bash
git add papyrus/services/
git commit -m "chore: add services layer"
```

---

### Task 10: Slim down `schemas/__init__.py`

**Files:**
- Modify: `papyrus/schemas/__init__.py`

**Step 1: Check which routes import from `papyrus.schemas` directly**

```bash
grep -r "from papyrus.schemas import" papyrus/
```

**Step 2: Update any routes importing from the top-level `papyrus.schemas`**

Change imports in route files from:
```python
from papyrus.schemas import Book, BookCreate
```
To direct submodule imports:
```python
from papyrus.schemas.book import Book, BookCreate
```

Do this for each route file that has top-level schema imports.

**Step 3: Replace `schemas/__init__.py` with an empty file**

```python
```

**Step 4: Run tests**

```bash
uv run pytest -v
```
Expected: all passing.

**Step 5: Commit**

```bash
git add papyrus/schemas/ papyrus/api/
git commit -m "refactor: remove centralized schemas re-export, import from submodules directly"
```

---

### Task 11: Set up Alembic

**Files:**
- Create: `alembic/` directory (via `alembic init`)
- Create: `alembic.ini`
- Modify: `alembic/env.py`

**Step 1: Initialize Alembic**

```bash
uv run alembic init alembic
```

This creates `alembic/` and `alembic.ini`.

**Step 2: Configure `alembic.ini`**

Set `sqlalchemy.url` to a placeholder — the actual URL will come from env:
```ini
sqlalchemy.url = driver://user:pass@localhost/dbname
```
Leave as-is; `env.py` will override it.

**Step 3: Update `alembic/env.py`**

Replace the `run_migrations_online` section to use the app's settings and models:

At the top of `env.py`, add:
```python
import asyncio
from papyrus.config import get_settings
from papyrus.models import Base

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

Replace the synchronous `run_migrations_online` with the async version. The standard async Alembic `env.py` pattern is documented at [alembic.sqlalchemy.org](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).

Full async `env.py`:
```python
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from papyrus.config import get_settings
from papyrus.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 4: Commit**

```bash
git add alembic/ alembic.ini
git commit -m "chore: set up Alembic with async PostgreSQL support"
```

---

### Task 12: Add structlog configuration

**Files:**
- Modify: `papyrus/main.py`

**Step 1: Configure structlog in `create_app()`**

Add at the top of `main.py`:
```python
import structlog
```

Add this block before `create_app()`:
```python
def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

Add `import logging` at the top.

Call `configure_logging()` at the start of `create_app()`.

**Step 2: Run tests**

```bash
uv run pytest -v
```

**Step 3: Commit**

```bash
git add papyrus/main.py
git commit -m "feat: add structlog structured logging"
```

---

### Task 13: Wire slowapi rate limiting

**Files:**
- Modify: `papyrus/main.py`

**Step 1: Add slowapi middleware to `create_app()`**

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)

def create_app() -> FastAPI:
    app = FastAPI(...)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # ... rest of setup
```

Rate limit decorators will be added to individual routes when business logic is implemented. This task only wires the middleware.

**Step 2: Run tests**

```bash
uv run pytest -v
```

**Step 3: Commit**

```bash
git add papyrus/main.py
git commit -m "feat: wire slowapi rate limiting middleware"
```

---

### Task 14: Add `.env.example`

**Files:**
- Create: `.env.example`

**Step 1: Create `.env.example`**

```bash
# Application
DEBUG=false

# Server
HOST=0.0.0.0
PORT=8080
API_PREFIX=/v1

# CORS — comma-separated list of allowed origins
CORS_ORIGINS=["http://localhost:3000"]

# Database
DATABASE_URL=postgresql+asyncpg://papyrus:papyrus@localhost:5432/papyrus

# Security — generate with: openssl rand -hex 32
SECRET_KEY=
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30

# Rate Limiting (requests per minute)
RATE_LIMIT_AUTH=5
RATE_LIMIT_GENERAL=100
RATE_LIMIT_UPLOAD=10
RATE_LIMIT_BATCH=20
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add .env.example"
```

---

### Task 15: Add Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY papyrus/ ./papyrus/
COPY alembic/ ./alembic/
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
CMD ["papyrus-server"]
```

**Step 2: Create `.dockerignore`**

```
.venv/
__pycache__/
*.pyc
.env
.env.*
!.env.example
.git/
.mypy_cache/
.ruff_cache/
.pytest_cache/
htmlcov/
docs/
tests/
*.db
*.sqlite3
```

**Step 3: Build to verify**

```bash
docker build -t papyrus-server .
```
Expected: build succeeds.

**Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "chore: add multi-stage Dockerfile"
```

---

### Task 16: Add `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

**Step 1: Create `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: papyrus
      POSTGRES_PASSWORD: papyrus
      POSTGRES_DB: papyrus
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U papyrus"]
      interval: 5s
      timeout: 5s
      retries: 5

  server:
    build: .
    restart: unless-stopped
    ports:
      - "${PORT:-8080}:8080"
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://papyrus:papyrus@db:5432/papyrus
    depends_on:
      db:
        condition: service_healthy
    command: >
      sh -c "alembic upgrade head && papyrus-server"

volumes:
  postgres_data:
```

**Step 2: Verify it starts**

```bash
cp .env.example .env
# Edit .env to set SECRET_KEY
docker compose up --build
```
Expected: server starts and responds at `http://localhost:8080/health`.

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: add Docker Compose for self-hosted deployment"
```

---

### Task 17: Add pre-commit configuration

**Files:**
- Create: `.pre-commit-config.yaml`

**Step 1: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic[mypy]
          - types-aiofiles
```

**Step 2: Install and run against all files**

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Fix any issues reported.

**Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: add pre-commit hooks for ruff and mypy"
```

---

### Task 18: Add GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          version: latest

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Lint
        run: uv run ruff check .

      - name: Format
        run: uv run ruff format --check .

      - name: Type check
        run: uv run mypy papyrus/

      - name: Test
        run: uv run pytest --cov=papyrus --cov-report=term-missing
```

**Step 2: Commit**

```bash
git add .github/
git commit -m "ci: add GitHub Actions workflow"
```

---

### Task 19: Reorganize tests to mirror source structure

**Files:**
- Create: `tests/api/routes/__init__.py`
- Create: `tests/api/__init__.py`
- Create: `tests/services/__init__.py`
- Move: all `tests/test_*.py` files to `tests/api/routes/`

**Step 1: Create directory structure**

```bash
mkdir -p tests/api/routes tests/services
touch tests/api/__init__.py tests/api/routes/__init__.py tests/services/__init__.py
```

**Step 2: Move existing route tests**

```bash
mv tests/test_annotations.py tests/api/routes/
mv tests/test_auth.py tests/api/routes/
mv tests/test_bookmarks.py tests/api/routes/
mv tests/test_books.py tests/api/routes/
mv tests/test_goals.py tests/api/routes/
mv tests/test_health.py tests/api/routes/
mv tests/test_notes.py tests/api/routes/
mv tests/test_progress.py tests/api/routes/
mv tests/test_reading_profiles.py tests/api/routes/
mv tests/test_saved_filters.py tests/api/routes/
mv tests/test_series.py tests/api/routes/
mv tests/test_shelves.py tests/api/routes/
mv tests/test_storage.py tests/api/routes/
mv tests/test_sync.py tests/api/routes/
mv tests/test_tags.py tests/api/routes/
mv tests/test_users.py tests/api/routes/
```

Leave `tests/test_entrypoint.py` and `tests/test_config.py` at the top level — they test app-level concerns, not routes.

**Step 3: Run tests**

```bash
uv run pytest -v
```
Expected: all passing.

**Step 4: Commit**

```bash
git add tests/
git commit -m "refactor: reorganize tests to mirror source structure"
```

---

### Task 20: Switch tests to `AsyncClient`

**Files:**
- Modify: `tests/conftest.py`
- Modify: All test files in `tests/api/routes/`

**Step 1: Update `conftest.py`**

Replace `TestClient` with `AsyncClient`:
```python
import os
os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from papyrus.core.security import create_access_token
from papyrus.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def user_id() -> str:
    return str(uuid4())


@pytest.fixture
def auth_headers(user_id: str) -> dict[str, str]:
    token = create_access_token({"sub": user_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def book_id() -> str:
    return str(uuid4())


@pytest.fixture
def shelf_id() -> str:
    return str(uuid4())


@pytest.fixture
def tag_id() -> str:
    return str(uuid4())


@pytest.fixture
def series_id() -> str:
    return str(uuid4())


@pytest.fixture
def annotation_id() -> str:
    return str(uuid4())


@pytest.fixture
def note_id() -> str:
    return str(uuid4())


@pytest.fixture
def bookmark_id() -> str:
    return str(uuid4())


@pytest.fixture
def goal_id() -> str:
    return str(uuid4())
```

**Step 2: Update all route test files**

Each test that uses `client` must become `async def` and `await` all HTTP calls:

Before:
```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
```

After:
```python
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
```

Apply this pattern to every test function in `tests/api/routes/`.

**Step 3: Run tests**

```bash
uv run pytest -v
```
Expected: all passing.

**Step 4: Commit**

```bash
git add tests/
git commit -m "refactor: switch tests from TestClient to AsyncClient"
```

---

### Task 21: Add test database fixture

**Files:**
- Modify: `tests/conftest.py`

This fixture is not needed until services and models exist, but the pattern should be established now so it's ready.

**Step 1: Add a `db_session` fixture to `conftest.py`**

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://papyrus:papyrus@localhost:5432/papyrus_test"
)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_maker() as session:
        async with session.begin():
            yield session
            await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

Add `from papyrus.models import Base` to `conftest.py` imports.

**Step 2: Run tests**

```bash
uv run pytest -v
```
Expected: all passing (the `db_session` fixture is defined but unused — that is fine).

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add async transaction-scoped db_session fixture"
```

---

### Task 22: Add polyfactory

No code to write — polyfactory is already added to dev dependencies in Task 2. This task documents the intended usage pattern for future test authors.

**Step 1: Create `tests/factories.py`**

```python
from polyfactory.factories.pydantic_factory import ModelFactory

from papyrus.schemas.book import BookCreate
from papyrus.schemas.shelf import CreateShelfRequest


class BookCreateFactory(ModelFactory[BookCreate]):
    __model__ = BookCreate


class CreateShelfRequestFactory(ModelFactory[CreateShelfRequest]):
    __model__ = CreateShelfRequest
```

Add factories here as schemas are developed. Use in tests like:
```python
from tests.factories import BookCreateFactory

async def test_create_book(client, auth_headers):
    payload = BookCreateFactory.build()
    response = await client.post("/v1/books", json=payload.model_dump(), headers=auth_headers)
    assert response.status_code == 201
```

**Step 2: Commit**

```bash
git add tests/factories.py
git commit -m "test: add polyfactory scaffold for test data generation"
```

---

## Summary

| # | Task | Type |
|---|------|------|
| 1 | Migrate to uv | Tooling |
| 2 | Replace deprecated packages | Dependencies |
| 3 | Update pyproject.toml tooling config | Tooling |
| 4 | Fix `run()` entrypoint | Bug fix |
| 5 | Enforce SECRET_KEY at startup | Security |
| 6 | Tighten default CORS | Security |
| 7 | Update security.py for PyJWT/pwdlib | Refactor |
| 8 | Add `models/` layer | Structure |
| 9 | Add `services/` layer | Structure |
| 10 | Slim down `schemas/__init__.py` | Refactor |
| 11 | Set up Alembic | Infrastructure |
| 12 | Add structlog | Observability |
| 13 | Wire slowapi | Infrastructure |
| 14 | Add `.env.example` | Documentation |
| 15 | Add Dockerfile | Infrastructure |
| 16 | Add docker-compose.yml | Infrastructure |
| 17 | Add pre-commit config | Tooling |
| 18 | Add GitHub Actions CI | CI |
| 19 | Reorganize tests | Structure |
| 20 | Switch tests to AsyncClient | Testing |
| 21 | Add test DB fixture | Testing |
| 22 | Add polyfactory | Testing |
