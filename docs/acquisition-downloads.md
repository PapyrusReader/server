# Managed Book Acquisition Developer Guide

This guide is for engineers and self-hosting operators configuring Papyrus,
Prowlarr, and qBittorrent. It explains the shared filesystem boundary required
for Papyrus to monitor and import downloads started from the Books page.

Managed acquisition is feature-flagged. It currently handles user-submitted
qBittorrent jobs only; automatic acquisition rules remain disabled.

## How it works

1. Papyrus searches enabled Prowlarr or Torznab indexers.
2. The user selects one or more releases on the Books page.
3. The server creates provisional books and sends each release to qBittorrent.
4. qBittorrent downloads into a job-specific directory.
5. The server monitors progress, validates the completed path, and copies one
   supported book file into private Papyrus media storage.
6. The provisional item becomes an ordinary book. The qBittorrent torrent and
   its original files remain available for seeding.

Release URLs are carried in short-lived, owner-bound tokens and are not stored
on acquisition jobs.

## Prerequisites

- A signed-in Papyrus account
- `ACQUISITION_ENABLED=true` on the server
- A server-visible acquisition import root
- At least one enabled Prowlarr or Torznab endpoint
- At least one enabled qBittorrent endpoint with a download root
- qBittorrent Web UI enabled and reachable from the Papyrus server
- Read and directory-traversal permission for the Papyrus server on the shared
  download directory

The server monitor starts only when both `ACQUISITION_ENABLED` and
`ACQUISITION_IMPORT_ROOT` are set.

## Configure the shared paths

Papyrus asks qBittorrent to save each job below:

```text
<qBittorrent download root>/<Papyrus user ID>/<acquisition job ID>/
```

The server reads the corresponding file below:

```text
<ACQUISITION_IMPORT_ROOT>/<Papyrus user ID>/<acquisition job ID>/
```

The two roots may have different absolute paths, but they must expose the same
relative user/job directory tree.

### Native processes on one host

Use the same absolute directory for both settings:

```dotenv
ACQUISITION_ENABLED=true
ACQUISITION_IMPORT_ROOT=/srv/papyrus-acquisition
```

In the qBittorrent integration editor, set **Download root** to:

```text
/srv/papyrus-acquisition
```

Create the directory before starting the server and grant qBittorrent write
access and Papyrus read access.

### Containers

Bind-mount one host directory into both containers. The container paths do not
need to match:

```yaml
services:
  server:
    volumes:
      - /srv/papyrus-acquisition:/imports:ro
    environment:
      ACQUISITION_ENABLED: "true"
      ACQUISITION_IMPORT_ROOT: /imports

  qbittorrent:
    volumes:
      - /srv/papyrus-acquisition:/downloads
```

Set the qBittorrent integration's **Download root** to `/downloads`. If both
services share a Compose network, use a server-reachable URL such as
`http://qbittorrent:8080`; `localhost` inside the Papyrus container refers to
the Papyrus container itself.

The repository's default Compose file does not provide this acquisition bind
mount. Add it in a local Compose override or deployment configuration.

## Configure the server

Add these values to `.env`:

```dotenv
ACQUISITION_ENABLED=true
ACQUISITION_IMPORT_ROOT=/srv/papyrus-acquisition
ACQUISITION_MONITOR_ACTIVE_INTERVAL_SECONDS=2
ACQUISITION_MONITOR_IDLE_INTERVAL_SECONDS=10
ACQUISITION_MONITOR_LEASE_SECONDS=30
```

The interval and lease defaults normally do not need adjustment. Apply the
database migration and restart the API:

```bash
uv run alembic upgrade head
uv run uvicorn papyrus.main:app --reload
```

## Configure integrations

Open **Settings → Acquisition**.

For Prowlarr:

- Use a URL reachable from the Papyrus server.
- Copy the API key from Prowlarr's general settings.
- Enable at least one working torrent indexer in Prowlarr.

For qBittorrent:

- Enable the Web UI.
- Use its server-reachable URL and Web UI credentials.
- Set **Download root** to the path qBittorrent sees, not the path shown inside
  the Papyrus server container.
- Save the integration after **Test connection** succeeds.

The Books page exposes indexer search only after the server reports a configured
`ACQUISITION_IMPORT_ROOT` and it sees an enabled indexer plus an enabled
qBittorrent integration with a download root.

## Download lifecycle

- **Queued / submitted / downloading / importing:** the book stays in the
  library with progress.
- **Needs file selection:** qBittorrent is paused because the release contains
  several supported files. Choose one from the attention sheet to resume.
- **Failed:** fix the path, permission, quota, or client problem, then retry the
  import. Retry does not submit a second torrent.
- Temporary qBittorrent or network failures remain active and are retried with
  bounded backoff instead of immediately becoming terminal failures.
- **Cancelled:** Papyrus deletes the torrent and downloaded data from
  qBittorrent.
- **Remove:** available for failed or cancelled jobs; it removes the job and
  its empty provisional book.
- **Completed:** progress decoration disappears and the item behaves as an
  ordinary book. The copied Papyrus media file is independent of the seeding
  copy.

Supported book extensions are EPUB, PDF, MOBI, AZW3, TXT, CBR, and CBZ.

## Validate the setup

1. Search for a title from the main Books page.
2. Select one release and choose **Download**.
3. Confirm that a provisional book appears immediately.
4. Confirm that qBittorrent saves below the configured user/job directory.
5. Watch progress on the book or in the Downloads filter.
6. Confirm that the completed book opens from Papyrus and the torrent remains
   in qBittorrent.

Repeat with multiple selected releases to validate partial batch outcomes.

## Troubleshooting

### Indexer search is unavailable

Check that acquisition is enabled, `ACQUISITION_IMPORT_ROOT` is configured, the
client is signed in and online, and Settings → Acquisition contains both an
enabled indexer and an enabled qBittorrent integration with a non-empty
download root.

### Connection testing works in a browser but not in Papyrus

The Papyrus server performs integration requests. Verify the base URL from the
server host or container, including DNS, port, firewall, and qBittorrent Web UI
authentication settings.

### A job downloads but import fails

Compare the qBittorrent save path with `ACQUISITION_IMPORT_ROOT`. The relative
user ID, job ID, and filename must exist below both roots. Also check:

- Papyrus can traverse directories and read the file.
- The path does not contain symbolic links.
- The selected file has a supported extension.
- The account has enough media-storage quota.

After correcting the cause, use **Retry import**.

### A release needs file selection

The torrent contains more than one supported book file. Open the job's
attention sheet and select the intended file. Papyrus lowers the other file
priorities and resumes qBittorrent.

### Cancelling removes downloaded data

This is intentional. Cancellation calls qBittorrent with file deletion
enabled. Do not cancel a job when its partial data must be preserved.
