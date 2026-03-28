import "../../styles/dev-pages.scss";
import "./page.scss";

import { callApi } from "../../lib/api";
import { getPageConfig } from "../../lib/config";
import { requireElement } from "../../lib/dom";
import { decodeJwt } from "../../lib/jwt";
import { loadStoredState, saveStoredState } from "../../lib/storage";

interface AuthSandboxConfig {
  registerUrl: string;
  loginUrl: string;
  refreshUrl: string;
  logoutUrl: string;
  logoutAllUrl: string;
  googleStartUrl: string;
  googleLinkStartUrl: string;
  googleLinkCompleteUrl: string;
  exchangeUrl: string;
  forgotPasswordUrl: string;
  resendVerificationUrl: string;
  verifyEmailUrl: string;
  resetPasswordUrl: string;
  powersyncUrl: string;
  meUrl: string;
  sessionUrl: string;
  redirectUri: string;
}

interface AuthTokensPayload {
  access_token?: string;
  refresh_token?: string;
}

interface AuthSandboxState {
  accessToken?: string;
  refreshToken?: string;
  pendingOauthMode?: "login" | "link";
}

const config = getPageConfig<AuthSandboxConfig>();
const storageKey = "papyrus-auth-sandbox-state";
const state = loadStoredState<AuthSandboxState>(storageKey);

const refs = {
  email: requireElement<HTMLInputElement>("email"),
  password: requireElement<HTMLInputElement>("password"),
  newPassword: requireElement<HTMLInputElement>("new-password"),
  displayName: requireElement<HTMLInputElement>("display-name"),
  clientType: requireElement<HTMLInputElement>("client-type"),
  deviceLabel: requireElement<HTMLInputElement>("device-label"),
  exchangeCode: requireElement<HTMLInputElement>("exchange-code"),
  verificationToken: requireElement<HTMLInputElement>("verification-token"),
  accessToken: requireElement<HTMLTextAreaElement>("access-token"),
  refreshToken: requireElement<HTMLTextAreaElement>("refresh-token"),
  claims: requireElement<HTMLElement>("claims"),
  lastResponse: requireElement<HTMLElement>("last-response"),
};

function persistState(): void {
  saveStoredState(storageKey, state);
}

function renderLastResponse(value: unknown): void {
  refs.lastResponse.textContent = JSON.stringify(value, null, 2);
}

function renderClaims(): void {
  refs.claims.textContent = JSON.stringify(decodeJwt(state.accessToken), null, 2);
}

function syncTokenFields(): void {
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
  renderClaims();
  persistState();
}

function applyTokens(payload: AuthTokensPayload): void {
  if (payload.access_token) {
    state.accessToken = payload.access_token;
  }

  if (payload.refresh_token) {
    state.refreshToken = payload.refresh_token;
  }

  syncTokenFields();
}

function authPayload(): Record<string, string | null> {
  return {
    email: refs.email.value,
    password: refs.password.value,
    display_name: refs.displayName.value,
    client_type: refs.clientType.value || "web",
    device_label: refs.deviceLabel.value || null,
  };
}

async function callSandboxApi<T = unknown>(url: string, requestInit: RequestInit, useAuth = false) {
  return callApi<T>(url, requestInit, {
    accessToken: useAuth ? state.accessToken ?? null : null,
    onResponse: renderLastResponse,
  });
}

async function handleOAuthReturn(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error");

  if (code) {
    refs.exchangeCode.value = code;
    history.replaceState(null, "", config.redirectUri);

    if (state.pendingOauthMode === "login") {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(
        config.exchangeUrl,
        {
          method: "POST",
          body: JSON.stringify({
            code,
            client_type: refs.clientType.value || "web",
            device_label: refs.deviceLabel.value || null,
          }),
        },
      );

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
      }
    } else if (state.pendingOauthMode === "link" && state.accessToken) {
      await callSandboxApi(
        config.googleLinkCompleteUrl,
        {
          method: "POST",
          body: JSON.stringify({ code }),
        },
        true,
      );
    } else {
      renderLastResponse({ status: 302, body: { code } });
    }

    delete state.pendingOauthMode;
    persistState();
    return;
  }

  if (error) {
    renderLastResponse({ status: 302, body: { error } });
    history.replaceState(null, "", config.redirectUri);
    delete state.pendingOauthMode;
    persistState();
  }
}

