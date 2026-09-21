# OPDS relay

`POST /v1/opds/relay` retrieves catalog feeds, search descriptions, covers, and book files. The route is anonymous and does not access the database or persistent file storage. The client imports the returned bytes into its selected local or signed-in library. Deploy the client and backend changes together.

The default API prefix is `/v1`; use the configured `API_PREFIX` if different. A request contains:

```json
{
  "url": "https://www.gutenberg.org/ebooks/1342.epub.noimages",
  "catalog_url": "https://www.gutenberg.org/ebooks/1342.opds",
  "max_bytes": 268435456
}
```

Protected catalogs may include `credentials: {"username": "reader", "password": "..."}` in the JSON body. The relay sends HTTP Basic credentials only to the `catalog_url` origin. It never forwards incoming Authorization or Cookie headers, upstream cookies, or upstream error-page bodies. Request bodies and credential-bearing URLs must not be captured by proxy/access logging.

Successful responses stream the bytes with the upstream Content-Type, an optional Content-Length, and `X-OPDS-URL` containing the final upstream URL. Redirects are followed on the server, with validation at each hop. The client uses that final URL to resolve relative catalog links. Responses use `Cache-Control: no-store`, attachment disposition, `nosniff`, and a sandbox CSP. Errors use the existing API error envelope with code `OPDS_RELAY_ERROR`; temporary capacity errors include `details.retryable: true`.

## Configuration

- `OPDS_RELAY_ENABLED`: defaults to `true`; set `false` to disable this feature.
- `OPDS_RELAY_ALLOWED_HOSTS`: defaults to `[]`, allowing public hosts. Set a JSON array of exact hostnames to restrict access, for example `["www.gutenberg.org"]`. Include image/CDN/redirect hosts required by the permitted catalogs. Private addresses remain blocked even for listed hosts.
- `RATE_LIMIT_GENERAL`: existing per-IP request limit, also applied to the relay.
- `CORS_ORIGINS`: include the web app origin. `X-OPDS-URL` is exposed to browser clients by the API middleware. The client uses `PAPYRUS_API_BASE_URL` or the selected custom backend independently of login.

Use HTTPS for deployed app-to-backend connections and protected upstream catalogs. The backend is trusted with catalog credentials. Configuring this relay does not enable PowerSync, require guest registration, or upload guest books.

## Bounds and operation

Only HTTP/HTTPS URLs without embedded credentials are accepted. Every resolved address must be public; private, loopback, link-local, shared, multicast, and IPv4-mapped IPv6 destinations are rejected. Each connection is pinned to a validated IP while retaining the original HTTP Host and TLS hostname verification. HTTPS-to-HTTP redirects are rejected. The relay does not use environment HTTP proxies.

Each worker allows eight concurrent resources and eight DNS operations. The client shares a four-request queue across browsing, covers, and downloads, and retries temporary relay-capacity errors at most twice. The existing in-memory rate limiter and concurrency limits are per worker; deployments with multiple replicas should apply aggregate quotas at their reverse proxy if needed.

Resources default to 8 MiB and cannot exceed 256 MiB. The service enforces both declared and actual byte counts, permits at most five redirects, bounds DNS and response opening to 30 seconds each, and interrupts stalled reads after 30 seconds. A five-minute deadline closes each resource even if a downstream client stops consuming it. OS DNS calls cannot be cancelled, so timed-out lookups continue in a bounded executor without admitting unlimited resolver work.

Connections close on completion, disconnect, and failure. A limit or network error after response headers have been sent aborts the stream; the client treats that as an interrupted download and does not commit an incomplete import. Compression is requested as `identity`; unexpected upstream content encodings are rejected. The relay retrieves complete resources, without range/resume support or shared caching.

Private LAN catalogs are deliberately unavailable through this anonymous relay. Authentication, licensing, and DRM requirements of upstream catalogs still apply.

## Verification

```sh
uv run pytest tests/services/test_opds.py tests/api/routes/test_opds.py
uv run ruff check papyrus/services/opds.py papyrus/api/routes/opds.py papyrus/schemas/opds.py
uv run mypy papyrus/services/opds.py papyrus/api/routes/opds.py papyrus/schemas/opds.py
```

Coverage includes destination validation, pinned TLS identity, scoped credentials, redirect limits, known/unknown size limits, truncated streams, slow DNS/headers/chunk framing, idle stream expiry, anonymous rate limiting, CORS metadata, and disconnect cleanup. The route tests do not need a running database.
