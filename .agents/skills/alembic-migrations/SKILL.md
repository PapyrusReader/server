---
name: alembic-migrations
description: Use when changing PostgreSQL schema or persisted data in this repository, including SQLAlchemy model updates, Alembic revisions, autogeneration review, and migration verification. Pair with endpoint or service work when application behavior also changes.
---

# Alembic Migrations

1. Inspect `alembic/env.py`, `papyrus/models`, and the code that depends on the schema change before generating a revision.
2. Update SQLAlchemy models first. Ensure new or changed models are imported from `papyrus.models` so `Base.metadata` is visible to Alembic.
3. Generate a revision with `uv run alembic revision --autogenerate -m "<message>"`, then review the generated upgrade and downgrade manually.
4. Do not trust autogenerate blindly. Confirm column types, defaults, nullability, indexes, foreign keys, and constraint names.
5. Keep revisions small, deterministic, and reversible when practical.
6. For destructive changes or data backfills, require explicit user approval and document the assumptions inline in the migration.
7. Apply the migration with `uv run alembic upgrade head` and run the narrowest relevant tests.
8. If the schema changed, verify the consuming application code and tests changed in the same work.

Return the revision id, what changed, how it was verified, and any rollback caveats.
