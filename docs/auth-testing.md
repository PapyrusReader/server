# Authentication Testing Setup

This repo can now support a full local auth test loop:

- email/password login
- refresh and logout flows
- verification and password-reset emails
- PowerSync token minting
- Google OAuth browser flow after you add Google credentials

For Flutter client integration guidance, see [`flutter-auth-integration.md`](flutter-auth-integration.md).

## Local Setup

1. Start local dependencies:

```bash
docker compose up -d database mailpit
```

1. Generate local PowerSync signing keys:

```bash
./scripts/generate_dev_powersync_keys.sh
```

1. Make sure `.env` contains the auth values shown in `.env.example`.

Recommended local values when the API runs on the host with `uvicorn`:

```dotenv
PUBLIC_BASE_URL=http://localhost:8080
EMAIL_DELIVERY_ENABLED=true
SMTP_HOST=127.0.0.1
SMTP_PORT=1025
SMTP_USE_TLS=false
SMTP_USE_SSL=false
SMTP_FROM_EMAIL=noreply@papyrus.local
SMTP_FROM_NAME=Papyrus
POWERSYNC_JWT_PRIVATE_KEY_FILE=.local/powersync/private.pem
POWERSYNC_JWT_PUBLIC_KEY_FILE=.local/powersync/public.pem
POWERSYNC_JWT_KEY_ID=papyrus-powersync-dev
POWERSYNC_JWT_AUDIENCE=powersync-dev
```

If the API runs inside Docker instead of on the host, set `SMTP_HOST=mailpit` and `POSTGRES_HOST=database`.

1. Apply migrations and run the API:

```bash
uv run alembic upgrade head
uv run uvicorn papyrus.main:app --reload
```

## Useful Local Pages

- API index: `http://localhost:8080/`
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
- Dev auth sandbox: `http://localhost:8080/__dev/auth-sandbox`
- Mailpit inbox UI: `http://localhost:8025`

## SMTP End-to-End Testing

Mailpit is a local SMTP sink. No real mailbox is needed.

- Trigger `forgot password` or `resend verification` from the sandbox or API.
- Open `http://localhost:8025` to inspect the delivered messages.
- For the opt-in smoke test, use any recipient address:

```bash
RUN_SMTP_SMOKE_TEST=true \
AUTH_SMOKE_EMAIL_RECIPIENT=smoke@papyrus.local \
uv run pytest tests/integration/test_auth_smoke.py -m auth_smoke -q
```

## Google OAuth Setup

Papyrus uses a server-owned browser OAuth flow. The Flutter app opens:

- `GET /v1/auth/oauth/google/start`

Google redirects back to the server callback:

- `GET /v1/auth/oauth/google/callback`

The server then redirects to your app callback URI with a one-time Papyrus exchange code.

### What To Create In Google Cloud

Create an OAuth client with:

- Client type: `Web application`
- Redirect URI:
  - local desktop testing: `http://localhost:8080/v1/auth/oauth/google/callback`
  - public tunnel/device testing: `https://<your-public-host>/v1/auth/oauth/google/callback`

Authorized JavaScript origins are not required for this backend-owned redirect flow. If the Google UI requires one for localhost testing, use:

- `http://localhost:8080`

Set the resulting values in `.env`:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
PUBLIC_BASE_URL=http://localhost:8080
```

For mobile-device testing or any device where the browser cannot reach your workstation as `localhost`, use a public HTTPS base URL and set `PUBLIC_BASE_URL` to that exact value.

### Localhost vs Public Testing

- Desktop same-machine testing:
  - `PUBLIC_BASE_URL=http://localhost:8080`
  - Google redirect URI: `http://localhost:8080/v1/auth/oauth/google/callback`
- Mobile emulator, physical phone, or shared test device:
  - expose the backend through a public HTTPS URL
  - set `PUBLIC_BASE_URL=https://<your-public-host>`
  - Google redirect URI: `https://<your-public-host>/v1/auth/oauth/google/callback`

The callback URI must match Google exactly, including scheme, host, port, path, and trailing slash behavior.

### OAuth Consent Screen Notes

For development:

- keep the app in testing mode
- add your Google account under test users if Google requires it

Papyrus only requests basic identity scopes:

- `openid`
- `email`
- `profile`

## Google Smoke Test

The Google smoke test now validates a live Papyrus session produced by a successful Google browser login.

Recommended workflow:

1. Complete a real Google login in the auth sandbox.
2. Copy the access token or refresh token from the sandbox.
3. Run the smoke test against the running server.

Access-token-only mode:

```bash
RUN_GOOGLE_SMOKE_TEST=true \
AUTH_SMOKE_SERVER_BASE_URL=http://localhost:8080 \
AUTH_SMOKE_GOOGLE_ACCESS_TOKEN=<access-token-from-sandbox> \
uv run pytest tests/integration/test_auth_smoke.py -m auth_smoke -q
```

Refresh-token mode is more durable and also validates token rotation:

```bash
RUN_GOOGLE_SMOKE_TEST=true \
AUTH_SMOKE_SERVER_BASE_URL=http://localhost:8080 \
AUTH_SMOKE_GOOGLE_REFRESH_TOKEN=<refresh-token-from-sandbox> \
uv run pytest tests/integration/test_auth_smoke.py -m auth_smoke -q
```

If both are provided, the test tries the access token first and falls back to refresh if the access token is expired.

Notes:

- refresh-token mode rotates the provided refresh token, so the old token will stop working after the test
- on success, the test prints `AUTH_SMOKE_ROTATED_REFRESH_TOKEN=...`; use that value for the next manual run
- if you only provide an access token, the test is non-destructive but depends on that token still being unexpired
- `AUTH_SMOKE_SERVER_BASE_URL` defaults to `PUBLIC_BASE_URL` if omitted
