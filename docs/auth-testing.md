# Authentication Testing

Use this runbook to test Papyrus auth, local email delivery, and Google OAuth.

## Setup

Create `.env` from `.env.example` and set these values:

```dotenv
PUBLIC_BASE_URL=http://localhost:8080
EMAIL_DELIVERY_ENABLED=true
SMTP_HOST=127.0.0.1
SMTP_PORT=1025
SMTP_USE_TLS=false
SMTP_USE_SSL=false
SMTP_FROM_EMAIL=noreply@papyrus.local
SMTP_FROM_NAME=Papyrus
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
OAUTH_ALLOWED_REDIRECT_SCHEMES=["papyrus"]
OAUTH_ALLOWED_REDIRECT_HOSTS=["localhost","127.0.0.1"]
POWERSYNC_JWT_PRIVATE_KEY_FILE=.local/powersync/private.pem
POWERSYNC_JWT_PUBLIC_KEY_FILE=.local/powersync/public.pem
POWERSYNC_JWT_KEY_ID=papyrus-powersync-dev
POWERSYNC_JWT_AUDIENCE=powersync-dev
```

Run from `server/`:

```bash
uv sync --extra dev
./scripts/generate_dev_powersync_keys.sh
docker compose up -d database mailpit
uv run alembic upgrade head
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
npm --prefix frontend/dev-pages install
npm --prefix frontend/dev-pages run dev
```

## Local Pages

- Auth sandbox: `http://localhost:8080/__dev/auth-sandbox`
- Mailpit inbox: `http://localhost:8025`
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`

## Email Testing

Use the auth sandbox to trigger:

- registration verification email
- resend verification email
- password reset email

Open `http://localhost:8025` and inspect the delivered messages.

Run the SMTP smoke test:

```bash
RUN_SMTP_SMOKE_TEST=true \
AUTH_SMOKE_EMAIL_RECIPIENT=smoke@papyrus.local \
uv run pytest tests/integration/test_auth_smoke.py -m auth_smoke -q
```

## Google OAuth

Create a Google OAuth client:

- Application type: `Web application`
- Authorized redirect URI:
  `http://localhost:8080/v1/auth/oauth/google/callback`

Keep the OAuth consent screen in testing mode and add the testing Google
account as a test user.

Papyrus requests these scopes:

- `openid`
- `email`
- `profile`

Test the browser flow:

1. Open `http://localhost:8080/__dev/auth-sandbox`.
2. Start Google login.
3. Complete the Google browser flow.
4. Copy the refresh token from the sandbox.
5. Run the smoke test:

```bash
RUN_GOOGLE_SMOKE_TEST=true \
AUTH_SMOKE_SERVER_BASE_URL=http://localhost:8080 \
AUTH_SMOKE_GOOGLE_REFRESH_TOKEN=<refresh-token-from-sandbox> \
uv run pytest tests/integration/test_auth_smoke.py -m auth_smoke -q
```

The smoke test prints `AUTH_SMOKE_ROTATED_REFRESH_TOKEN=...`. Use that refresh
token for the next run.
