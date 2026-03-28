# Flutter Authentication Integration

This guide describes how a Flutter app should integrate with the Papyrus server authentication that already exists in this backend.

It covers:

- Android
- iOS
- macOS
- Windows
- Linux
- Flutter web

Papyrus is the authentication authority. The Flutter app should not talk to Google directly as its main auth API. Instead:

- email/password uses Papyrus auth endpoints directly
- Google login uses a Papyrus-owned browser OAuth flow
- PowerSync uses Papyrus-issued PowerSync tokens after Papyrus authentication succeeds

## Architecture

Papyrus is designed for an offline-first client.

- The app can remain unauthenticated while the user is using only local features.
- The app authenticates only when the user enables cloud-backed features such as sync.
- The server owns all account state, sessions, refresh-token rotation, Google identity linking, and PowerSync token minting.
- Google is only an upstream identity provider. The Flutter app should never send Google access tokens or ID tokens to Papyrus APIs unless the backend contract explicitly changes in the future.

## Recommended Flutter Packages

Use these packages as the baseline:

- `dio` for API calls and interceptors
- `flutter_secure_storage` for storing the Papyrus refresh token on Android, iOS, macOS, Windows, and Linux
- `flutter_web_auth_2` for browser-based Google OAuth login and callback handling
- `app_links` only if you need deeper custom scheme or universal-link handling than `flutter_web_auth_2` already provides

If your app already standardizes on `http` instead of `dio`, the HTTP contract stays the same. The main reason to prefer `dio` here is interceptor support for bearer-token attachment and one-time refresh retry.

## Backend Endpoints

The Flutter app should integrate with these server endpoints:

- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `POST /v1/auth/logout-all`
- `GET /v1/auth/oauth/google/start`
- `POST /v1/auth/exchange-code`
- `POST /v1/auth/link/google/start`
- `POST /v1/auth/link/google/complete`
- `POST /v1/auth/powersync-token`
- `GET /v1/users/me`

Related endpoints that are usually needed in a real client flow:

- `POST /v1/auth/resend-verification`
- `POST /v1/auth/verify-email`
- `POST /v1/auth/forgot-password`
- `POST /v1/auth/reset-password`
- `POST /v1/users/me/change-password`

## Tokens And Storage

Papyrus returns:

- `access_token`: short-lived bearer token for normal API requests
- `refresh_token`: long-lived token used to get a new access token
- `expires_in`: access-token lifetime in seconds
- `user`: authenticated user profile

Recommended storage model:

- Keep `access_token` in memory only.
- Store `refresh_token` in `flutter_secure_storage` on Android, iOS, macOS, Windows, and Linux.
- On Flutter web, do not assume secure local storage is equivalent to native secure storage. Prefer in-memory access tokens and carefully managed refresh behavior. If you persist a refresh token in web storage, treat that as a weaker security posture and scope it accordingly.
- Every successful refresh rotates the refresh token. The client must overwrite the previously stored refresh token every time `POST /v1/auth/refresh` succeeds.

## Request And Response Shapes

### Register

`POST /v1/auth/register`

```json
{
  "email": "reader@example.com",
  "password": "SecureP@ss123",
  "display_name": "Reader",
  "client_type": "mobile",
  "device_label": "pixel-9"
}
```

