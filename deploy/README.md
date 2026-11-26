# Single-server production deployment

This is a portable Docker Compose deployment for a Linux VM, including Hetzner
Cloud. It is separate from the local `docker-compose.yml`. Nothing here provisions
or changes a live server automatically.

The API and migration job use `ghcr.io/papyrusreader/server:<version>`, built for
amd64 and arm64 when Octopus dispatches the server build on `master`. API version
metadata comes from the installed Python package. GitHub releases record the
image digest; deploy a recorded digest instead of a mutable tag when stronger
artifact pinning is needed. The runtime uses UID/GID 10001, not root.

## Branch workflow

Feature/fix PRs target the default `development` branch and leave versions
unchanged. CI checks integration work without publishing a container. Octopus pins
the versioned server commit in a protected workspace manifest and dispatches
the build with the approved manifest SHA. It deploys the resulting image by
digest before publishing client downloads. See the
[workspace release guide](https://github.com/PapyrusReader/papyrus/blob/development/RELEASING.md).

## Host and domains

Use a VM with Docker Engine/Compose v2 and Python 3.12+, enough disk for uploaded
books, and off-host backups. Avoid sizing from an untested load estimate; monitor
memory, database storage and replication lag during internal testing. Configure
SSH key access and a Hetzner firewall allowing SSH from your own IP and public
TCP 80/443 (UDP 443 is optional for HTTP/3). This Compose project publishes no
host ports. Databases remain on its private network; only API, sync and the
Flutter web server join the external `papyrus-edge` network.

The registered domain is `papyrus-reader.com`. Use `api.papyrus-reader.com`,
`sync.papyrus-reader.com` and `app.papyrus-reader.com`.
Point their DNS A records to the VM (add AAAA only if IPv6 routing works). The
independent `papyrus-edge` Compose project owns public ports and HTTPS certificates.
Its configuration is in `PapyrusReader/observability` (`observability/edge` in the workspace) and
uses `papyrus-api:8080`, `papyrus-sync:8080` and `papyrus-app:8080` as upstreams.
The `web` service here serves the built Flutter app over internal HTTP, including
verification/password-reset routes. The client
release environment must use the same API/sync origins. Set the Google OAuth web
client's authorized redirect URI to
`https://api.papyrus-reader.com/v1/auth/oauth/google/callback`; mobile callbacks remain
`papyrus://auth/callback`.

Windows and Linux apps receive the server's login result at
`http://localhost:43821/auth/callback`. The API allows this exact desktop callback
in production without adding `localhost` to `OAUTH_ALLOWED_REDIRECT_HOSTS`.
Google still redirects to the public API callback above. Do not register the
desktop loopback URL as the Google web client's authorized redirect URI.

Provision the shared edge project and its `papyrus-edge` network before deploying
these services. Keep domain values aligned with `edge.env`; the ACME contact email
belongs there. The public landing page is a separate Compose project owned by the
website repository and is not started, stopped or mounted by this deployment.

## First deployment

Check out the server release's source so PowerSync config and migrations match
the container version. Run these commands from `server/deploy` on the VM:

```sh
cp production.env.example production.env
chmod 600 production.env
mkdir -p secrets web
chmod 700 secrets
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out secrets/powersync-private.pem
openssl pkey -in secrets/powersync-private.pem -pubout -out secrets/powersync-public.pem
sudo chown 10001:10001 secrets/powersync-private.pem secrets/powersync-public.pem
chmod 400 secrets/powersync-private.pem
chmod 444 secrets/powersync-public.pem
```

Edit `production.env` locally on the VM. The domain, `APP_PUBLIC_BASE_URL`, CORS and allowed web redirect hosts already
match `papyrus-reader.com`; keep these aligned if you change a hostname. Generate
**separate** values for `SECRET_KEY`, `POSTGRES_PASSWORD`,
`POWERSYNC_SOURCE_PASSWORD` and `POWERSYNC_STORAGE_PASSWORD` using
`openssl rand -hex 32`. Database values must be URL-safe because connection URLs
are assembled from them. Do not rotate the JWT private key on ordinary deploys.

Set `FORWARDED_ALLOW_IPS` to the Caddy container's address on `papyrus-edge`:

```sh
docker inspect papyrus-edge-proxy-1 --format '{{(index .NetworkSettings.Networks "papyrus-edge").IPAddress}}'
```

Use that single private address in `production.env`, never `*` or the entire Docker
subnet. The API must remain private behind Caddy. Caddy supplies the client address
for IP-based authentication and OPDS relay limits. If the proxy is recreated with a
different address, update this value and recreate the API container too.

### Upload rate limits

Sync uploads and media uploads use the authenticated account ID after session
validation. Devices and sessions for one account share an allowance, while separate
accounts remain independent even behind the same proxy or home network.

The recommended initial values are `RATE_LIMIT_BATCH=300` and
`RATE_LIMIT_UPLOAD=60` per minute. A sync request contains one client transaction,
which can contain multiple row mutations. Offline backlogs naturally create bursts.
These values are starting limits, not measured capacity guarantees. Keep the
authentication limit at 5 and OPDS relay limit at 100 requests/minute per client IP.
The current limiter is in memory for the single API process. Use shared limiter
storage before scaling to multiple workers or replicas.

Existing deployments must update these two values explicitly in `production.env`.
Changing the example file does not change an installed configuration. Deploy the
released server image through the normal release process, then verify independent
accounts behind the proxy, 429 responses with `Retry-After`, and public probes.
The matching client suppresses repeated upload attempts during the cooldown and
leaves failed transactions queued for a later attempt. No migration or library reset
is required.

### Continue service setup

Configure a real SMTP provider with TLS, verified sender, and its credentials;
Mailpit is for local development. Add Google OAuth credentials if testing Google
sign-in. Extract the matching client's `web-release` artifact into `web/`, so
`web/index.html` exists, then run `python3 migrate_web_layout.py` to initialize
`web/current/index.html`. This preserves the flat files and snapshots the initial
release. Subsequent client releases activate versioned directories atomically. The mobile app does not require visiting the web app for
ordinary reading, but registration verification/reset emails use its routes.

If the GHCR package is private, authenticate Docker on the VM with a restricted
read-packages token; never reuse the CI publishing token. Ensure the requested
image has actually been published. Then:

```sh
./deploy.sh
```

The script validates settings without printing credentials, pulls images, waits
for both databases, stops app services, runs Alembic once, creates/updates the
restricted PowerSync replication role and publication, then starts services with
health checks. It intentionally causes a short maintenance window. If a migration
fails, services stay stopped; inspect the failure before restoring service.
Migrations do not run independently in every API replica.

Verify externally: `https://api.papyrus-reader.com/health`, `/openapi.json` (release version),
`https://sync.papyrus-reader.com/probes/liveness`, the web app, SMTP verification/reset, Google
sign-in, book/media upload and sync from a Play-installed Android build. Monitor
PowerSync replication slots: a 1 GB WAL retention cap protects disk but an
extended outage can invalidate a slot and require a controlled resync.

## Optional services

### OPDS relay

`OPDS_RELAY_ENABLED` enables catalog transport, including anonymous browsing.
Restrict it with `OPDS_RELAY_ALLOWED_HOSTS`, a JSON array of exact hostnames;
include any required redirect and cover-image hosts. An empty array permits all
public hosts. Private/LAN destinations remain blocked even when listed.

Use HTTPS and allow the web app's origin in `CORS_ORIGINS`. The selected backend
receives protected catalog credentials, so do not log relay request bodies or
credential-bearing URLs. The relay does not upload guest libraries. Rate and
concurrency limits are per API worker; apply aggregate limits before scaling out.

### Support submissions

Set `SUPPORT_GITHUB_TOKEN` to a fine-grained PAT restricted to the target repository
with Issues read/write and organization Projects read/write permissions. Attachment
uploads also require the token owner to have repository write access; GitHub App
installation tokens cannot upload them. Keep this token on the server. An empty
token disables submissions. Routing settings are listed in `production.env.example`.

Reports and attachments become public on GitHub. A timeout can leave a created
issue or uploaded attachment without a confirmed client response; check GitHub
before retrying. Project routing failures are logged with the issue number and
need manual reconciliation; they are not persisted for retry. A project's visible
`viewerCanUpdate` value alone does not prove the PAT has Projects write permission.

## Updating and recovery

Deploy the backward-compatible server first, then roll out the client. Update
`PAPYRUS_VERSION`, check out the corresponding source/config and install the web
artifact before running `./deploy.sh`. Check Alembic current/head before/after a
release and investigate `alembic check` differences. Do not downgrade migrations
or assume rolling back an image reverses a data migration.

The publication bootstrap reads the release SQL through standard input. A running
database container can retain an older inode for its individual file bind mount
after configuration replacement; do not execute the mounted file during upgrades.
Verify that `pg_publication_tables` includes every configured sync table.

Before each release, take a PostgreSQL dump of the application database and a
consistent media backup. Keep encrypted, off-host backups of the database, media,
production environment and JWT keys; perform an actual restore drill. The named
volumes retain application/PostgreSQL/PowerSync data across container replacement.
The shared proxy's separate volumes retain certificates. **Do not use
`docker compose down -v`** for upgrades.
VM snapshots alone are not a verified database/media backup. PowerSync storage
can be rebuilt, but doing so requires coordinated client resync.

For commands outside the script, always use:

```sh
docker compose --env-file production.env -f compose.yml ps
docker compose --env-file production.env -f compose.yml logs --tail 100 api powersync
```

Do not expose logs containing tokens or environment values when asking for help.
Verify public DNS, SMTP/OAuth, credentials and real-device sync/auth after
deployment; local container compilation is not a deployment test.

### Unattended release job

Install `release_job.py` and a launcher on the deployment host to allow the
workspace release runner to invoke this deployment. The release's `compose.yml`
must support `PAPYRUS_SERVER_IMAGE` for both the API and migrations. The server
checkout must have access to workspace `master`, the pinned server commit, and the published GHCR image. Use
the existing production checkout containing the ignored `deploy/production.env`,
secrets, and active web layout.

If production is populated from copied release files, keep a separate source
checkout and pass the live path as `--deployment-root`. The job compares the live
Compose, deployment scripts, and PowerSync files with the commit running in the
API container. It stops on local changes before replacing any release files.

Install the job and an executable launcher outside that checkout. For example,
`/usr/local/bin/papyrus-release-server` could contain:

```sh
#!/bin/sh
exec python3 /opt/papyrus/release_job.py \
  --checkout /srv/papyrus/server \
  --backup-check /opt/papyrus/backup-and-restore-check \
  >> /var/log/papyrus-release.log 2>&1
```

Point `--backup-check` at the host's executable backup procedure. It runs in the
current deployment directory before checkout, configuration changes, or migrations.
It receives the workspace manifest SHA, requested version, revision, and pinned image as JSON on standard
input. It must exit successfully only after taking the documented encrypted off-host
backup and checking restoration in an isolated environment. A nonzero exit aborts
the deployment. Keep the host log private and accessible to the release operator.

Octopus calls the launcher over the configured deployment SSH connection. The host job locks
the deployment, checks the requested commit against the approved workspace manifest, pulls and
checks the published image, and uses the release's `deploy.sh` with that digest.
It records the new `PAPYRUS_VERSION` in `production.env`. The workspace runner
checks the live API, container digest and revision, package version, and migration
heads after the job returns. If the SSH job fails, inspect the host log and current
deployment state before retrying or restoring service.

References:
- [Hetzner Cloud firewalls](https://docs.hetzner.com/cloud/firewalls/overview/)
- [Docker Compose deployment](https://docs.docker.com/compose/how-tos/production/)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)

## Updating the web delivery layout

For an existing deployment, run `python3 migrate_web_layout.py` from `deploy/`
before installing the updated Caddyfile. It leaves the running flat directory
intact. Copy the new Caddyfile into the existing bind-mounted file (preserve its
inode), then validate and reload only the web process:

```sh
docker compose --env-file production.env -f compose.yml exec -T web caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file production.env -f compose.yml exec -T web caddy reload --config /etc/caddy/Caddyfile
```

Keep the parent `web` directory mounted at `/srv/web`. Check `/` and `/login` after
migration. Client delivery credentials and the restricted receiver are documented
in the client's `docs/RELEASING.md`. This migration needs no database changes and
must not run the full server deployment script or restart API/PowerSync services.

## Observability

PowerSync exposes Prometheus metrics on private container port 9090. The shared
observability collector joins `papyrus-production_default` and scrapes that port.
Do not publish it on the host or add a public Caddy route. Applying this YAML
change only requires restarting PowerSync, followed by its readiness check.
The `PapyrusReader/observability` repository owns dashboards, collection, alerts,
and the bootstrap for restricted `papyrus_monitor` database roles.

## Google Books metadata

Set `GOOGLE_BOOKS_API_KEY` in the VM's `production.env` to enable Google Books
searches. Keep the key out of Flutter build definitions and client CI secrets.
Restrict it to the Books API in Google Cloud; a production-only key can also be
restricted to the server's outbound IP address. Recreate the API container after
changing its environment. Deploy the server endpoint before releasing clients
that use it. Self-hosted servers supply their own key; missing configuration
returns 503 and readers can select Open Library instead.

`GET /v1/metadata/google-books?q=...&page=1&isbn=false` is available to guests
with limits of 10 requests/minute and 60/hour per IP. Signed-in clients use
`/v1/metadata/google-books/authenticated`, which validates the existing session
and allows 30/minute and 300/hour per user. Both routes share
`METADATA_DAILY_REQUEST_LIMIT` (default 1000 requests/day, including cache hits).
Set this conservatively relative to the project's Google quota and other users
of that key. No automatic upstream retries consume extra quota.

Each page has 10 results. Public results are cached in memory for five minutes,
with a maximum of 256 pages; failures are not cached. Cache and rate-limit state
are process-local and reset on restart, matching the current single-process
server deployment. Multiple workers or replicas need shared rate-limit storage
before relying on this as a deployment-wide budget. Search terms are sent to the
selected Papyrus server and Google; no library records or user credentials are
forwarded to Google. Open Library searches remain direct from the client.
