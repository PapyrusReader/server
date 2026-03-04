# Automated Test Database Setup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a session-scoped autouse pytest fixture that automatically creates the `papyrus_test` database and `papyrus` role so `pytest` works without any manual setup.

**Architecture:** A `setup_test_db` fixture in `conftest.py` connects to the `postgres` maintenance database as superuser via a raw `asyncpg` connection, creates the role and database if absent, then grants privileges. The `db_session` fixture declares it as a dependency to guarantee ordering. `TEST_DATABASE_URL` is derived from the same env vars used by `setup_test_db` instead of being hardcoded.

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode = "auto"`), asyncpg (already a project dependency)

---

### Task 1: Derive `TEST_DATABASE_URL` from env vars

**Files:**
- Modify: `tests/conftest.py:79-82`

The current hardcoded URL:
```python
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://papyrus:papyrus@localhost:5432/papyrus_test",
)
```

Replace with one derived from the env vars already set at the top of `conftest.py`:

**Step 1: Update `TEST_DATABASE_URL` in `conftest.py`**

Replace lines 79-82 with:
```python
_pg_host = os.environ.get("POSTGRES_HOST", "localhost")
_pg_port = os.environ.get("POSTGRES_PORT", "5432")

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://papyrus:papyrus@{_pg_host}:{_pg_port}/papyrus_test",
)
```

**Step 2: Run the existing integration test to verify nothing broke**

```bash
pytest tests/test_database.py -v
```
Expected: `1 passed`

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: derive TEST_DATABASE_URL from env vars"
```

---

### Task 2: Add `setup_test_db` autouse fixture

**Files:**
- Modify: `tests/conftest.py`

This fixture:
- Reads superuser credentials from env vars (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`)
- Opens a raw `asyncpg` connection to the `postgres` maintenance database (not SQLAlchemy — `CREATE DATABASE` cannot run inside a transaction)
- Creates the `papyrus` role if absent
- Creates `papyrus_test` database if absent
- Grants all privileges on `papyrus_test` to `papyrus`

**Step 1: Write the failing test**

Add to `tests/test_database.py`:
```python
@pytest.mark.integration
async def test_setup_creates_database_idempotently(db_session: AsyncSession):
    """Running setup twice should not raise errors (idempotent)."""
    # If setup_test_db is idempotent, a second call via the fixture won't raise.
    # We verify indirectly: if db_session works, setup succeeded at least once.
    result = await db_session.execute(text("SELECT current_database()"))
    assert result.scalar() == "papyrus_test"
```

**Step 2: Run to verify it fails (no `setup_test_db` fixture yet)**

```bash
pytest tests/test_database.py::test_setup_creates_database_idempotently -v
```
Expected: FAIL — `db_session` cannot connect because the test DB may not exist in a fresh environment, or the assertion fails.

> Note: if your DB already exists from manual setup earlier, this test may pass. That's fine — proceed to implement the fixture anyway; the real validation is dropping and recreating the DB manually to confirm automation works.

**Step 3: Add `setup_test_db` fixture to `conftest.py`**

Add these imports at the top of `tests/conftest.py` (after existing imports):
```python
import asyncpg
```

Add this fixture before the `db_session` fixture:
```python
@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db() -> None:
    """Create the test database and role if they do not exist."""
    pg_user = os.environ.get("POSTGRES_USER", "postgres")
    pg_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    pg_host = os.environ.get("POSTGRES_HOST", "localhost")
    pg_port = int(os.environ.get("POSTGRES_PORT", "5432"))

    conn = await asyncpg.connect(
        user=pg_user,
        password=pg_password,
        host=pg_host,
        port=pg_port,
        database="postgres",
    )
    try:
        await conn.execute(
            "CREATE ROLE papyrus WITH LOGIN PASSWORD 'papyrus'"
        )
    except asyncpg.DuplicateObjectError:
        pass  # Role already exists

    try:
        await conn.execute("CREATE DATABASE papyrus_test OWNER papyrus")
    except asyncpg.DuplicateDatabaseError:
        pass  # Database already exists

    await conn.execute("GRANT ALL PRIVILEGES ON DATABASE papyrus_test TO papyrus")
    await conn.close()
```

**Step 4: Add `setup_test_db` as a dependency of `db_session`**

Update the `db_session` fixture signature from:
```python
async def db_session() -> AsyncGenerator[AsyncSession, None]:
```
to:
```python
async def db_session(setup_test_db: None) -> AsyncGenerator[AsyncSession, None]:
```

**Step 5: Run all integration tests**

```bash
pytest tests/test_database.py -v
```
Expected: `2 passed`

**Step 6: Commit**

```bash
git add tests/conftest.py tests/test_database.py
git commit -m "test: automate test database provisioning with session fixture"
```

---

### Task 3: Validate end-to-end automation

Verify that starting from a state with no `papyrus_test` database, `pytest` creates it automatically.

**Step 1: Drop the test database and role**

```bash
docker compose exec database psql -U postgres -c "DROP DATABASE IF EXISTS papyrus_test;"
docker compose exec database psql -U postgres -c "DROP ROLE IF EXISTS papyrus;"
```

**Step 2: Run pytest with no manual setup**

```bash
pytest tests/test_database.py -v
```
Expected: `2 passed` — pytest created the database and role automatically.

**Step 3: Run again (idempotency check)**

```bash
pytest tests/test_database.py -v
```
Expected: `2 passed` — no errors from duplicate create attempts.
