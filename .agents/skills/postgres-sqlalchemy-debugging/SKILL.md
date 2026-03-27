---
name: postgres-sqlalchemy-debugging
description: Use when diagnosing PostgreSQL, async SQLAlchemy, or Alembic issues in this repository, including bad queries, session and transaction bugs, metadata drift, migration mismatches, and test database failures.
---

# Postgres And SQLAlchemy Debugging

1. Reproduce first. Capture the failing command, stack trace, SQL, endpoint, or pytest target.
2. Inspect the relevant layers in order: caller route or service, `papyrus/core/database.py`, schemas or models, migration state, and `tests/conftest.py` when the failure is test-only.
3. Sort the failure into one of these buckets before changing code:
   - model or schema drift versus migration drift
   - session lifecycle or transaction boundary bugs
   - async SQLAlchemy misuse
   - query-shape bugs such as joins, filters, pagination, or serialization mismatches
   - environment or configuration issues involving Docker Postgres or database URLs
4. Prefer the smallest fix that fully explains the failure.
5. Keep route handlers thin. Move query and persistence fixes into `papyrus/services` or model-level helpers where possible.
6. When the root cause is schema drift, pair this skill with `alembic-migrations`.
7. Add a regression test for any behavior change.
8. Verify with the narrowest relevant command, such as the failing pytest target or `uv run alembic upgrade head`.

Return the root cause, fix, verification, and any remaining uncertainty.
