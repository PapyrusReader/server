# Single-server production deployment

This is a portable Docker Compose deployment for a Linux VM, including Hetzner
Cloud. It is separate from the local `docker-compose.yml`. Nothing here provisions
or changes a live server automatically.

The API and migration job use `ghcr.io/papyrusreader/server:<version>`, built for
amd64 and arm64 when the committed server version changes on `master`. API version
metadata comes from the installed Python package. GitHub releases record the
image digest; deploy a recorded digest instead of a mutable tag when stronger
artifact pinning is needed. The runtime uses UID/GID 10001, not root.

## Branch workflow

Feature/fix PRs target the default `development` branch and leave versions
unchanged. CI checks integration work without publishing a container. When ready,
prepare the coordinated version bump on `development`, then promote it to
`master` with a release PR using **Create a merge commit**. The existing version
gate publishes only from `master`. Bring `master` back into `development` after
the release, and deploy additive server changes before a client that needs them.
See the workspace `RELEASING.md` for coordination and merge order.

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
Its configuration is in the workspace repository's `deploy/edge` directory and
uses `papyrus-api:8080`, `papyrus-sync:8080` and `papyrus-app:8080` as upstreams.
The `web` service here serves the built Flutter app over internal HTTP, including
verification/password-reset routes. The client
release environment must use the same API/sync origins. Set the Google OAuth web
client's authorized redirect URI to
`https://api.papyrus-reader.com/v1/auth/oauth/google/callback`; mobile callbacks remain
`papyrus://auth/callback`.

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

## Updating and recovery

The initial testing baseline is `0.0.1`. Its release workflow removes the unused
`1.0.0` container by its exact manifest digest after publishing the replacement.
It preserves shared manifests and refuses to delete versions with unrelated tags.
The package itself stays public. This cleanup uses the publishing repository's
package admin access through `GITHUB_TOKEN` and only runs for `0.0.1`.

Deploy the backward-compatible server first, then roll out the client. Update
`PAPYRUS_VERSION`, check out the corresponding source/config and install the web
artifact before running `./deploy.sh`. Check Alembic current/head before/after a
release and investigate `alembic check` differences. Do not downgrade migrations
or assume rolling back an image reverses a data migration.

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
The first release still needs public DNS, SMTP/OAuth setup, credentials and a
real-device sync/auth test; local container compilation is not a deployment test.

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