Successful response:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque-token>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "user_id": "11111111-1111-1111-1111-111111111111",
    "email": "reader@example.com",
    "display_name": "Reader",
    "avatar_url": null,
    "email_verified": false,
    "created_at": "2026-03-28T12:00:00Z",
    "last_login_at": "2026-03-28T12:00:00Z"
  }
}
```

### Login

`POST /v1/auth/login`

```json
{
  "email": "reader@example.com",
  "password": "SecureP@ss123",
  "client_type": "desktop",
  "device_label": "macbook-air"
}
```

Response shape is the same as register.

### Refresh

`POST /v1/auth/refresh`

```json
{
  "refresh_token": "<stored-refresh-token>"
}
```

Response shape is also the same as register. The returned `refresh_token` replaces the old one.

### Exchange Papyrus Browser Code

After a successful Google browser flow, Papyrus redirects back to the app with a Papyrus one-time `code`. The app then calls:

`POST /v1/auth/exchange-code`

```json
{
  "code": "<papyrus-exchange-code>",
  "client_type": "web",
  "device_label": "chrome"
}
```

Response shape is the same as register.

### Current User

`GET /v1/users/me`

Requires:

```http
Authorization: Bearer <access_token>
```

Response:

```json
{
  "user_id": "11111111-1111-1111-1111-111111111111",
  "email": "reader@example.com",
  "display_name": "Reader",
  "avatar_url": null,
  "email_verified": true,
  "created_at": "2026-03-28T12:00:00Z",
  "last_login_at": "2026-03-28T12:15:00Z"
}
```

### PowerSync Token

`POST /v1/auth/powersync-token`

Requires bearer auth.

Response:

```json
{
  "token": "<powersync-jwt>",
  "expires_in": 300
}
```

## Flutter Client Structure

Keep the client split into a few clear responsibilities.

### `AuthRepository`

The repository should own:

- register
- login
- refresh
- logout
- logout all
- Google login start and exchange-code completion
- Google link start and complete
- fetch current user
- fetch PowerSync token

### `TokenStore`

The token store should own:

- current in-memory `accessToken`
- persisted `refreshToken`
- loading the persisted refresh token at app startup
- replacing the stored refresh token after refresh
- clearing both tokens on sign-out or unrecoverable auth failure

### Auth State

Use an explicit auth state model instead of only checking whether a token exists.

Recommended states:

- `signedOut`
- `authenticating`
- `signedIn`
- `refreshing`
- `authError`

At minimum, `signedIn` should hold:

- current user
- current access token in memory
- current refresh token presence

## Email And Password Flow

### Register

1. User enables cloud features and chooses sign-up.
2. App calls `POST /v1/auth/register`.
3. App keeps `access_token` in memory.
4. App stores `refresh_token` in secure storage on native/desktop.
5. App transitions to authenticated state and may immediately call `GET /v1/users/me`.

### Login

1. App calls `POST /v1/auth/login`.
2. Store tokens the same way as register.
3. Treat the returned `user` as the initial authenticated profile.

### Refresh Behavior

The HTTP client should:

1. attach `Authorization: Bearer <access_token>` to protected requests
2. if a protected request returns `401`, attempt exactly one refresh
3. call `POST /v1/auth/refresh` with the stored refresh token
4. replace both in-memory access token and stored refresh token with the returned values
5. retry the original request once
6. if refresh fails, clear tokens and transition to signed-out

Do not allow multiple simultaneous refresh operations. Use a single in-flight refresh guard so concurrent `401` responses wait on the same refresh result.

### Logout

Use:

- `POST /v1/auth/logout` to invalidate the current session
- `POST /v1/auth/logout-all` to invalidate all sessions

After either call:

- clear in-memory access token
- clear stored refresh token
- transition to signed-out

## Google OAuth Flow

Papyrus uses a server-owned browser flow.

The Flutter app should not use `google_sign_in` as the primary integration path here. The server already owns:

- the Google client ID and secret
- the Google callback
- identity verification
- account linking rules

### Native And Desktop Flow

Use a callback URI owned by the app, for example:

- `papyrus://auth/callback`

Recommended flow with `flutter_web_auth_2`:

1. Build the Papyrus start URL:
   - `GET /v1/auth/oauth/google/start?redirect_uri=papyrus://auth/callback`
2. Open that URL in the system browser with `flutter_web_auth_2`.
3. Google authenticates the user.
4. Papyrus receives the Google callback at `/v1/auth/oauth/google/callback`.
5. Papyrus redirects to `papyrus://auth/callback?code=<papyrus-code>` or `papyrus://auth/callback?error=<error>`.
6. The app extracts `code` from the callback URL.
7. The app calls `POST /v1/auth/exchange-code`.
8. Papyrus returns normal auth tokens and the user profile.

Example Dart shape:

```dart
final result = await FlutterWebAuth2.authenticate(
  url: '$baseUrl/v1/auth/oauth/google/start?redirect_uri=${Uri.encodeComponent('papyrus://auth/callback')}',
  callbackUrlScheme: 'papyrus',
);

final callbackUri = Uri.parse(result);
final code = callbackUri.queryParameters['code'];
final error = callbackUri.queryParameters['error'];
```

If `error` is present, treat the login as failed and do not call `/v1/auth/exchange-code`.

### Flutter Web Flow

For Flutter web, the callback should be owned by the web app, for example:

- `https://app.example.com/auth/callback`

The flow is the same, except the app callback is an HTTPS route in the web app instead of a custom scheme.

