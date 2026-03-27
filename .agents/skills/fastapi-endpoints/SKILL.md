---
name: fastapi-endpoints
description: Use when adding or modifying FastAPI endpoints in this repository, including route handlers, schema wiring, router registration, service-layer delegation, and endpoint tests. Pair with the migration skill when persisted schema changes are involved.
---

# FastAPI Endpoints

1. Inspect the relevant route module in `papyrus/api/routes/`, the matching schemas in `papyrus/schemas/`, any existing service module in `papyrus/services/`, and the nearest tests in `tests/api/routes/`.
2. Keep handlers thin. They should handle dependency injection, request parsing, service calls, error translation, and response shaping only.
3. Put business rules, query orchestration, and transaction-aware logic in `papyrus/services/<domain>.py`.
4. Reuse or add Pydantic schemas for request and response models. Keep explicit return types.
5. If you add a new router module, register it in `papyrus/api/routes/__init__.py`.
6. Add or update route tests for the changed behavior. Add service tests when logic moves into `papyrus/services/`.
7. If the endpoint needs a schema change, pair this skill with `alembic-migrations`.
8. Before finishing, run the narrowest relevant checks: `uv run pytest ...`, `uv run ruff check ...`, and the current typecheck command for the touched scope.

Return a short summary that names the touched routes, services, tests, and any follow-up risk.
