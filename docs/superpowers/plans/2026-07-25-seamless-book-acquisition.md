# Seamless Book Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users search torrent indexers from Books, submit one or many releases to qBittorrent, monitor them while clients are closed, and atomically turn completed downloads into ordinary Papyrus books.

**Architecture:** FastAPI owns credentials, release tokens, jobs, qBittorrent polling, path validation, and media import. PowerSync continues to synchronize books while authenticated REST supplies short-lived search results and changing acquisition progress. Flutter merges REST jobs with synchronized books by `book_id` and reuses the application's existing library, bottom-sheet, and destructive-dialog components.

**Tech Stack:** FastAPI, Pydantic, async SQLAlchemy, PostgreSQL, Alembic, pytest, urllib, Flutter, Dart, Provider, PowerSync, package:http, flutter_test.

---

## Repository map

Server checkout: `/home/karolis/Documents/Projects/Papyrus/server`

- `papyrus/models/acquisition.py`: endpoint download roots and the complete persisted job lifecycle.
- `papyrus/schemas/acquisition.py`: secure release tokens, batch outcomes, progress, and job actions.
- `papyrus/api/routes/acquisition.py`: authenticated thin route wiring.
- `papyrus/services/acquisition.py`: endpoint ownership, secure tokens, submission orchestration, and qBittorrent adapter operations.
- `papyrus/services/acquisition_monitor.py`: lease-safe polling and lifecycle transitions for user-submitted jobs only.
- `papyrus/services/media.py`: reusable path-based atomic import.
- `papyrus/config.py` and `papyrus/main.py`: import-root configuration and monitor lifespan.
- `alembic/versions/`: one new reversible acquisition lifecycle revision.
- `tests/services/` and `tests/api/routes/`: service, adapter, import, route, ownership, and migration contract tests.

Client checkout: `/home/karolis/Documents/Projects/Papyrus/client`

- `app/lib/acquisition/acquisition_models.dart`: release tokens, batch results, job state, progress, and candidates.
- `app/lib/acquisition/acquisition_api_client.dart`: typed search, batch, polling, selection, retry, cancel, and removal calls.
- `app/lib/providers/acquisition_downloads_provider.dart`: foreground polling, optimistic jobs, reconciliation, and download selection.
- `app/lib/pages/library_page.dart`: local/remote search mode and download-specific actions.
- `app/lib/widgets/library/`: remote result rows, progress treatments, Downloads filter, and pending states.
- `app/lib/pages/acquisition_page.dart`: integration management only.
- `app/test/`: model, API, provider, widget, polling, and regression tests.

### Task 1: Persist the managed-download contract

**Files:**
- Modify: `papyrus/models/acquisition.py`
- Modify: `papyrus/models/__init__.py`
- Modify: `papyrus/config.py`
- Modify: `.env.example`
- Create: `alembic/versions/<revision>_add_managed_acquisition_jobs.py`
- Modify: `tests/test_models.py`
- Create: `tests/test_acquisition_migration.py`

- [x] **Step 1: Write failing model tests**

Assert that qBittorrent endpoints expose a nullable `download_root`, and that jobs expose `book_id`, client hash/state, progress basis points, byte/speed/ETA fields, selected relative path, retry/error fields, `next_poll_at`, `lease_owner`, `lease_until`, and lifecycle timestamps. Assert indexes exist for `(owner_user_id, status)` and `next_poll_at`.

- [x] **Step 2: Verify the model tests fail**

Run: `uv run pytest tests/test_models.py -q`

Expected: failures report missing managed-acquisition columns and indexes.

- [x] **Step 3: Add the model and configuration contract**

Use the exact public state values:

```python
ACQUISITION_JOB_STATUSES = (
    "queued",
    "submitted",
    "downloading",
    "needs_file_selection",
    "importing",
    "completed",
    "failed",
    "cancelled",
)
```

Add `acquisition_import_root: str | None`, `acquisition_monitor_active_interval_seconds: float = 2`, `acquisition_monitor_idle_interval_seconds: float = 10`, and `acquisition_monitor_lease_seconds: int = 30`. Keep `download_url` nullable for legacy rows and do not persist it for new managed submissions.

