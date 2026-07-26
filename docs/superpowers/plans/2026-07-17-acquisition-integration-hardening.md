# Acquisition Integration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make server PR #4 and client PR #18 safe to merge by adding an operator activation boundary, removing automatic execution, correcting persistence and remote-operation semantics, and completing the client management workflow.

**Architecture:** The server remains the only component that stores credentials or contacts acquisition integrations. A disabled-by-default server capability gates every private route, protocol adapters return controlled job outcomes, and manual rules replace the unsafe worker. The Flutter client caches capability availability, consumes typed job outcomes, and owns presentation state for integration testing, search selection, and submissions.

**Tech Stack:** FastAPI, Pydantic, async SQLAlchemy, PostgreSQL, Alembic, pytest, Flutter, Dart, Provider, go_router, package:http, flutter_test.

---

## Repository map

Server checkout: `/tmp/papyrus-server-pr4-work.aXSYmc/repo`

- `papyrus/config.py`: operator feature setting.
- `papyrus/api/routes/acquisition.py`: HTTP dependencies and thin route wiring.
- `papyrus/services/acquisition.py`: connection testing, protocol adapters, deletion cleanup, and job orchestration.
- `papyrus/schemas/acquisition.py`: capability, connection-test, and nullable job contracts.
- `papyrus/models/acquisition.py`: nullable foreign-key model metadata.
- `alembic/versions/d4e5f6a7b8c9_add_acquisition.py`: new-table migration aligned with the models.
- `papyrus/main.py`: application lifespan without acquisition automation.
- `tests/api/routes/test_acquisition.py`: authenticated API and persistence regressions.
- `tests/services/test_acquisition.py`: protocol adapter regressions.

Client checkout: `/tmp/papyrus-client-pr18-work.hH4BiS/repo`

- `app/lib/acquisition/acquisition_models.dart`: capability and job response types.
- `app/lib/acquisition/acquisition_api_client.dart`: typed job and connection-test calls.
- `app/lib/providers/acquisition_availability_provider.dart`: lifecycle-owned capability cache.
- `app/lib/main.dart`: provider construction, replacement, disposal, and registration.
- `app/lib/config/app_router.dart`: synchronous preference plus capability guard.
- `app/lib/pages/profile_page.dart`: capability-aware management visibility.
- `app/lib/pages/acquisition_page.dart`: integration dialog, search selection, and per-release state.
- `app/test/acquisition/`: API, model, and availability-provider tests.
- `app/test/config/app_router_test.dart`: route guard regression.
- `app/test/pages/profile_storage_sync_test.dart`: Profile visibility regression.
- `app/test/pages/acquisition_page_test.dart`: management and action-state widget regressions.

### Task 1: Add the server activation boundary and remove automation

**Files:**
- Modify: `.env.example`
- Modify: `papyrus/config.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Modify: `papyrus/main.py`
- Modify: `tests/api/routes/test_acquisition.py`

- [ ] **Step 1: Write failing activation tests**

Add tests that temporarily set the singleton application setting and restore it in `finally`:

```python
async def test_disabled_capabilities_are_empty(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "acquisition_enabled", False)

    response = await client.get("/v1/acquisition/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "endpoint_kinds": [],
        "indexer_kinds": [],
        "download_client_kinds": [],
        "arr_kinds": [],
        "arr_commands": {},
    }


