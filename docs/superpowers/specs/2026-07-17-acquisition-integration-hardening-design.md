# Acquisition Integration Hardening Technical Specification

- Status: Approved for implementation
- Date: 2026-07-17
- Audience: Papyrus server and Flutter client maintainers
- Scope: `PapyrusReader/server#4` and `PapyrusReader/client#18`

## Overview

This specification defines the changes required to make the server-mediated torrent acquisition feature safe to enable, predictable across the HTTP boundary, and complete enough for client integration. It records the accepted decisions to disable automatic rule execution and require an explicit server-operator opt-in.

## Problem

The current pull requests expose useful integration primitives, but they do not yet provide a safe release boundary. Remote submission failures are returned as successful HTTP requests and ignored by the client, endpoint deletion conflicts with audit-history foreign keys, every API process starts an unsafe repeating worker, and arbitrary outbound integration requests are enabled on every server. The client also lacks capability-aware navigation and several required management states.

## Goals

- Require a server operator to enable acquisition explicitly.
- Keep manual endpoint, search, submission, Arr-command, and rule execution workflows.
- Remove automatic background rule execution from this release.
- Preserve audit jobs while allowing integrations to be deleted safely.
- Make failed remote operations visible to the Flutter client.
- Support non-persisting connection tests for new and edited integrations.
- Complete capability gating, integration forms, indexer selection, and per-release submission state.
- Add regression coverage for each corrected behavior.

## Non-goals

- Scheduling or automatically claiming acquisition rules.
- Cross-replica worker leadership or distributed job queues.
- Usenet or Newznab support.
- A general-purpose outbound proxy or administrator-managed hostname allowlist.
- Redesigning unrelated authentication, profile, or synchronization code.

## Constraints

- Private and loopback integration URLs remain valid after an operator enables acquisition because LAN services are the primary use case.
- Credentials stay encrypted at rest and are never returned by API schemas.
- Existing PR dependencies and repository architecture remain unchanged.
- Route handlers remain thin; protocol and transaction-aware behavior belongs in `papyrus/services`.
- The Flutter feature remains disabled by default in local preferences.

## Proposed design

### Server activation boundary

Add `acquisition_enabled: bool = False` to server settings and document `ACQUISITION_ENABLED=false` in `.env.example`. `GET /v1/acquisition/capabilities` remains available and returns `enabled=false` with empty supported-kind and command collections when disabled. Every other acquisition route uses a shared dependency that returns HTTP 404 while the feature is disabled.

Enabling this setting is the operator's authorization for authenticated users to connect the server to private/LAN HTTP services. Deployments that do not trust their users must leave the setting disabled or enforce outbound restrictions outside the application.

### Manual rules only

Remove the acquisition worker from the FastAPI lifespan and remove the automation interval setting. Retain rule creation, listing, and `POST /rules/{rule_id}/run` for explicit execution. No code in this release executes rules on a timer.

### Job outcome contract

Submission and Arr-command endpoints continue returning HTTP 201 with an `AcquisitionJob` audit record. The domain outcome is the job's `status`:

- `submitted`: the remote system accepted the operation.
- `failed`: the remote adapter rejected or could not complete the operation; `error` contains a user-safe message.
- `queued`: reserved for a future asynchronous execution model.

The Flutter API client parses the job response. UI success feedback is shown only for `submitted`; `failed` becomes an operation error using the returned message.

Transmission adapters validate the RPC `result` field. Deluge adapters validate JSON-RPC errors/results and choose `core.add_torrent_magnet` for magnets or `core.add_torrent_url` for HTTP(S) torrent URLs. Malformed remote payloads become controlled HTTP 502 errors instead of uncaught exceptions.

### Safe endpoint deletion

Audit jobs remain after integration deletion. Change `acquisition_jobs.endpoint_id` and `acquisition_rules.download_client_id` to nullable foreign keys with `ON DELETE SET NULL`. Before deleting an endpoint, remove its identifier from the owner's rule `endpoint_ids`; disable any rule that loses its download client or all configured indexers. The endpoint credentials are deleted with the endpoint.