1. User clicks “Continue with Google”.
2. App navigates the browser to:
   - `GET /v1/auth/oauth/google/start?redirect_uri=https://app.example.com/auth/callback`
3. After Google auth, Papyrus redirects to:
   - `https://app.example.com/auth/callback?code=<papyrus-code>`
4. The Flutter web route reads the `code`.
5. The app calls `POST /v1/auth/exchange-code`.
6. The app stores tokens according to the web storage policy chosen by the app.

Important distinction:

- the Flutter app callback URI is the `redirect_uri` query parameter passed to Papyrus
- the Google redirect URI configured in Google Cloud must point to the Papyrus backend callback
- these are different URLs and should not be confused

### Backend And Google Configuration

The backend callback is always the Papyrus server callback:

- `https://<server-host>/v1/auth/oauth/google/callback`

That server callback must match:

- `PUBLIC_BASE_URL`
- the Google Cloud OAuth redirect URI configuration

The Flutter app callback must not be registered as the Google redirect URI. Papyrus redirects to the app callback only after Papyrus has already completed the Google exchange.

## Google Account Linking

Linking Google to an existing Papyrus account requires an already authenticated Papyrus session.

Flow:

1. App is already signed in with Papyrus.
2. App calls `POST /v1/auth/link/google/start` with:

```json
{
  "redirect_uri": "papyrus://auth/callback"
}
```

3. Papyrus returns:

```json
{
  "authorization_url": "https://accounts.google.com/..."
}
```

4. App opens `authorization_url` in the browser.
5. After browser completion, Papyrus redirects back to the app with a one-time Papyrus `code`.
6. App calls `POST /v1/auth/link/google/complete` with that code.

```json
{
  "code": "<papyrus-link-code>"
}
```

Important rule:

- Papyrus does not auto-link by email

If a Google account has the same email as an existing Papyrus account but is not linked yet, the user must first authenticate to Papyrus and then explicitly link Google.

## PowerSync Integration

After Papyrus authentication succeeds, the app should fetch a PowerSync token from Papyrus:

- `POST /v1/auth/powersync-token`

Use the Papyrus access token to authenticate that request.

The PowerSync token is separate from the Papyrus API access token:

- Papyrus API token authenticates calls to the Papyrus backend
- PowerSync token authenticates calls to PowerSync

Do not send Google tokens directly to PowerSync.

Recommended client behavior:

- request a fresh PowerSync token on PowerSync startup
- refresh it when PowerSync needs new credentials
- keep PowerSync token handling separate from the main Papyrus refresh-token flow

## Failure Handling

The Flutter app should handle these cases explicitly.

### Protected API Returns `401`

- try refresh once
- if refresh succeeds, retry the failed request once
- if refresh fails, clear tokens and sign the user out

### Session Revocation

Papyrus validates sessions server-side. Existing access tokens can stop working immediately after:

- logout
- logout all
- password change
- password reset
- account disablement

The client should treat `401` or `403` after those operations as expected behavior, not as a transport problem.

### Google Flow Returns `error`

If the app callback contains:

- `?error=...`

then the app should:

- surface a user-friendly auth failure
- not call `/v1/auth/exchange-code`
- keep the current auth state unchanged unless the login flow was replacing an existing session intentionally

### Refresh Rotation

Refresh tokens rotate. If the app fails to persist the newly returned refresh token, the next refresh may fail because the previously stored token is stale.

This is one of the most important client integration details in this auth design.

## Local Development

For local backend testing:

- API docs: `http://localhost:8080/docs`
- auth sandbox: `http://localhost:8080/__dev/auth-sandbox`
- Mailpit: `http://localhost:8025`

Use the sandbox to verify:

- email/password register and login
- Google browser login
- exchange-code to tokens
- refresh rotation
- `/users/me`
- PowerSync token minting

See [`auth-testing.md`](auth-testing.md) for:

- local server setup
- Mailpit and SMTP testing
- Google Cloud setup for the backend callback
- provider smoke tests

## Suggested Flutter Integration Order

Implement the client in this order:

1. email/password register and login
2. token store and refresh interceptor
3. `/users/me` bootstrap on app launch
4. logout and logout-all
5. Google browser login with exchange-code completion
6. Google account linking
7. PowerSync token integration

This keeps the auth foundation simple before layering in browser and sync-specific behavior.
