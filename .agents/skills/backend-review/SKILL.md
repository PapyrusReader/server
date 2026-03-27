---
name: backend-review
description: Use when reviewing FastAPI and Postgres backend changes in this repository for bugs, regressions, migration risk, SQLAlchemy issues, API contract drift, and missing tests. Do not use for feature implementation unless the user explicitly asked for review-plus-fix work.
---

# Backend Review

1. Default to a code review mindset. Prioritize correctness, security, behavior regressions, data integrity, migration safety, and missing tests.
2. Inspect changed routes, services, schemas, models, migrations, and matching tests together.
3. Check especially for:
   - business logic left in route handlers instead of `papyrus/services`
   - behavior changes without test coverage
   - schema changes without Alembic revisions
   - migration changes without downgrade or verification
   - async SQLAlchemy session, transaction, or query bugs
   - auth, validation, or serialization drift
4. Report findings first with file references and concrete impact.
5. Avoid style-only comments unless they hide a real correctness or maintainability problem.
6. If no findings remain, say so explicitly and mention any residual testing or verification gaps.

Return findings ordered by severity, then open questions or assumptions, then a short change summary.