async def test_disabled_acquisition_routes_are_not_available(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_enabled", False)

    response = await client.get("/v1/acquisition/endpoints", headers=auth_headers)

    assert response.status_code == 404
```

- [ ] **Step 2: Verify the tests fail for the expected reason**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "disabled" -q`

Expected: capability assertion fails because `enabled` is true and endpoints remain accessible.

- [ ] **Step 3: Implement the setting, dependency, and manual-only lifespan**

Add the setting and route dependency:

```python
class Settings(BaseSettings):
    acquisition_enabled: bool = False


def require_acquisition_enabled() -> None:
    if not get_settings().acquisition_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acquisition is disabled")
```

Attach `Depends(require_acquisition_enabled)` to every acquisition route except `/capabilities`. Return empty capability collections when disabled. Remove `acquisition_worker`, its task lifecycle, the `run_enabled_rules` import, and `acquisition_automation_interval_seconds`. Document `ACQUISITION_ENABLED=false` in `.env.example`.

- [ ] **Step 4: Verify activation tests pass**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "capabilities or disabled" -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the activation boundary**

```bash
git add .env.example papyrus/config.py papyrus/api/routes/acquisition.py papyrus/main.py tests/api/routes/test_acquisition.py
git commit -m "fix: gate acquisition behind server opt-in"
```

### Task 2: Correct remote adapter and job outcome behavior

**Files:**
- Modify: `papyrus/services/acquisition.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Create: `tests/services/test_acquisition.py`
- Modify: `tests/api/routes/test_acquisition.py`

- [ ] **Step 1: Write failing Transmission and Deluge tests**

Use real adapter functions with only `_request` replaced:

```python
async def test_transmission_rejects_rpc_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b'{"result":"invalid or corrupt torrent file","arguments":{}}'

    monkeypatch.setattr(acquisition, "_request", request)

    with pytest.raises(HTTPException) as exc_info:
        await acquisition.submit_to_client(_endpoint("transmission"), "magnet:?xt=urn:btih:test", None, None)

    assert exc_info.value.status_code == 502


async def test_deluge_uses_url_method_for_http_torrent(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, object]] = []

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        bodies.append(json.loads(cast(bytes, kwargs["body"])))
        if len(bodies) == 1:
            return 200, {"Set-Cookie": "_session_id=test"}, b'{"result":true,"error":null,"id":1}'
        return 200, {}, b'{"result":"torrent-id","error":null,"id":2}'

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.submit_to_client(_endpoint("deluge"), "https://indexer.test/release.torrent", None, None)

    assert bodies[1]["method"] == "core.add_torrent_url"