- [x] **Step 4: Generate and review a reversible migration**

Run: `uv run alembic revision --autogenerate -m "add managed acquisition jobs"`

Review column types, nullability, named foreign keys, server defaults, indexes, and downgrade order. The migration must add new columns and constraints without rewriting or deleting legacy rows.

- [x] **Step 5: Verify models and migration**

Run: `uv run pytest tests/test_models.py tests/test_acquisition_migration.py -q`

Expected: all selected tests pass, including upgrade and downgrade assertions.

### Task 2: Replace exposed download URLs with release tokens and batch submission

**Files:**
- Modify: `papyrus/schemas/acquisition.py`
- Modify: `papyrus/services/acquisition.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Modify: `tests/services/test_acquisition.py`
- Modify: `tests/api/routes/test_acquisition.py`

- [x] **Step 1: Write failing release-token tests**

Search results must contain `release_token` and no `download_url`. Verify token round-trip, owner binding, expiry, and tamper rejection using existing secret encryption primitives. The decrypted payload must contain the download URL, title, indexer, protocol, size, seeders, endpoint id, owner id, and expiry.

- [x] **Step 2: Verify token tests fail**

Run: `uv run pytest tests/services/test_acquisition.py tests/api/routes/test_acquisition.py -k "release_token" -q`

Expected: response-contract failures show that raw download URLs are still returned.

- [x] **Step 3: Implement secure search results**

Define `Release` with `release_token`, title, protocol, indexer, size, seeders, and format hints. Sign/encrypt tokens with the existing server secret and a short fixed lifetime. Reject invalid, expired, or cross-owner tokens with a controlled 400 response.

- [x] **Step 4: Write failing batch-submission tests**

`POST /v1/acquisition/submissions/batch` accepts:

```json
{
  "endpoint_id": "uuid",
  "release_tokens": ["token-1", "token-2"]
}
```

Assert one linked placeholder book and job per valid token, stable request ordering, independent failed outcomes, qBittorrent-only endpoint validation, owner checks, generated Papyrus tags, and owner/job-relative save paths. Assert no new raw URL is persisted.

- [x] **Step 5: Implement batch orchestration and compatibility wrapper**

Move all transaction-aware creation into `papyrus/services/acquisition.py`. Keep `/submissions` as a one-item wrapper over the batch service. Return per-item `job` or controlled `error` data without rolling back successful siblings.

- [x] **Step 6: Verify search and batch behavior**

Run: `uv run pytest tests/services/test_acquisition.py tests/api/routes/test_acquisition.py -k "search or token or batch or submission" -q`

Expected: all selected tests pass.

### Task 3: Add owned job APIs and qBittorrent lifecycle operations

**Files:**
- Modify: `papyrus/schemas/acquisition.py`
- Modify: `papyrus/services/acquisition.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Modify: `tests/services/test_acquisition.py`
- Modify: `tests/api/routes/test_acquisition.py`

- [x] **Step 1: Write failing owned-job route tests**

Cover paginated list/detail, candidate files, selecting one file, cancel, retry import, and deleting failed/cancelled jobs. Verify every cross-owner request returns 404, endpoint deletion returns 409 while active jobs exist, invalid transitions return 409, and repeated cancel is idempotent.

