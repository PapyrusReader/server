import "../../styles/dev-pages.scss";
import "./page.scss";

import { callApi } from "../../lib/api";
import { getPageConfig } from "../../lib/config";
import { requireElement } from "../../lib/dom";
import { decodeJwt } from "../../lib/jwt";
import { extractErrorMessage, formatTimestamp, renderJson, setButtonBusy } from "../../lib/feedback";
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

interface CurrentUser {
  user_id: string;
  email?: string | null;
  display_name?: string;
}

interface AuthTokensPayload {
  access_token?: string;
  refresh_token?: string;
  user?: CurrentUser;
}

type OutcomeKind = "idle" | "success" | "error" | "warning" | "pending";

interface OutcomeState {
  kind: OutcomeKind;
  message: string;
  at?: string;
}

interface AuthSandboxState {
  accessToken?: string;
  refreshToken?: string;
  currentUser?: CurrentUser;
  pendingOauthMode?: "login" | "link";
  lastOutcome?: OutcomeState;
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
  credentialsStatus: requireElement<HTMLElement>("auth-credentials-status"),
  actionsStatus: requireElement<HTMLElement>("auth-actions-status"),
  recoveryStatus: requireElement<HTMLElement>("auth-recovery-status"),
  diagnosticsStatus: requireElement<HTMLElement>("auth-diagnostics-status"),
  railState: requireElement<HTMLElement>("auth-rail-state"),
  railIdentity: requireElement<HTMLElement>("auth-rail-identity"),
  railAccess: requireElement<HTMLElement>("auth-rail-access"),
  railRefresh: requireElement<HTMLElement>("auth-rail-refresh"),
  railAction: requireElement<HTMLElement>("auth-rail-action"),
  railUpdated: requireElement<HTMLElement>("auth-rail-updated"),
  railOutcome: requireElement<HTMLElement>("auth-rail-outcome"),
  register: requireElement<HTMLButtonElement>("register"),
  login: requireElement<HTMLButtonElement>("login"),
  googleLogin: requireElement<HTMLButtonElement>("google-login"),
  refresh: requireElement<HTMLButtonElement>("refresh"),
  logout: requireElement<HTMLButtonElement>("logout"),
  logoutAll: requireElement<HTMLButtonElement>("logout-all"),
  googleLink: requireElement<HTMLButtonElement>("google-link"),
  getMe: requireElement<HTMLButtonElement>("get-me"),
  sessionState: requireElement<HTMLButtonElement>("session-state"),
  powersyncToken: requireElement<HTMLButtonElement>("powersync-token"),
  exchange: requireElement<HTMLButtonElement>("exchange"),
  completeLink: requireElement<HTMLButtonElement>("complete-link"),
  resendVerification: requireElement<HTMLButtonElement>("resend-verification"),
  forgotPassword: requireElement<HTMLButtonElement>("forgot-password"),
  verifyEmail: requireElement<HTMLButtonElement>("verify-email"),
  resetPassword: requireElement<HTMLButtonElement>("reset-password"),
};

const busyActions = new Set<string>();
let currentAction = "Idle";

function persistState(): void {
  saveStoredState(storageKey, state);
}

function isSignedIn(): boolean {
  return Boolean(state.accessToken && state.currentUser?.user_id);
}

function markOutcome(kind: OutcomeKind, message: string): void {
  state.lastOutcome = {
    kind,
    message,
    at: new Date().toISOString(),
  };
  persistState();
  renderStatus();
}

function setPendingAction(message: string): void {
  currentAction = message;
  renderStatus();
}

function clearPendingAction(): void {
  currentAction = "Idle";
  renderStatus();
}

function applyChipState(target: HTMLElement, label: string, kind: "muted" | "success" | "warning" | "danger" | "accent"): void {
  target.textContent = label;
  target.className = `dev-chip dev-chip--${kind}`;
}

