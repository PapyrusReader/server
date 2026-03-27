"""Development-only authentication sandbox routes."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from papyrus.api.deps import get_current_access_token_payload, get_current_auth_session
from papyrus.config import get_settings
from papyrus.models import AuthSession

router = APIRouter(tags=["Dev"])
CurrentAuthSession = Annotated[AuthSession, Depends(get_current_auth_session)]
CurrentAccessPayload = Annotated[dict[str, Any], Depends(get_current_access_token_payload)]


def _sandbox_html(request: Request) -> str:
    api_prefix = get_settings().api_prefix
    exchange_url = f"{api_prefix}/auth/exchange-code"
    register_url = f"{api_prefix}/auth/register"
    login_url = f"{api_prefix}/auth/login"
    refresh_url = f"{api_prefix}/auth/refresh"
    logout_url = f"{api_prefix}/auth/logout"
    logout_all_url = f"{api_prefix}/auth/logout-all"
    google_start_url = f"{api_prefix}/auth/oauth/google/start"
    google_link_start_url = f"{api_prefix}/auth/link/google/start"
    google_link_complete_url = f"{api_prefix}/auth/link/google/complete"
    forgot_password_url = f"{api_prefix}/auth/forgot-password"
    resend_verification_url = f"{api_prefix}/auth/resend-verification"
    verify_email_url = f"{api_prefix}/auth/verify-email"
    reset_password_url = f"{api_prefix}/auth/reset-password"
    powersync_url = f"{api_prefix}/auth/powersync-token"
    me_url = f"{api_prefix}/users/me"
    session_url = request.url_for("auth_sandbox_session")

    config = {
        "registerUrl": register_url,
        "loginUrl": login_url,
        "refreshUrl": refresh_url,
        "logoutUrl": logout_url,
        "logoutAllUrl": logout_all_url,
        "googleStartUrl": google_start_url,
        "googleLinkStartUrl": google_link_start_url,
        "googleLinkCompleteUrl": google_link_complete_url,
        "exchangeUrl": exchange_url,
        "forgotPasswordUrl": forgot_password_url,
        "resendVerificationUrl": resend_verification_url,
        "verifyEmailUrl": verify_email_url,
        "resetPasswordUrl": reset_password_url,
        "powersyncUrl": powersync_url,
        "meUrl": me_url,
        "sessionUrl": str(session_url),
        "redirectUri": str(request.url.replace(query="")),
    }

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Papyrus Auth Sandbox</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f7f1e7;
        --panel: #fffdf8;
        --ink: #1e1b18;
        --muted: #6e6259;
        --line: #dbcdbf;
        --accent: #7d4f2b;
        --accent-strong: #5f391d;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: "Iowan Old Style", "Palatino Linotype", serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, #fff6df 0, transparent 35%),
          linear-gradient(180deg, #f4ecdf 0%, var(--bg) 100%);
      }}
      main {{
        max-width: 1200px;
        margin: 0 auto;
        padding: 32px 20px 48px;
      }}
      h1, h2 {{ margin: 0 0 12px; }}
      p {{ margin: 0 0 16px; color: var(--muted); }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 16px;
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 18px;
        box-shadow: 0 10px 30px rgba(72, 54, 39, 0.08);
      }}
      label {{
        display: block;
        margin: 12px 0 6px;
        font-size: 0.92rem;
        color: var(--muted);
      }}
      input, textarea, button {{
        width: 100%;
        border-radius: 12px;
        border: 1px solid var(--line);
        padding: 10px 12px;
        font: inherit;
        background: #fff;
      }}
      textarea {{
        min-height: 120px;
        resize: vertical;
      }}
      button {{
        margin-top: 10px;
        cursor: pointer;
        background: var(--accent);
        color: #fff;
        border: 0;
      }}
      button.secondary {{
        background: #efe4d4;
        color: var(--ink);
      }}
      .inline {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }}
      .inline button {{
        flex: 1 1 140px;
      }}
      pre {{
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
      }}
      code {{
        font-family: "SFMono-Regular", "Menlo", monospace;
      }}
    </style>
  </head>
  <body>
    <main>
      <div class="panel">
        <h1>Auth Sandbox</h1>
        <p>Dev-only page for manually exercising the Papyrus auth flows against this backend.</p>
      </div>

      <div class="grid">
        <section class="panel">
          <h2>Account</h2>
          <label for="email">Email</label>
          <input id="email" type="email" value="sandbox@example.com" />
          <label for="password">Password</label>
          <input id="password" type="password" value="SecureP@ss123" />
          <label for="new-password">New Password</label>
          <input id="new-password" type="password" value="NewSecureP@ss123" />
          <label for="display-name">Display Name</label>
          <input id="display-name" type="text" value="Sandbox User" />
          <label for="client-type">Client Type</label>
          <input id="client-type" type="text" value="web" />
          <label for="device-label">Device Label</label>
          <input id="device-label" type="text" value="auth-sandbox" />
          <div class="inline">
            <button id="register">Register</button>
            <button id="login">Login</button>
            <button id="refresh">Refresh</button>
          </div>
          <div class="inline">
            <button id="logout" class="secondary">Logout</button>
            <button id="logout-all" class="secondary">Logout All</button>
          </div>
        </section>

        <section class="panel">
          <h2>OAuth And Email</h2>
          <label for="exchange-code">Exchange Code</label>
          <input id="exchange-code" type="text" />
          <div class="inline">
            <button id="google-login">Start Google Login</button>
            <button id="google-link" class="secondary">Start Google Link</button>
          </div>
          <div class="inline">
            <button id="exchange">Exchange Code</button>
            <button id="complete-link" class="secondary">Complete Link</button>
          </div>
          <div class="inline">
            <button id="resend-verification">Resend Verification</button>
            <button id="forgot-password" class="secondary">Forgot Password</button>
          </div>
          <label for="verification-token">Verification/Reset Token</label>
          <input id="verification-token" type="text" />
          <div class="inline">
            <button id="verify-email">Verify Email</button>
            <button id="reset-password" class="secondary">Reset Password</button>
          </div>
        </section>

        <section class="panel">
          <h2>Protected Calls</h2>
          <div class="inline">
            <button id="get-me">GET /users/me</button>
            <button id="session-state" class="secondary">Session State</button>
          </div>
          <div class="inline">
            <button id="powersync-token">PowerSync Token</button>
          </div>
          <label for="access-token">Access Token</label>
          <textarea id="access-token"></textarea>
          <label for="refresh-token">Refresh Token</label>
          <textarea id="refresh-token"></textarea>
        </section>

        <section class="panel">
          <h2>Decoded Claims</h2>
          <pre id="claims">{{}}</pre>
        </section>

        <section class="panel" style="grid-column: 1 / -1;">
          <h2>Last Response</h2>
          <pre id="last-response">{{}}</pre>
        </section>
      </div>
    </main>

    <script>
      const config = {json.dumps(config)};
      const storageKey = "papyrus-auth-sandbox-state";
      const state = JSON.parse(localStorage.getItem(storageKey) || "{{}}");

      const refs = {{
        email: document.getElementById("email"),
        password: document.getElementById("password"),
        newPassword: document.getElementById("new-password"),
        displayName: document.getElementById("display-name"),
        clientType: document.getElementById("client-type"),
        deviceLabel: document.getElementById("device-label"),
        exchangeCode: document.getElementById("exchange-code"),
        verificationToken: document.getElementById("verification-token"),
        accessToken: document.getElementById("access-token"),
        refreshToken: document.getElementById("refresh-token"),
        claims: document.getElementById("claims"),
        lastResponse: document.getElementById("last-response"),
      }};

      function saveState() {{
        localStorage.setItem(storageKey, JSON.stringify(state));
      }}

      function setTokens(payload) {{
        if (payload.access_token) state.accessToken = payload.access_token;
        if (payload.refresh_token) state.refreshToken = payload.refresh_token;
        refs.accessToken.value = state.accessToken || "";
        refs.refreshToken.value = state.refreshToken || "";
        renderClaims();
        saveState();
      }}

      function decodeJwt(token) {{
        if (!token || token.split(".").length < 2) return null;
        const body = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
        const padded = body.padEnd(body.length + (4 - (body.length % 4 || 4)) % 4, "=");
        return JSON.parse(atob(padded));
      }}

      function renderClaims() {{
        refs.claims.textContent = JSON.stringify(decodeJwt(state.accessToken), null, 2);
      }}

      function renderLastResponse(value) {{
        refs.lastResponse.textContent = JSON.stringify(value, null, 2);
      }}

      async function callApi(url, options = {{}}, useAuth = false) {{
        const headers = new Headers(options.headers || {{}});
        headers.set("Content-Type", "application/json");
        if (useAuth && state.accessToken) {{
          headers.set("Authorization", `Bearer ${{state.accessToken}}`);
        }}
        const response = await fetch(url, {{ ...options, headers }});
        const text = await response.text();
        let body;
        try {{
          body = text ? JSON.parse(text) : null;
        }} catch {{
          body = text;
        }}
        renderLastResponse({{ status: response.status, body }});
        return {{ response, body }};
      }}

      function authPayload() {{
        return {{
          email: refs.email.value,
          password: refs.password.value,
          display_name: refs.displayName.value,
          client_type: refs.clientType.value || "web",
          device_label: refs.deviceLabel.value || null,
        }};
      }}

      document.getElementById("register").onclick = async () => {{
        const {{ body }} = await callApi(config.registerUrl, {{
          method: "POST",
          body: JSON.stringify(authPayload()),
        }});
        if (body) setTokens(body);
      }};

      document.getElementById("login").onclick = async () => {{
        const {{ body }} = await callApi(config.loginUrl, {{
          method: "POST",
          body: JSON.stringify({{
            email: refs.email.value,
            password: refs.password.value,
            client_type: refs.clientType.value || "web",
            device_label: refs.deviceLabel.value || null,
          }}),
        }});
        if (body) setTokens(body);
      }};

      document.getElementById("refresh").onclick = async () => {{
        const {{ body }} = await callApi(config.refreshUrl, {{
          method: "POST",
          body: JSON.stringify({{ refresh_token: state.refreshToken || refs.refreshToken.value }}),
        }});
        if (body) setTokens(body);
      }};

      document.getElementById("logout").onclick = async () => {{
        await callApi(config.logoutUrl, {{
          method: "POST",
          body: JSON.stringify({{ all_devices: false }}),
        }}, true);
      }};

      document.getElementById("logout-all").onclick = async () => {{
        await callApi(config.logoutAllUrl, {{ method: "POST" }}, true);
      }};

      document.getElementById("google-login").onclick = async () => {{
        const url = new URL(config.googleStartUrl, window.location.origin);
        url.searchParams.set("redirect_uri", config.redirectUri);
        window.location.assign(url.toString());
      }};

      document.getElementById("google-link").onclick = async () => {{
        const {{ body }} = await callApi(config.googleLinkStartUrl, {{
          method: "POST",
          body: JSON.stringify({{ redirect_uri: config.redirectUri }}),
        }}, true);
        if (body?.authorization_url) {{
          window.location.assign(body.authorization_url);
        }}
      }};

      document.getElementById("exchange").onclick = async () => {{
        const {{ body }} = await callApi(config.exchangeUrl, {{
          method: "POST",
          body: JSON.stringify({{
            code: refs.exchangeCode.value,
            client_type: refs.clientType.value || "web",
            device_label: refs.deviceLabel.value || null,
          }}),
        }});
        if (body) setTokens(body);
      }};

      document.getElementById("complete-link").onclick = async () => {{
        await callApi(config.googleLinkCompleteUrl, {{
          method: "POST",
          body: JSON.stringify({{ code: refs.exchangeCode.value }}),
        }}, true);
      }};

      document.getElementById("resend-verification").onclick = async () => {{
        await callApi(config.resendVerificationUrl, {{
          method: "POST",
          body: JSON.stringify({{ email: refs.email.value }}),
        }});
      }};

      document.getElementById("forgot-password").onclick = async () => {{
        await callApi(config.forgotPasswordUrl, {{
          method: "POST",
          body: JSON.stringify({{ email: refs.email.value }}),
        }});
      }};

      document.getElementById("verify-email").onclick = async () => {{
        await callApi(config.verifyEmailUrl, {{
          method: "POST",
          body: JSON.stringify({{ token: refs.verificationToken.value }}),
        }});
      }};

      document.getElementById("reset-password").onclick = async () => {{
        await callApi(config.resetPasswordUrl, {{
          method: "POST",
          body: JSON.stringify({{
            token: refs.verificationToken.value,
            password: refs.newPassword.value,
          }}),
        }});
      }};

      document.getElementById("get-me").onclick = async () => {{
        await callApi(config.meUrl, {{ method: "GET" }}, true);
      }};

      document.getElementById("session-state").onclick = async () => {{
        await callApi(config.sessionUrl, {{ method: "GET" }}, true);
      }};

      document.getElementById("powersync-token").onclick = async () => {{
        await callApi(config.powersyncUrl, {{ method: "POST" }}, true);
      }};

      refs.accessToken.addEventListener("input", () => {{
        state.accessToken = refs.accessToken.value.trim();
        renderClaims();
        saveState();
      }});

      refs.refreshToken.addEventListener("input", () => {{
        state.refreshToken = refs.refreshToken.value.trim();
        saveState();
      }});

      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");
      const error = params.get("error");
      if (code) {{
        refs.exchangeCode.value = code;
        renderLastResponse({{ status: 302, body: {{ code }} }});
        history.replaceState(null, "", config.redirectUri);
      }} else if (error) {{
        renderLastResponse({{ status: 302, body: {{ error }} }});
        history.replaceState(null, "", config.redirectUri);
      }}

      setTokens(state);
    </script>
  </body>
</html>"""


@router.get("/__dev/auth-sandbox", response_class=HTMLResponse)
async def auth_sandbox(request: Request) -> HTMLResponse:
    """Render the development-only authentication sandbox."""
    return HTMLResponse(_sandbox_html(request))


@router.get("/__dev/auth-sandbox/session", name="auth_sandbox_session")
async def auth_sandbox_session(
    auth_session: CurrentAuthSession,
    payload: CurrentAccessPayload,
) -> dict[str, Any]:
    """Return decoded token and backing DB session state for the sandbox page."""
    return {
        "access_payload": payload,
        "session": {
            "session_id": str(auth_session.session_id),
            "user_id": str(auth_session.user_id),
            "client_type": auth_session.client_type,
            "device_label": auth_session.device_label,
            "created_at": auth_session.created_at.isoformat(),
            "expires_at": auth_session.expires_at.isoformat(),
            "revoked_at": auth_session.revoked_at.isoformat() if auth_session.revoked_at is not None else None,
            "last_seen_at": auth_session.last_seen_at.isoformat() if auth_session.last_seen_at is not None else None,
            "user_disabled_at": (
                auth_session.user.disabled_at.isoformat() if auth_session.user.disabled_at is not None else None
            ),
        },
    }