- [x] **Step 2: Verify route tests fail**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "job or candidate or cancel or retry" -q`

Expected: missing routes return 404 or existing list responses fail the new contract.

- [x] **Step 3: Implement thin routes and service transitions**

Expose:

```text
GET    /v1/acquisition/jobs
GET    /v1/acquisition/jobs/{job_id}
GET    /v1/acquisition/jobs/{job_id}/files
POST   /v1/acquisition/jobs/{job_id}/file-selection
POST   /v1/acquisition/jobs/{job_id}/cancel
POST   /v1/acquisition/jobs/{job_id}/retry-import
DELETE /v1/acquisition/jobs/{job_id}
```

Allow deletion only for failed/cancelled unimported jobs and remove their placeholder book transactionally. Completed books remain under ordinary book deletion.

- [x] **Step 4: Write and implement qBittorrent adapter tests**

Test login once per operation, tag/save-path fields, torrent info, file listing, pausing, file-priority updates, resuming, and `deleteFiles=true`. Preserve both canonical and lowercase cookie handling and qBittorrent 204 login success.

- [x] **Step 5: Verify job actions**

Run: `uv run pytest tests/services/test_acquisition.py tests/api/routes/test_acquisition.py -q`

Expected: all acquisition service and route tests pass.

### Task 4: Monitor jobs and atomically import completed files

**Files:**
- Create: `papyrus/services/acquisition_monitor.py`
- Modify: `papyrus/services/media.py`
- Modify: `papyrus/main.py`
- Create: `tests/services/test_acquisition_monitor.py`
- Modify: `tests/api/routes/test_media.py`

- [x] **Step 1: Write failing path-import tests**

Add tests for a reusable `import_media_path` service using the same ownership, quota, extension, hashing, replacement, temporary-file, rollback, and aggregate quota locks as uploads. Verify source files remain untouched for seeding.

- [x] **Step 2: Verify import tests fail**

Run: `uv run pytest tests/api/routes/test_media.py -k "import_media_path" -q`

Expected: import function is missing.

- [x] **Step 3: Implement path-based media import**

Factor common persistence from `upload_media` without changing its HTTP contract. Resolve the source before copying, stream into Papyrus-owned temporary storage, commit the asset/book reference atomically, then remove obsolete Papyrus-owned assets only after commit.

- [x] **Step 4: Write failing monitor tests**

Cover endpoint-grouped polling, state/progress mapping, 2/10-second scheduling, 30-second leases, expired-lease recovery, multi-replica exclusion, tag/hash correlation, zero/one/multiple supported candidates, pause-on-selection, path traversal, symlink escape, missing files, quota failure, retry without re-download, restart during import, and no search/rule execution.

- [x] **Step 5: Implement the narrow monitor**

Poll only user-submitted nonterminal jobs. Authenticate once per endpoint cycle. On one supported candidate, transition through `importing` and set `books.file_media_id` before `completed`. On several candidates, pause and enter `needs_file_selection`. Persist controlled recoverable failures and schedule the next poll.

- [x] **Step 6: Wire the monitor lifecycle safely**

Start it only when `ACQUISITION_ENABLED` and `ACQUISITION_IMPORT_ROOT` are configured. Cancel and await it during application shutdown. Do not restore the removed automatic-rule worker.

- [x] **Step 7: Verify monitor and media behavior**

Run: `uv run pytest tests/services/test_acquisition_monitor.py tests/api/routes/test_media.py -q`

Expected: all selected tests pass.

### Task 5: Add the Flutter job model, polling provider, and API

**Files:**
- Modify: `app/lib/acquisition/acquisition_models.dart`
- Modify: `app/lib/acquisition/acquisition_api_client.dart`
- Create: `app/lib/providers/acquisition_downloads_provider.dart`
- Modify: `app/lib/main.dart`
- Modify: `app/test/acquisition/acquisition_models_test.dart`
- Modify: `app/test/acquisition/acquisition_api_client_test.dart`
- Create: `app/test/providers/acquisition_downloads_provider_test.dart`

- [x] **Step 1: Write failing model and API tests**

Cover token-only release parsing, all exact job states, nullable progress fields, batch partial outcomes, pagination, candidates, selection, cancel, retry, and removal request shapes.

- [x] **Step 2: Verify model/API tests fail**

Run: `flutter test test/acquisition`

Expected: new fields, models, and methods are missing.

- [x] **Step 3: Implement typed models and API calls**

Keep credentials server-side. Parse unknown future states as failed/unknown presentation without crashing. Preserve existing integration-management calls.

- [x] **Step 4: Write failing provider tests**

Verify optimistic jobs from batch responses, reconciliation by `bookId`, 2-second visible/10-second foreground polling, no background polling, refresh after auth/server changes, terminal retention, and disposal of timers/in-flight work.

- [x] **Step 5: Implement and register the provider**

The provider owns REST job state only. It must not duplicate synchronized book storage or mutate ordinary library selection.

- [x] **Step 6: Verify the acquisition data layer**

Run: `flutter test test/acquisition test/providers/acquisition_downloads_provider_test.dart`

Expected: all selected tests pass.

### Task 6: Integrate acquisition into the main Books experience

**Files:**
- Modify: `app/lib/pages/library_page.dart`
- Modify: `app/lib/pages/acquisition_page.dart`
- Modify: `app/lib/widgets/search/library_search_bar.dart`
- Modify: `app/lib/widgets/library/book_card.dart`
- Modify: `app/lib/widgets/library/book_grid.dart`
- Modify: `app/lib/widgets/library/book_list_item.dart`
- Modify: `app/lib/widgets/library/library_filter_chips.dart`
- Create: `app/lib/widgets/library/acquisition_result_list.dart`
- Create: `app/lib/widgets/library/download_action_sheet.dart`
- Modify: `app/test/pages/library_page_test.dart`
- Modify: `app/test/pages/acquisition_page_test.dart`
- Create: `app/test/widgets/library/acquisition_result_list_test.dart`

- [x] **Step 1: Write failing local/remote search tests**

Verify local search remains immediate, the explicit `Search indexers for “query”` action appears only when managed acquisition is ready, remote results replace library content only after activation, and returning to local mode restores the prior library state.

- [x] **Step 2: Verify search tests fail**

Run: `flutter test test/pages/library_page_test.dart`

Expected: the indexer-search action and remote mode are absent.

- [x] **Step 3: Implement remote results and batch selection**

Show title, indexer, size, seeders, and format hints in quiet rows. Provide checkboxes, select all/clear, and `Download N`. If several eligible qBittorrent endpoints exist, choose one once per batch. Return to Books and expose optimistic placeholder cards after submission.

- [x] **Step 4: Write failing progress and action tests**

Verify compact grid progress, detailed list progress, Downloads count/filter, completed transition to ordinary books, separate download selection, cancel/remove actions, file-selection/retry bottom sheets, and delete-shelf-style destructive dialogs.

- [x] **Step 5: Implement progress and existing-component actions**

Reuse the real application bottom-sheet chrome and shelf deletion dialog appearance. Do not create custom modal routes or lookalike sheet handles. Failed/cancelled placeholders remain until removal.

- [x] **Step 6: Remove search/submission from settings**

Keep `acquisition_page.dart` focused on indexer, qBittorrent, and Arr integration management and connection testing.

- [x] **Step 7: Verify Books and settings regressions**

Run: `flutter test test/pages/library_page_test.dart test/pages/acquisition_page_test.dart test/widgets/library`

Expected: all selected tests pass.

### Task 7: Document, verify, and exercise the complete flow

**Files:**
- Modify: `.env.example`
- Create: `docs/acquisition-downloads.md`
- Modify: relevant server and client tests only when verification exposes a regression.

- [x] **Step 1: Document shared-path configuration**

Describe qBittorrent Web UI authentication, endpoint-visible download root, server-visible import root, native paths, Docker bind mounts, readiness gating, supported formats, seeding retention, cancellation semantics, and troubleshooting.

- [x] **Step 2: Run server quality gates**

Run:

```bash
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run pyright
```

Use `uv run mypy .` only if Pyright is unavailable. The known local `.env` ordering check must either be realigned without exposing/changing secret values or reported separately from behavioral results.

- [x] **Step 3: Run client quality gates**

Run:

```bash
dart format --output=none --set-exit-if-changed app/lib app/test
flutter analyze
flutter test
```

- [ ] **Step 4: Run live acceptance**

With real Prowlarr and qBittorrent, verify EPUB search, batch submission, immediate placeholders, progress, atomic import, retained seeding, cancellation with partial-data deletion, multi-file attention, and server restart during download/import.

- [ ] **Step 5: Review and commit deliberately**

Review server and client diffs independently. Preserve unrelated changes. Create separate intentional commits only after all relevant checks pass, and do not push until the local acceptance result has been reported.