function registerHandlers(): void {
  requireElement<HTMLButtonElement>("register").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.registerUrl, {
      method: "POST",
      body: JSON.stringify(authPayload()),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }
  };

  requireElement<HTMLButtonElement>("login").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.loginUrl, {
      method: "POST",
      body: JSON.stringify({
        email: refs.email.value,
        password: refs.password.value,
        client_type: refs.clientType.value || "web",
        device_label: refs.deviceLabel.value || null,
      }),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }
  };

  requireElement<HTMLButtonElement>("refresh").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.refreshUrl, {
      method: "POST",
      body: JSON.stringify({ refresh_token: state.refreshToken ?? refs.refreshToken.value }),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }
  };

  requireElement<HTMLButtonElement>("logout").onclick = async () => {
    await callSandboxApi(
      config.logoutUrl,
      {
        method: "POST",
        body: JSON.stringify({ all_devices: false }),
      },
      true,
    );
  };

  requireElement<HTMLButtonElement>("logout-all").onclick = async () => {
    await callSandboxApi(config.logoutAllUrl, { method: "POST" }, true);
  };

  requireElement<HTMLButtonElement>("google-login").onclick = () => {
    state.pendingOauthMode = "login";
    persistState();
    const url = new URL(config.googleStartUrl, window.location.origin);
    url.searchParams.set("redirect_uri", config.redirectUri);
    window.location.assign(url.toString());
  };

  requireElement<HTMLButtonElement>("google-link").onclick = async () => {
    state.pendingOauthMode = "link";
    persistState();
    const { body } = await callSandboxApi<{ authorization_url?: string }>(
      config.googleLinkStartUrl,
      {
        method: "POST",
        body: JSON.stringify({ redirect_uri: config.redirectUri }),
      },
      true,
    );

    if (body && typeof body === "object" && "authorization_url" in body && typeof body.authorization_url === "string") {
      window.location.assign(body.authorization_url);
    }
  };

  requireElement<HTMLButtonElement>("exchange").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.exchangeUrl, {
      method: "POST",
      body: JSON.stringify({
        code: refs.exchangeCode.value,
        client_type: refs.clientType.value || "web",
        device_label: refs.deviceLabel.value || null,
      }),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }
  };

  requireElement<HTMLButtonElement>("complete-link").onclick = async () => {
    await callSandboxApi(
      config.googleLinkCompleteUrl,
      {
        method: "POST",
        body: JSON.stringify({ code: refs.exchangeCode.value }),
      },
      true,
    );
  };

  requireElement<HTMLButtonElement>("resend-verification").onclick = async () => {
    await callSandboxApi(config.resendVerificationUrl, {
      method: "POST",
      body: JSON.stringify({ email: refs.email.value }),
    });
  };

  requireElement<HTMLButtonElement>("forgot-password").onclick = async () => {
    await callSandboxApi(config.forgotPasswordUrl, {
      method: "POST",
      body: JSON.stringify({ email: refs.email.value }),
    });
  };

  requireElement<HTMLButtonElement>("verify-email").onclick = async () => {
    await callSandboxApi(config.verifyEmailUrl, {
      method: "POST",
      body: JSON.stringify({ token: refs.verificationToken.value }),
    });
  };

  requireElement<HTMLButtonElement>("reset-password").onclick = async () => {
    await callSandboxApi(config.resetPasswordUrl, {
      method: "POST",
      body: JSON.stringify({
        token: refs.verificationToken.value,
        password: refs.newPassword.value,
      }),
    });
  };

  requireElement<HTMLButtonElement>("get-me").onclick = async () => {
    await callSandboxApi(config.meUrl, { method: "GET" }, true);
  };

  requireElement<HTMLButtonElement>("session-state").onclick = async () => {
    await callSandboxApi(config.sessionUrl, { method: "GET" }, true);
  };

  requireElement<HTMLButtonElement>("powersync-token").onclick = async () => {
    await callSandboxApi(config.powersyncUrl, { method: "POST" }, true);
  };

  refs.accessToken.addEventListener("input", () => {
    state.accessToken = refs.accessToken.value.trim();
    syncTokenFields();
  });

  refs.refreshToken.addEventListener("input", () => {
    state.refreshToken = refs.refreshToken.value.trim();
    syncTokenFields();
  });
}

async function bootstrap(): Promise<void> {
  registerHandlers();
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
  renderClaims();
  await handleOAuthReturn();
}

void bootstrap();