function renderStatus(): void {
  if (isSignedIn()) {
    applyChipState(refs.railState, "Signed in", "success");
    refs.railIdentity.textContent =
      state.currentUser?.email ?? state.currentUser?.display_name ?? state.currentUser?.user_id ?? "Authenticated user";
  } else {
    applyChipState(refs.railState, "Signed out", "muted");
    refs.railIdentity.textContent = "No authenticated user loaded.";
  }

  applyChipState(refs.railAccess, state.accessToken ? "Access token loaded" : "No access token", state.accessToken ? "accent" : "muted");
  applyChipState(
    refs.railRefresh,
    state.refreshToken ? "Refresh token loaded" : "No refresh token",
    state.refreshToken ? "accent" : "muted",
  );

  refs.railAction.textContent = busyActions.size > 0 ? currentAction : "Idle";
  refs.railUpdated.textContent = formatTimestamp(state.lastOutcome?.at);
  refs.railOutcome.textContent = state.lastOutcome?.message ?? "No actions have been triggered yet.";

  refs.credentialsStatus.textContent = isSignedIn()
    ? `Authenticated as ${state.currentUser?.email ?? state.currentUser?.display_name ?? "current user"}.`
    : "Ready for registration or login.";
  refs.actionsStatus.textContent = isSignedIn()
    ? "Signed-in actions are enabled."
    : "Signed-in actions are disabled until the sandbox has an active session.";
  refs.recoveryStatus.textContent =
    refs.exchangeCode.value.trim() || refs.verificationToken.value.trim()
      ? "Token-driven actions are ready."
      : "Paste an exchange or email token to enable the matching actions.";
  refs.diagnosticsStatus.textContent = state.accessToken
    ? "Token payloads and the most recent API response are available below."
    : "Token payloads appear here after a successful auth flow.";
}

function renderClaims(): void {
  renderJson(refs.claims, decodeJwt(state.accessToken));
}

function renderLastResponse(value: unknown): void {
  renderJson(refs.lastResponse, value);
}

function syncTokenFields(): void {
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
  renderClaims();
}

function renderControls(): void {
  const signedIn = isSignedIn();
  const hasRefresh = Boolean(state.refreshToken);
  const hasExchangeCode = refs.exchangeCode.value.trim().length > 0;
  const hasVerificationToken = refs.verificationToken.value.trim().length > 0;
  const globalBusy = busyActions.size > 0;

  refs.register.disabled = globalBusy || signedIn;
  refs.login.disabled = globalBusy || signedIn;
  refs.googleLogin.disabled = globalBusy || signedIn;
  refs.refresh.disabled = globalBusy || !hasRefresh;
  refs.logout.disabled = globalBusy || !signedIn;
  refs.logoutAll.disabled = globalBusy || !signedIn;
  refs.googleLink.disabled = globalBusy || !signedIn;
  refs.getMe.disabled = globalBusy || !signedIn;
  refs.sessionState.disabled = globalBusy || !signedIn;
  refs.powersyncToken.disabled = globalBusy || !signedIn;
  refs.exchange.disabled = globalBusy || !hasExchangeCode;
  refs.completeLink.disabled = globalBusy || !signedIn || !hasExchangeCode;
  refs.resendVerification.disabled = globalBusy;
  refs.forgotPassword.disabled = globalBusy;
  refs.verifyEmail.disabled = globalBusy || !hasVerificationToken;
  refs.resetPassword.disabled = globalBusy || !hasVerificationToken;
}

function applyTokens(payload: AuthTokensPayload): void {
  if (payload.access_token) {
    state.accessToken = payload.access_token;
  }

  if (payload.refresh_token) {
    state.refreshToken = payload.refresh_token;
  }

  if (payload.user) {
    state.currentUser = payload.user;
  }

  syncTokenFields();
  persistState();
  renderStatus();
  renderControls();
}

function clearAuthState(): void {
  delete state.accessToken;
  delete state.refreshToken;
  delete state.currentUser;
  syncTokenFields();
  persistState();
  renderStatus();
  renderControls();
}

async function callSandboxApi<T = unknown>(url: string, requestInit: RequestInit, useAuth = false) {
  return callApi<T>(url, requestInit, {
    accessToken: useAuth ? state.accessToken ?? null : null,
    onResponse: renderLastResponse,
  });
}

async function loadCurrentUser(): Promise<void> {
  if (!state.accessToken) {
    return;
  }

  const { response, body } = await callSandboxApi<CurrentUser>(config.meUrl, { method: "GET" }, true);

  if (response.ok && body && typeof body === "object") {
    state.currentUser = body as CurrentUser;
    persistState();
    renderStatus();
    renderControls();
  }
}

async function runButtonAction(
  actionKey: string,
  button: HTMLButtonElement,
  busyLabel: string,
  pendingMessage: string,
  runner: () => Promise<void>,
): Promise<void> {
  busyActions.add(actionKey);
  setButtonBusy(button, true, busyLabel);
  setPendingAction(pendingMessage);
  renderControls();

  try {
    await runner();
  } finally {
    busyActions.delete(actionKey);
    setButtonBusy(button, false, busyLabel);
    clearPendingAction();
    renderControls();
  }
}