The unmerged Alembic revision is updated in place so model metadata and the migration remain aligned. Downgrade continues to drop the three acquisition tables.

### Connection testing

Add authenticated `POST /v1/acquisition/endpoints/test`. The request accepts either:

- complete unsaved endpoint values (`kind`, `base_url`, and relevant credentials), or
- an owned `endpoint_id` plus optional URL or credential overrides for an edit flow.

The server builds an in-memory endpoint configuration, merges stored credentials only for an owned endpoint, performs a bounded protocol-specific health/authentication request, and returns `{ "ok": true }`. Failures use controlled 4xx/502 responses and never persist the supplied credentials.

Protocol checks use the smallest supported request: system status/capabilities for indexers and Arr apps, authentication or session inspection for download clients.

### Client availability and navigation

Add a lifecycle-owned acquisition availability provider that caches capability state for the active server. It closes/replaces its HTTP client when the server changes and refreshes through the existing access-token refresh path.

The Profile toggle remains visible and disabled by default. The management row appears only when the preference is enabled and the active server reports `enabled=true`. The router redirects `/acquisition` to Profile unless both conditions are true. Unknown/loading capability state is treated as unavailable.

### Client management and action states

The endpoint dialog:

- shows API key fields for Prowlarr, Torznab, and Arr applications;
- shows username/password for qBittorrent and Transmission;
- shows password for Deluge;
- validates HTTP(S) URLs without embedded credentials;
- supports a non-persisting connection test;
- shows inline test/save progress and error text;
- prevents duplicate test or save requests.

Search exposes enabled indexers as selectable controls. Search is enabled only when at least one indexer is selected and at least one enabled download client exists. A new search clears stale results and errors.

Submission state is tracked by release and target client. An in-flight pair cannot be submitted twice, while unrelated releases remain actionable. Returned failed jobs display their server-provided error. Arr commands use the same job-outcome handling.

## Interfaces and dependencies

- Server configuration: `ACQUISITION_ENABLED`.
- Existing authenticated acquisition routes retain their paths.
- New route: `POST /v1/acquisition/endpoints/test`.
- `AcquisitionCapabilities.enabled` becomes authoritative in the Flutter model.
- `AcquisitionJob` becomes a typed Flutter response model.
- No new third-party dependencies are introduced.

## Testing strategy

Server tests cover disabled capabilities/routes, connection-test ownership and non-persistence, failed job responses, Transmission/Deluge protocol errors, magnet versus URL dispatch, endpoint deletion with jobs/rules, and manual-only application lifespan. Migration metadata and a single Alembic head are verified alongside Ruff, Mypy, and Pytest.

Client tests cover capability parsing, availability refresh and server switching, route/profile gating, conditional credential fields, connection-test serialization and error state, indexer selection/search prerequisites, per-release duplicate prevention, and submitted/failed job feedback. Dart formatting, Flutter analysis, and the full Flutter test suite must pass.

## Risks and mitigations

- Private-network access is intentionally powerful. It is mitigated by a disabled-by-default operator setting and documented trust requirement.
- Making endpoint foreign keys nullable changes response schemas. The server schema and any client job model must accept missing endpoint IDs.
- Capability checks can race server changes. Availability state is keyed to the active server URI and reset before each refresh.
- Protocol health endpoints vary by version. Tests cover request shape, and failures are surfaced rather than treated as success.

## Rollout and rollback

1. Merge and deploy the server PR with the migration.
2. Leave acquisition disabled until the operator explicitly sets `ACQUISITION_ENABLED=true`.
3. Verify capabilities, connection testing, manual search, submission, and Arr commands against supported services.
4. Merge and release the client PR after server verification.

Rollback the client independently if UI behavior regresses. Roll back the server only before users depend on stored acquisition configuration; the downgrade deletes acquisition endpoints, encrypted credentials, rules, and job history.

## Accepted decisions

- Automatic background rule execution is excluded from this release.
- Acquisition is disabled by default and enabled by server operators.
- Enabled servers permit private/LAN integration URLs.
- Audit jobs survive endpoint deletion through nullable foreign keys.
- HTTP 201 plus typed job status is the submission outcome contract.
