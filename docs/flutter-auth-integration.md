# Flutter Auth And PowerSync Integration

Use this guide to connect the Flutter client to Papyrus authentication and
PowerSync.

## Overview

- Offline mode is local-only and does not create a server session.
- Email/password auth uses Papyrus auth endpoints.
- Google auth uses a Papyrus-owned browser OAuth flow.
- The Flutter client stores Papyrus refresh tokens and keeps access tokens in
  memory.
- PowerSync receives short-lived JWTs from Papyrus.
- Local PowerSync writes upload through the Papyrus server.

## Runtime Flow

1. Flutter builds `PapyrusApiConfig.fromEnvironment()`.
2. `AuthProvider.bootstrap()` asks `AuthRepository` for a stored refresh token.
3. With a stored refresh token, Flutter calls `POST /v1/auth/refresh`.
4. After auth succeeds, `main.dart` connects `PapyrusPowerSyncService`.
5. PowerSync calls `PapyrusPowerSyncConnector.fetchCredentials()`.
6. The connector calls `POST /v1/auth/powersync-token`.
7. PowerSync connects to `POWERSYNC_SERVICE_URL` with the Papyrus-issued JWT.
8. PowerSync reads user-scoped rows from Postgres through
   `server/powersync/sync-config.yaml`.
9. Local PowerSync writes upload through `POST /v1/sync/powersync-upload`.
10. The server validates ownership and writes the mutations to Postgres.

## Offline Mode

`AuthProvider.setOfflineMode(true)` clears Papyrus tokens and loads local data.
PowerSync disconnects and clears the synced local database. App routes stay
available through `AuthProvider.isOfflineMode`.

## Email And Password Auth

Login:

```http
POST /v1/auth/login
```

```json
{
  "email": "reader@example.com",
  "password": "SecureP@ss123",
  "client_type": "mobile",
  "device_label": "flutter-android"
}
```

Registration uses `POST /v1/auth/register` with the same client metadata and a
`display_name`.

Auth responses include:

- `access_token`: Papyrus bearer token kept in memory.
- `refresh_token`: opaque token stored by `TokenStore`.
- `expires_in`: access token lifetime in seconds.
- `user`: authenticated user profile.

## Google Auth

Flutter opens the Papyrus OAuth start URL:

```text
GET /v1/auth/oauth/google/start?redirect_uri=<app-callback-uri>
```

Google returns to Papyrus:

```text
GET /v1/auth/oauth/google/callback
```

Papyrus redirects back to the Flutter callback with a one-time code. Flutter
exchanges that code for Papyrus tokens:

```http
POST /v1/auth/exchange-code
```

```json
{
  "code": "<papyrus-code>",
  "client_type": "web",
  "device_label": "flutter-web"
}
```

Flutter callback URIs:

- Mobile: `papyrus://auth/callback`
- Linux and Windows: `http://localhost:43821/auth/callback`
- Web: `<current-origin>/auth/callback`

Google Cloud redirect URI:

```text
http://localhost:8080/v1/auth/oauth/google/callback
```

## Token Refresh

`AuthRepository` owns token refresh.

- `bootstrap()` refreshes during startup with the stored refresh token.
- `createPowerSyncToken()` retries once after a Papyrus `401`.
- `uploadPowerSyncBatch()` retries once after a Papyrus `401`.
- Successful refresh responses replace the stored refresh token.
- Failed refresh clears tokens and signs the user out.

## PowerSync Tokens

Flutter requests a PowerSync JWT from Papyrus:

```http
POST /v1/auth/powersync-token
Authorization: Bearer <papyrus-access-token>
```

```json
{
  "token": "<powersync-jwt>",
  "expires_in": 300
}
```

Papyrus signs PowerSync tokens with RS256:

- `sub`: Papyrus user id.
- `aud`: `POWERSYNC_JWT_AUDIENCE`.
- `type`: `powersync`.
- `iat`: issued-at timestamp.
- `exp`: expiration timestamp.
- `kid`: `POWERSYNC_JWT_KEY_ID` header.

PowerSync validates tokens through:

```http
GET /v1/auth/jwks
```

## PowerSync Data Flow

Pull:

1. PowerSync validates the client JWT.
2. `sync-config.yaml` selects rows with
   `WHERE owner_user_id::text = auth.user_id()`.
3. PowerSync sends user rows to the Flutter local PowerSync database.
4. Flutter watches local tables and updates `DataStore`.

Upload:

1. Flutter writes to the local PowerSync database.
2. PowerSync queues CRUD mutations.
3. `PapyrusPowerSyncConnector.uploadData()` reads queued transactions.
4. The connector posts the batch to `POST /v1/sync/powersync-upload`.
5. The server applies supported mutations after ownership checks.
6. PowerSync replication sends committed changes to connected clients.

Synced source tables:

- `books`
- `annotations`
- `reading_sessions`

## Local Flutter Command

Run from `client/app/`:

```bash
flutter run -d chrome --web-hostname papyrus.localhost --web-port 3000 --dart-define-from-file=.dart_defines
```

## Related Docs

- [Authentication testing](auth-testing.md)
- [PowerSync sandbox](powersync-sandbox.md)