```

Add a route regression asserting a rejected adapter produces a persisted `status="failed"` job with HTTP 201 and a safe `error` string.

- [ ] **Step 2: Verify the adapter tests fail**

Run: `uv run pytest tests/services/test_acquisition.py -q`

Expected: Transmission does not raise and Deluge uses `core.add_torrent_magnet` for the URL.

- [ ] **Step 3: Implement strict remote response parsing**

Add focused helpers:

```python
def _json_object(payload: bytes, integration: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"{integration} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail=f"{integration} returned an invalid response")
    return value


def _require_deluge_result(payload: bytes) -> object:
    response = _json_object(payload, "Deluge")
    if response.get("error") is not None or response.get("result") in {None, False}:
        raise HTTPException(status_code=502, detail="Deluge rejected the request")
    return response["result"]
```

Require Transmission `result == "success"`. Validate Deluge login and add responses. Choose the Deluge method from `download_url.startswith("magnet:")`. Use `_json_object` for Prowlarr and Arr payloads so malformed responses remain controlled failures.

- [ ] **Step 4: Verify service and route tests pass**

Run: `uv run pytest tests/services/test_acquisition.py tests/api/routes/test_acquisition.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit adapter corrections**

```bash
git add papyrus/services/acquisition.py papyrus/api/routes/acquisition.py tests/services/test_acquisition.py tests/api/routes/test_acquisition.py
git commit -m "fix: validate acquisition client responses"
```

### Task 3: Make endpoint deletion preserve audit history

**Files:**
- Modify: `papyrus/models/acquisition.py`
- Modify: `papyrus/schemas/acquisition.py`
- Modify: `papyrus/services/acquisition.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Modify: `alembic/versions/d4e5f6a7b8c9_add_acquisition.py`
- Modify: `tests/api/routes/test_acquisition.py`

- [ ] **Step 1: Write the failing deletion regression**

Create an endpoint, a job referencing it, and a rule that uses it as both an indexer and download client. Delete through the API and assert:

```python
assert response.status_code == 204
assert persisted_job.endpoint_id is None
assert persisted_rule.download_client_id is None
assert persisted_rule.endpoint_ids == []
assert persisted_rule.enabled is False
```

- [ ] **Step 2: Verify deletion fails with a foreign-key error**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "delete_endpoint_preserves" -q`

Expected: the request returns 500 or raises `IntegrityError` because dependent rows still reference the endpoint.

- [ ] **Step 3: Implement nullable foreign keys and cleanup**

Update model metadata and the unmerged migration:

```python
download_client_id: Mapped[UUID | None] = mapped_column(
    Uuid,
    ForeignKey("acquisition_endpoints.endpoint_id", ondelete="SET NULL"),
    nullable=True,
)
endpoint_id: Mapped[UUID | None] = mapped_column(
    Uuid,
    ForeignKey("acquisition_endpoints.endpoint_id", ondelete="SET NULL"),
    nullable=True,
)
```

Make `AcquisitionJob.endpoint_id` optional in the response schema. Add a service that locks/loads the owner's rules, removes the deleted ID from JSON lists, clears matching download clients, disables unusable rules, deletes the endpoint, and commits once. Delegate the route to that service.

- [ ] **Step 4: Verify deletion and migration metadata**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "delete_endpoint_preserves" -q`

Run: `uv run alembic heads`

Expected: the regression passes and `d4e5f6a7b8c9 (head)` is the only head.

- [ ] **Step 5: Commit persistence corrections**

```bash
git add papyrus/models/acquisition.py papyrus/schemas/acquisition.py papyrus/services/acquisition.py papyrus/api/routes/acquisition.py alembic/versions/d4e5f6a7b8c9_add_acquisition.py tests/api/routes/test_acquisition.py
git commit -m "fix: preserve acquisition jobs on endpoint deletion"
```

### Task 4: Add non-persisting connection tests

**Files:**
- Modify: `papyrus/schemas/acquisition.py`
- Modify: `papyrus/services/acquisition.py`
- Modify: `papyrus/api/routes/acquisition.py`
- Modify: `tests/api/routes/test_acquisition.py`
- Modify: `tests/services/test_acquisition.py`

- [ ] **Step 1: Write failing API tests for unsaved and edited connections**

Add tests that call `/v1/acquisition/endpoints/test` with an unsaved Prowlarr payload and with an owned endpoint ID plus credential overrides. Replace `test_endpoint_connection` at the route boundary and capture the transient endpoint. Assert no new `AcquisitionEndpoint` row is persisted and another user's endpoint returns 404.

```python
assert response.status_code == 200
assert response.json() == {"ok": True}
assert captured.kind == "prowlarr"
assert decrypt_secret_payload(captured.credentials["encrypted"])["api_key"] == "override"
assert await endpoint_count(db_session) == before_count
```

- [ ] **Step 2: Verify the connection-test route is missing**

Run: `uv run pytest tests/api/routes/test_acquisition.py -k "test_connection" -q`

Expected: requests return 404.

- [ ] **Step 3: Implement schemas, transient configuration, and protocol checks**

Add:

```python
class AcquisitionEndpointTest(BaseModel):
    endpoint_id: UUID | None = None
    kind: EndpointKind | None = None
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None


class AcquisitionEndpointTestResult(BaseModel):
    ok: bool
```

Validate that `kind` and `base_url` are present without `endpoint_id`. For an edit, load only an owned endpoint and merge supplied values with decrypted stored credentials in memory. Implement protocol-specific bounded checks and return `AcquisitionEndpointTestResult(ok=True)` only after the remote accepts the request.

- [ ] **Step 4: Verify connection-test coverage**

Run: `uv run pytest tests/api/routes/test_acquisition.py tests/services/test_acquisition.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit connection testing**

```bash
git add papyrus/schemas/acquisition.py papyrus/services/acquisition.py papyrus/api/routes/acquisition.py tests/api/routes/test_acquisition.py tests/services/test_acquisition.py
git commit -m "feat: test acquisition connections without saving"
```

### Task 5: Verify the complete server branch

**Files:**
- Review all server files changed by Tasks 1-4.

- [ ] **Step 1: Run formatting and lint checks**

Run: `uv run ruff format --check .`

Run: `uv run ruff check .`

Expected: both commands exit 0.

- [ ] **Step 2: Run type checking**

Run: `uv run mypy .`

Expected: exit 0 with no type errors.

- [ ] **Step 3: Run the full server test suite**

Run: `uv run pytest`

Expected: all tests pass.

- [ ] **Step 4: Verify migration shape**

Run: `uv run alembic heads`

Run against a disposable test database: `uv run alembic upgrade head`

Expected: one head and a successful upgrade creating nullable `SET NULL` acquisition foreign keys.

### Task 6: Add typed client capability, job, and connection-test contracts

**Files:**
- Modify: `app/lib/acquisition/acquisition_models.dart`
- Modify: `app/lib/acquisition/acquisition_api_client.dart`
- Modify: `app/test/acquisition/acquisition_models_test.dart`
- Modify: `app/test/acquisition/acquisition_api_client_test.dart`

- [ ] **Step 1: Write failing model and API tests**

Add assertions for `enabled`, nullable job endpoint IDs, failed jobs, and connection-test payloads:

```dart
expect(AcquisitionCapabilities.fromJson({'enabled': false}).enabled, isFalse);

final job = AcquisitionJob.fromJson({
  'job_id': 'job-1',
  'endpoint_id': null,
  'rule_id': null,
  'title': 'Release',
  'download_url': 'magnet:?xt=urn:btih:test',
  'status': 'failed',
  'error': 'Transmission rejected the release',
});
expect(job.isSubmitted, isFalse);
expect(job.error, 'Transmission rejected the release');
```

Expect `submitRelease` and `runArrCommand` to return `AcquisitionJob`. Expect `testEndpoint` to POST `/v1/acquisition/endpoints/test` with either unsaved values or `endpoint_id` and overrides.

- [ ] **Step 2: Verify the new contract tests fail**

Run: `flutter test --no-pub test/acquisition/acquisition_models_test.dart test/acquisition/acquisition_api_client_test.dart`

Expected: missing `enabled`, `AcquisitionJob`, and `testEndpoint` APIs fail compilation.

- [ ] **Step 3: Implement typed contracts**

Add immutable model fields and parsing:

```dart
class AcquisitionJob {
  final String id;
  final String? endpointId;
  final String status;
  final String? error;

  bool get isSubmitted => status == 'submitted';
}
```

Return parsed jobs from submission methods and add the connection-test call. Keep all authentication errors represented by `AuthApiException`.

- [ ] **Step 4: Verify model and API tests pass**

Run: `flutter test --no-pub test/acquisition/acquisition_models_test.dart test/acquisition/acquisition_api_client_test.dart`

Expected: all selected tests pass.

- [ ] **Step 5: Commit client contracts**

```bash
git add app/lib/acquisition/acquisition_models.dart app/lib/acquisition/acquisition_api_client.dart app/test/acquisition/acquisition_models_test.dart app/test/acquisition/acquisition_api_client_test.dart
git commit -m "fix: consume acquisition capability and job outcomes"
```

### Task 7: Add lifecycle-owned capability availability and route gating

**Files:**
- Create: `app/lib/providers/acquisition_availability_provider.dart`
- Modify: `app/lib/main.dart`
- Modify: `app/lib/config/app_router.dart`
- Modify: `app/lib/pages/profile_page.dart`
- Create: `app/test/acquisition/acquisition_availability_provider_test.dart`
- Modify: `app/test/config/app_router_test.dart`
- Modify: `app/test/pages/profile_storage_sync_test.dart`

- [ ] **Step 1: Write failing provider, router, and Profile tests**

Define provider tests with an injected capability loader:

```dart
final provider = AcquisitionAvailabilityProvider(
  loadCapabilities: (_) async => const AcquisitionCapabilities(
    enabled: true,
    endpointKinds: [],
    indexerKinds: [],
    downloadClientKinds: [],
    arrKinds: [],
    arrCommands: {},
  ),
);
await provider.refresh(serverBaseUri);
expect(provider.isAvailableFor(serverBaseUri), isTrue);
```

Extend router tests so `/acquisition` redirects when the preference is on but availability is false, then permits the route after availability becomes true. Extend Profile tests so the toggle remains visible while `Torrent & automation` appears only for enabled preference plus available server.

- [ ] **Step 2: Verify availability tests fail**

Run: `flutter test --no-pub test/acquisition/acquisition_availability_provider_test.dart test/config/app_router_test.dart test/pages/profile_storage_sync_test.dart`

Expected: the provider type is missing and router/Profile only check local preferences.

- [ ] **Step 3: Implement the availability provider and wiring**

Create a `ChangeNotifier` keyed by active server URI with `unknown`, `loading`, `available`, and `unavailable` state. It owns one `AcquisitionApiClient`, closes it on server change/disposal, and loads through `AuthProvider.withFreshAccessToken`.

Construct it in `_PapyrusState`, pass it to `AppRouter`, register it in `MultiProvider`, refresh it after auth/server changes, and dispose it. Include it in `Listenable.merge`. Guard with:

```dart
if (location == '/acquisition' &&
    (!preferencesProvider.acquisitionEnabled ||
     !acquisitionAvailabilityProvider.isAvailableFor(activeServerUri))) {
  return '/profile';
}
```

Profile watches the provider and gates only the management row/button, not the opt-in toggle.

- [ ] **Step 4: Verify provider, router, and Profile tests pass**

Run: `flutter test --no-pub test/acquisition/acquisition_availability_provider_test.dart test/config/app_router_test.dart test/pages/profile_storage_sync_test.dart`

Expected: all selected tests pass.

- [ ] **Step 5: Commit availability gating**

```bash
git add app/lib/providers/acquisition_availability_provider.dart app/lib/main.dart app/lib/config/app_router.dart app/lib/pages/profile_page.dart app/test/acquisition/acquisition_availability_provider_test.dart app/test/config/app_router_test.dart app/test/pages/profile_storage_sync_test.dart
git commit -m "fix: gate acquisition UI by server capability"
```

### Task 8: Complete integration testing and form state

**Files:**
- Modify: `app/lib/pages/acquisition_page.dart`
- Create: `app/test/pages/acquisition_page_test.dart`

- [ ] **Step 1: Write failing dialog widget tests**

Pump `AcquisitionPage` with injected/fake API behavior and assert:

- Prowlarr shows API key but not username/password.
- qBittorrent shows username/password but not API key.
- Deluge shows password only.
- Test connection displays progress, prevents a second request, and renders a returned error inside the dialog.
- Save is disabled while a test/save request is active.

- [ ] **Step 2: Verify dialog tests fail**

Run: `flutter test --no-pub test/pages/acquisition_page_test.dart --plain-name "integration dialog"`

Expected: all credential fields are currently unconditional and there is no connection-test action.

- [ ] **Step 3: Implement conditional fields and inline operation state**

Extract a focused stateful `_EndpointDialog` widget with injected callbacks. Derive fields from `AcquisitionEndpointKind` and maintain distinct `testing`, `saving`, and `error` state. Send current form values to `testEndpoint`; for edits include `endpointId` so blank credentials preserve stored secrets.

- [ ] **Step 4: Verify dialog tests pass**

Run: `flutter test --no-pub test/pages/acquisition_page_test.dart --plain-name "integration dialog"`

Expected: all selected tests pass.

- [ ] **Step 5: Commit integration form completion**

```bash
git add app/lib/pages/acquisition_page.dart app/test/pages/acquisition_page_test.dart
git commit -m "feat: test acquisition integrations from the client"
```

### Task 9: Complete search selection and per-release outcomes

**Files:**
- Modify: `app/lib/pages/acquisition_page.dart`
- Modify: `app/test/pages/acquisition_page_test.dart`

- [ ] **Step 1: Write failing search and submission widget tests**

Assert that search remains disabled without both a selected enabled indexer and an enabled download client. Select one of two indexer chips and verify only its ID is sent. Start one submission and assert only that release/client pair is disabled. Complete with a failed job and assert the backend error is displayed instead of success.

- [ ] **Step 2: Verify state tests fail**

Run: `flutter test --no-pub test/pages/acquisition_page_test.dart --plain-name "search and submission"`

Expected: no indexer controls exist, search ignores client availability, and the page uses one global submission flag.

- [ ] **Step 3: Implement selected indexers and pair-scoped submissions**

Maintain `Set<String> _selectedIndexerIds` and initialize newly loaded enabled indexers only when no explicit selection exists. Render `FilterChip` controls. Compute `canSearch` from selected enabled indexers plus enabled clients.

Maintain `Set<String> _submittingKeys` where the key combines release URL and client ID. Add before awaiting, remove in `finally`, and pass disabled state to each release tile. Inspect the returned job and show success only for `submitted`; otherwise show `job.error ?? 'Submission failed.'`. Apply the same outcome check to Arr commands.

- [ ] **Step 4: Verify state tests pass**

Run: `flutter test --no-pub test/pages/acquisition_page_test.dart`

Expected: all acquisition page tests pass.

- [ ] **Step 5: Commit search and submission completion**

```bash
git add app/lib/pages/acquisition_page.dart app/test/pages/acquisition_page_test.dart
git commit -m "fix: complete acquisition search and submission states"
```

### Task 10: Verify the complete client branch

**Files:**
- Review all client files changed by Tasks 6-9.

- [ ] **Step 1: Format and verify formatting**

Run: `dart format app/lib app/test`

Run: `dart format --output=none --set-exit-if-changed app/lib app/test`

Expected: the second command exits 0 with zero changed files.

- [ ] **Step 2: Run static analysis**

Run from `app/`: `flutter analyze --no-pub`

Expected: exit 0 with no issues.

- [ ] **Step 3: Run the full Flutter test suite**

Run from `app/`: `flutter test --no-pub`

Expected: all tests pass; only explicitly skipped integration tests remain skipped.

### Task 11: Final cross-repository verification and publication

**Files:**
- Review both repository diffs and commit histories.

- [ ] **Step 1: Confirm clean worktrees and intended commits**

Run in each checkout: `git status --short --branch`

Run in each checkout: `git log --oneline --decorate -8`

Expected: clean worktrees with focused commits on top of the original PR heads.

- [ ] **Step 2: Re-run the narrow cross-contract tests**

Server: `uv run pytest tests/api/routes/test_acquisition.py tests/services/test_acquisition.py -q`

Client from `app/`: `flutter test --no-pub test/acquisition test/config/app_router_test.dart test/pages/acquisition_page_test.dart test/pages/profile_storage_sync_test.dart`

Expected: both commands exit 0.

- [ ] **Step 3: Push to the existing PR source branches**

After confirming the source branch names and remote permissions:

```bash
git push github HEAD:feature/torrent-acquisition
```

Run once from each repository checkout. Do not force-push.

- [ ] **Step 4: Verify GitHub heads and checks**

Confirm server PR #4 and client PR #18 point at the new commits. Report CI as pending until GitHub Actions completes; do not claim remote success from local checks alone.