async function handleOAuthReturn(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error");

  if (code) {
    refs.exchangeCode.value = code;
    history.replaceState(null, "", config.redirectUri);

    if (state.pendingOauthMode === "login") {
      setPendingAction("Completing Google login");
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
        markOutcome("success", "Google login completed.");
      } else {
        markOutcome("error", extractErrorMessage(body, "Google login failed."));
      }
    } else if (state.pendingOauthMode === "link" && state.accessToken) {
      setPendingAction("Completing Google link");
      const { response, body } = await callSandboxApi(
        config.googleLinkCompleteUrl,
        {
          method: "POST",
          body: JSON.stringify({ code }),
        },
        true,
      );

      markOutcome(response.ok ? "success" : "error", response.ok ? "Google account linked." : extractErrorMessage(body, "Google link failed."));
    } else {
      markOutcome("warning", "OAuth callback returned a code that was not automatically applied.");
    }

    delete state.pendingOauthMode;
    persistState();
    renderStatus();
    renderControls();
    return;
  }

  if (error) {
    history.replaceState(null, "", config.redirectUri);
    delete state.pendingOauthMode;
    persistState();
    markOutcome("error", `OAuth callback failed: ${error}`);
  }
}

function registerHandlers(): void {
  refs.register.onclick = async () => {
    await runButtonAction("register", refs.register, "Registering", "Registering user", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.registerUrl, {
        method: "POST",
        body: JSON.stringify({
          email: refs.email.value,
          password: refs.password.value,
          display_name: refs.displayName.value,
          client_type: refs.clientType.value || "web",
          device_label: refs.deviceLabel.value || null,
        }),
      });

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
        markOutcome("success", "Registration succeeded and tokens were stored.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Registration failed."));
    });
  };

  refs.login.onclick = async () => {
    await runButtonAction("login", refs.login, "Logging in", "Logging in", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.loginUrl, {
        method: "POST",
        body: JSON.stringify({
          email: refs.email.value,
          password: refs.password.value,
          client_type: refs.clientType.value || "web",
          device_label: refs.deviceLabel.value || null,
        }),
      });

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
        markOutcome("success", "Login succeeded.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Login failed."));
    });
  };

  refs.googleLogin.onclick = () => {
    state.pendingOauthMode = "login";
    persistState();
    markOutcome("pending", "Redirecting to Google login.");
    const url = new URL(config.googleStartUrl, window.location.origin);
    url.searchParams.set("redirect_uri", config.redirectUri);
    window.location.assign(url.toString());
  };

  refs.refresh.onclick = async () => {
    await runButtonAction("refresh", refs.refresh, "Refreshing", "Refreshing access token", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.refreshUrl, {
        method: "POST",
        body: JSON.stringify({ refresh_token: state.refreshToken }),
      });

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
        markOutcome("success", "Tokens refreshed.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Token refresh failed."));
    });
  };

  refs.logout.onclick = async () => {
    await runButtonAction("logout", refs.logout, "Logging out", "Ending current session", async () => {
      const { response, body } = await callSandboxApi(
        config.logoutUrl,
        {
          method: "POST",
          body: JSON.stringify({ all_devices: false }),
        },
        true,
      );

      if (response.ok) {
        clearAuthState();
        markOutcome("success", "Current session logged out.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Logout failed."));
    });
  };

  refs.logoutAll.onclick = async () => {
    await runButtonAction("logout-all", refs.logoutAll, "Logging out", "Ending all sessions", async () => {
      const { response, body } = await callSandboxApi(config.logoutAllUrl, { method: "POST" }, true);

      if (response.ok) {
        clearAuthState();
        markOutcome("success", "All sessions logged out.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Global logout failed."));
    });
  };

  refs.googleLink.onclick = async () => {
    await runButtonAction("google-link", refs.googleLink, "Preparing", "Preparing Google link flow", async () => {
      state.pendingOauthMode = "link";
      persistState();
      const { response, body } = await callSandboxApi<{ authorization_url?: string }>(
        config.googleLinkStartUrl,
        {
          method: "POST",
          body: JSON.stringify({ redirect_uri: config.redirectUri }),
        },
        true,
      );

      if (response.ok && body && typeof body === "object" && typeof body.authorization_url === "string") {
        markOutcome("pending", "Redirecting to Google to link the account.");
        window.location.assign(body.authorization_url);
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Failed to start Google link flow."));
    });
  };

  refs.exchange.onclick = async () => {
    await runButtonAction("exchange", refs.exchange, "Exchanging", "Exchanging one-time code", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.exchangeUrl, {
        method: "POST",
        body: JSON.stringify({
          code: refs.exchangeCode.value,
          client_type: refs.clientType.value || "web",
          device_label: refs.deviceLabel.value || null,
        }),
      });

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
        markOutcome("success", "Exchange code consumed and tokens loaded.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Exchange failed."));
    });
  };

  refs.completeLink.onclick = async () => {
    await runButtonAction("complete-link", refs.completeLink, "Linking", "Completing Google link", async () => {
      const { response, body } = await callSandboxApi(
        config.googleLinkCompleteUrl,
        {
          method: "POST",
          body: JSON.stringify({ code: refs.exchangeCode.value }),
        },
        true,
      );

      markOutcome(response.ok ? "success" : "error", response.ok ? "Google link completed." : extractErrorMessage(body, "Link completion failed."));
    });
  };

  refs.resendVerification.onclick = async () => {
    await runButtonAction(
      "resend-verification",
      refs.resendVerification,
      "Sending",
      "Sending verification email",
      async () => {
        const { response, body } = await callSandboxApi(config.resendVerificationUrl, {
          method: "POST",
          body: JSON.stringify({ email: refs.email.value }),
        });

        markOutcome(
          response.ok ? "success" : "error",
          response.ok ? "Verification email requested." : extractErrorMessage(body, "Resend verification failed."),
        );
      },
    );
  };

  refs.forgotPassword.onclick = async () => {
    await runButtonAction("forgot-password", refs.forgotPassword, "Sending", "Sending password reset email", async () => {
      const { response, body } = await callSandboxApi(config.forgotPasswordUrl, {
        method: "POST",
        body: JSON.stringify({ email: refs.email.value }),
      });

      markOutcome(
        response.ok ? "success" : "error",
        response.ok ? "Password reset email requested." : extractErrorMessage(body, "Forgot password failed."),
      );
    });
  };

  refs.verifyEmail.onclick = async () => {
    await runButtonAction("verify-email", refs.verifyEmail, "Verifying", "Verifying email token", async () => {
      const { response, body } = await callSandboxApi(config.verifyEmailUrl, {
        method: "POST",
        body: JSON.stringify({ token: refs.verificationToken.value }),
      });

      markOutcome(response.ok ? "success" : "error", response.ok ? "Email verified." : extractErrorMessage(body, "Email verification failed."));
    });
  };

  refs.resetPassword.onclick = async () => {
    await runButtonAction("reset-password", refs.resetPassword, "Resetting", "Resetting password", async () => {
      const { response, body } = await callSandboxApi(config.resetPasswordUrl, {
        method: "POST",
        body: JSON.stringify({
          token: refs.verificationToken.value,
          password: refs.newPassword.value,
        }),
      });

      markOutcome(response.ok ? "success" : "error", response.ok ? "Password reset completed." : extractErrorMessage(body, "Password reset failed."));
    });
  };

  refs.getMe.onclick = async () => {
    await runButtonAction("get-me", refs.getMe, "Loading", "Fetching current user", async () => {
      const { response, body } = await callSandboxApi<CurrentUser>(config.meUrl, { method: "GET" }, true);

      if (response.ok && body && typeof body === "object") {
        state.currentUser = body as CurrentUser;
        persistState();
        markOutcome("success", "Loaded current user profile.");
        renderStatus();
        renderControls();
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Fetching /users/me failed."));
    });
  };

  refs.sessionState.onclick = async () => {
    await runButtonAction("session-state", refs.sessionState, "Loading", "Fetching current session state", async () => {
      const { response, body } = await callSandboxApi(config.sessionUrl, { method: "GET" }, true);
      markOutcome(response.ok ? "success" : "error", response.ok ? "Session state loaded." : extractErrorMessage(body, "Loading session state failed."));
    });
  };

  refs.powersyncToken.onclick = async () => {
    await runButtonAction("powersync-token", refs.powersyncToken, "Issuing", "Issuing PowerSync token", async () => {
      const { response, body } = await callSandboxApi(config.powersyncUrl, { method: "POST" }, true);
      markOutcome(response.ok ? "success" : "error", response.ok ? "PowerSync token issued." : extractErrorMessage(body, "PowerSync token request failed."));
    });
  };

  refs.exchangeCode.addEventListener("input", renderControls);
  refs.verificationToken.addEventListener("input", renderControls);
}

async function bootstrap(): Promise<void> {
  syncTokenFields();
  renderLastResponse({});
  renderStatus();
  renderControls();
  registerHandlers();

  await handleOAuthReturn();

  if (state.accessToken && !state.currentUser) {
    await loadCurrentUser();
  } else {
    renderStatus();
    renderControls();
  }
}

void bootstrap();
