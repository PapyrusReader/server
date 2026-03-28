import "../../styles/dev-pages.scss";
import "./page.scss";

import { callApi } from "../../lib/api";
import { getPageConfig } from "../../lib/config";
import { requireElement } from "../../lib/dom";
import { escapeHtml } from "../../lib/html";
import { extractErrorMessage, formatTimestamp, renderJson, setButtonBusy } from "../../lib/feedback";
import { loadStoredState, saveStoredState } from "../../lib/storage";

interface PowerSyncSandboxConfig {
  register_url: string;
  login_url: string;
  refresh_url: string;
  logout_url: string;
  google_start_url: string;
  exchange_url: string;
  me_url: string;
  powersync_token_url: string;
  powersync_endpoint: string;
  items_url: string;
  upload_url: string;
  redirect_uri: string;
  sdk_module_url: string;
  db_worker_url: string;
  sync_worker_url: string;
  db_filename: string;
}

interface CurrentUser {
  user_id: string;
  email?: string | null;
}

interface AuthTokensPayload {
  access_token?: string;
  refresh_token?: string;
  user?: CurrentUser;
}

interface LocalItem {
  id?: string;
  item_id?: string;
  title?: string | null;
  notes?: string | null;
  owner_user_id?: string;
  created_at?: string;
  updated_at?: string;
}

interface PowerSyncSandboxState {
  accessToken?: string;
  refreshToken?: string;
  currentUser?: CurrentUser;
  localItems?: LocalItem[];
  connected?: boolean;
  lastOutcome?: {
    kind: "idle" | "success" | "error" | "pending";
    message: string;
    at?: string;
  };
}

interface WatchResult {
  rows?: { _array?: LocalItem[] } | LocalItem[];
}

type PowerSyncModule = {
  PowerSyncDatabase: new (options: unknown) => any;
  WASQLiteOpenFactory: new (options: unknown) => any;
  Schema: new (tables: unknown) => any;
  Table: new (columns: unknown) => any;
  column: {
    text: unknown;
  };
};

const config = getPageConfig<PowerSyncSandboxConfig>();
const storageKey = `papyrus-powersync-sandbox:${config.db_filename}`;
const state = loadStoredState<PowerSyncSandboxState>(storageKey);

const refs = {
  email: requireElement<HTMLInputElement>("email"),
  password: requireElement<HTMLInputElement>("password"),
  displayName: requireElement<HTMLInputElement>("display-name"),
  clientType: requireElement<HTMLInputElement>("client-type"),
  deviceLabel: requireElement<HTMLInputElement>("device-label"),
  title: requireElement<HTMLInputElement>("title"),
  notes: requireElement<HTMLTextAreaElement>("notes"),
  accessToken: requireElement<HTMLTextAreaElement>("access-token"),
  refreshToken: requireElement<HTMLTextAreaElement>("refresh-token"),
  authStatus: requireElement<HTMLElement>("auth-status"),
  syncStatus: requireElement<HTMLElement>("sync-status"),
  clientLabel: requireElement<HTMLElement>("client-label"),
  localItems: requireElement<HTMLElement>("local-items"),
  serverItems: requireElement<HTMLElement>("server-items"),
  lastResponse: requireElement<HTMLElement>("last-response"),
  railAuth: requireElement<HTMLElement>("powersync-rail-auth"),
  railSync: requireElement<HTMLElement>("powersync-rail-sync"),
  railDb: requireElement<HTMLElement>("powersync-rail-db"),
  railAction: requireElement<HTMLElement>("powersync-rail-action"),
  railUpdated: requireElement<HTMLElement>("powersync-rail-updated"),
  railOutcome: requireElement<HTMLElement>("powersync-rail-outcome"),
  register: requireElement<HTMLButtonElement>("register"),
  login: requireElement<HTMLButtonElement>("login"),
  googleLogin: requireElement<HTMLButtonElement>("google-login"),
  refresh: requireElement<HTMLButtonElement>("refresh"),
  logout: requireElement<HTMLButtonElement>("logout"),
  connect: requireElement<HTMLButtonElement>("connect"),
  disconnect: requireElement<HTMLButtonElement>("disconnect"),
  createItem: requireElement<HTMLButtonElement>("create-item"),
  refreshServer: requireElement<HTMLButtonElement>("refresh-server"),
};

let powersyncModule: PowerSyncModule | undefined;
let db: any;
let localWatch: { unsubscribe?: () => void; close?: () => void } | null = null;
let currentAction = "Idle";
const busyActions = new Set<string>();

function persistState(): void {
  saveStoredState(storageKey, state);
}

function isSignedIn(): boolean {
  return Boolean(state.accessToken && state.currentUser?.user_id);
}

function isConnected(): boolean {
  return Boolean(state.connected && db);
}

function applyChipState(target: HTMLElement, label: string, kind: "muted" | "success" | "warning" | "danger" | "accent"): void {
  target.textContent = label;
  target.className = `dev-chip dev-chip--${kind}`;
}

function markOutcome(kind: "idle" | "success" | "error" | "pending", message: string): void {
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

function renderStatus(): void {
  applyChipState(
    refs.railAuth,
    isSignedIn() ? `Signed in: ${state.currentUser?.email ?? "user"}` : "Signed out",
    isSignedIn() ? "success" : "muted",
  );
  applyChipState(
    refs.railSync,
    isConnected() ? "Connected" : state.connected ? "Reconnecting" : "Disconnected",
    isConnected() ? "accent" : "warning",
  );
  applyChipState(refs.railDb, config.db_filename, "muted");
  refs.railAction.textContent = busyActions.size > 0 ? currentAction : "Idle";
  refs.railUpdated.textContent = formatTimestamp(state.lastOutcome?.at);
  refs.railOutcome.textContent = state.lastOutcome?.message ?? "Authenticate to enable PowerSync operations.";

  refs.clientLabel.textContent = config.db_filename;
  refs.clientLabel.className = "dev-chip dev-chip--muted";
  refs.authStatus.textContent = isSignedIn()
    ? `Authenticated as ${state.currentUser?.email ?? state.currentUser?.user_id}.`
    : "Sign in to enable PowerSync connection controls.";
  refs.syncStatus.textContent = isConnected()
    ? `Connected to ${config.powersync_endpoint}.`
    : isSignedIn()
      ? "Ready to connect to PowerSync."
      : "Authenticate before connecting to PowerSync.";
}

function renderLastResponse(value: unknown): void {
  renderJson(refs.lastResponse, value);
}

function syncTokenFields(): void {
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
}

function renderControls(): void {
  const signedIn = isSignedIn();
  const connected = isConnected();
  const globalBusy = busyActions.size > 0;

  refs.register.disabled = globalBusy || signedIn;
  refs.login.disabled = globalBusy || signedIn;
  refs.googleLogin.disabled = globalBusy || signedIn;
  refs.refresh.disabled = globalBusy || !state.refreshToken;
  refs.logout.disabled = globalBusy || !signedIn;
  refs.connect.disabled = globalBusy || !signedIn || connected;
  refs.disconnect.disabled = globalBusy || !connected;
  refs.createItem.disabled = globalBusy || !connected;
  refs.refreshServer.disabled = globalBusy || !signedIn;

  refs.localItems.querySelectorAll<HTMLButtonElement>("button[data-action]").forEach((button) => {
    button.disabled = globalBusy || !connected;
  });
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

function clearTokens(): void {
  delete state.accessToken;
  delete state.refreshToken;
  delete state.currentUser;
  delete state.connected;
  syncTokenFields();
  persistState();
  renderStatus();
  renderControls();
}

function renderItems(target: HTMLElement, items: LocalItem[], emptyMessage: string): void {
  if (items.length === 0) {
    target.innerHTML = `<div class="dev-item"><p class="dev-status">${escapeHtml(emptyMessage)}</p></div>`;
    return;
  }

  const buttonsDisabled = busyActions.size > 0 || !isConnected() ? "disabled" : "";

  target.innerHTML = items
    .map(
      (item) => `
        <div class="dev-item">
          <strong>${escapeHtml(item.title ?? "Untitled Item")}</strong>
          <div class="dev-status">id: ${escapeHtml(item.item_id ?? item.id)}</div>
          <div class="dev-status">updated: ${escapeHtml(item.updated_at ?? "unknown")}</div>
          <p>${escapeHtml(item.notes ?? "No notes")}</p>
          ${
            target === refs.localItems
              ? `
            <div class="dev-item-actions">
              <button data-action="update" data-id="${escapeHtml(item.id ?? item.item_id)}" ${buttonsDisabled}>Update</button>
              <button class="secondary" data-action="delete" data-id="${escapeHtml(item.id ?? item.item_id)}" ${buttonsDisabled}>Delete</button>
            </div>
          `
              : ""
          }
        </div>
      `,
    )
    .join("");
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

  const { response, body } = await callSandboxApi<CurrentUser>(config.me_url, { method: "GET" }, true);

  if (response.ok && body && typeof body === "object") {
    state.currentUser = body as CurrentUser;
    persistState();
    renderStatus();
    renderControls();
  }
}

async function loadPowerSyncModule(): Promise<PowerSyncModule> {
  if (!powersyncModule) {
    powersyncModule = (await import(/* @vite-ignore */ config.sdk_module_url)) as PowerSyncModule;
  }

  return powersyncModule;
}

function buildSchema(module: PowerSyncModule): unknown {
  const demoItems = new module.Table({
    owner_user_id: module.column.text,
    title: module.column.text,
    notes: module.column.text,
    created_at: module.column.text,
    updated_at: module.column.text,
  });

  return new module.Schema({ demo_items: demoItems });
}

function normalizeWatchRows(result: WatchResult | undefined): LocalItem[] {
  const rows = result?.rows;

  if (Array.isArray(rows)) {
    return rows;
  }

  if (rows && "_array" in rows && Array.isArray(rows._array)) {
    return rows._array;
  }

  return [];
}

async function refreshServerItems(): Promise<void> {
  if (!state.accessToken) {
    renderItems(refs.serverItems, [], "Sign in to inspect the server source rows.");
    return;
  }

  const { response, body } = await callSandboxApi<{ items?: LocalItem[] }>(config.items_url, { method: "GET" }, true);

  if (!response.ok || !body || typeof body !== "object" || !Array.isArray(body.items)) {
    return;
  }

  renderItems(refs.serverItems, body.items, "No source rows yet.");
  renderControls();
}

function startWatchingLocalItems(): void {
  if (!db) {
    return;
  }

  localWatch = db.watch(
    "SELECT id, owner_user_id, title, notes, created_at, updated_at FROM demo_items ORDER BY updated_at DESC, id DESC",
    [],
    {
      onResult: (result: WatchResult) => {
        state.localItems = normalizeWatchRows(result);
        renderItems(refs.localItems, state.localItems ?? [], "No local synced items yet.");
        renderControls();
      },
    },
  );
}

async function connectPowerSync(): Promise<void> {
  if (db || !state.accessToken || !state.currentUser?.user_id) {
    return;
  }

  const module = await loadPowerSyncModule();
  const schema = buildSchema(module);

  db = new module.PowerSyncDatabase({
    schema,
    database: new module.WASQLiteOpenFactory({
      dbFilename: config.db_filename,
      worker: config.db_worker_url,
    }),
    sync: {
      worker: config.sync_worker_url,
    },
  });

  const connector = {
    async fetchCredentials(): Promise<{ endpoint: string; token: string }> {
      const { response, body } = await callSandboxApi<{ token?: string }>(config.powersync_token_url, { method: "POST" }, true);

      if (!response.ok || !body || typeof body !== "object" || typeof body.token !== "string") {
        throw new Error(extractErrorMessage(body, "Failed to fetch PowerSync credentials"));
      }

      return {
        endpoint: config.powersync_endpoint,
        token: body.token,
      };
    },

    async uploadData(database: any): Promise<void> {
      let transaction = await database.getNextCrudTransaction();

      while (transaction) {
        markOutcome("pending", "Uploading queued PowerSync changes.");

        const { response, body } = await callSandboxApi(
          config.upload_url,
          {
            method: "POST",
            body: JSON.stringify({ batch: transaction.crud }),
          },
          true,
        );

        if (!response.ok) {
          throw new Error(extractErrorMessage(body, "Failed to upload PowerSync changes"));
        }

        await transaction.complete();
        transaction = await database.getNextCrudTransaction();
      }

      await refreshServerItems();
      markOutcome("success", "Queued PowerSync changes uploaded.");
    },
  };

  await db.connect(connector);
  state.connected = true;
  persistState();
  renderStatus();
  startWatchingLocalItems();
  await refreshServerItems();
  renderItems(refs.localItems, state.localItems ?? [], "No local synced items yet.");
  renderControls();
}

async function disconnectPowerSync(): Promise<void> {
  if (!db) {
    delete state.connected;
    persistState();
    renderStatus();
    renderControls();
    return;
  }

  localWatch?.unsubscribe?.();
  localWatch?.close?.();

  await db.close({ disconnect: true });
  db = null;
  localWatch = null;
  delete state.connected;
  persistState();
  renderStatus();
  renderItems(refs.localItems, [], "Connect PowerSync to mirror demo rows locally.");
  renderControls();
}

async function createLocalItem(): Promise<void> {
  if (!db || !state.currentUser?.user_id) {
    return;
  }

  const itemId = crypto.randomUUID();
  const title = refs.title.value.trim() || "Untitled Item";
  const notes = refs.notes.value.trim() || null;

  await db.execute(
    "INSERT INTO demo_items(id, owner_user_id, title, notes, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
    [itemId, state.currentUser.user_id, title, notes],
  );
}

async function updateLocalItem(itemId: string): Promise<void> {
  if (!db) {
    return;
  }

  await db.execute(
    "UPDATE demo_items SET title = ?, updated_at = datetime('now') WHERE id = ?",
    [`Updated at ${new Date().toISOString()}`, itemId],
  );
}

async function deleteLocalItem(itemId: string): Promise<void> {
  if (!db) {
    return;
  }

  await db.execute("DELETE FROM demo_items WHERE id = ?", [itemId]);
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
    const { response, body } = await callSandboxApi<AuthTokensPayload>(config.exchange_url, {
      method: "POST",
      body: JSON.stringify({
        code,
        client_type: refs.clientType.value || "web",
        device_label: refs.deviceLabel.value || null,
      }),
    });

    history.replaceState(null, "", config.redirect_uri);

    if (response.ok && body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
      markOutcome("success", "Google login completed.");
      return;
    }

    markOutcome("error", extractErrorMessage(body, "Google login failed."));
    return;
  }

  if (error) {
    history.replaceState(null, "", config.redirect_uri);
    markOutcome("error", `OAuth callback failed: ${error}`);
  }
}

function registerHandlers(): void {
  refs.register.onclick = async () => {
    await runButtonAction("register", refs.register, "Registering", "Registering user", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.register_url, {
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
        await refreshServerItems();
        markOutcome("success", "Registration succeeded.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Registration failed."));
    });
  };

  refs.login.onclick = async () => {
    await runButtonAction("login", refs.login, "Logging in", "Logging in", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.login_url, {
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
        await refreshServerItems();
        markOutcome("success", "Login succeeded.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Login failed."));
    });
  };

  refs.googleLogin.onclick = () => {
    markOutcome("pending", "Redirecting to Google login.");
    const url = new URL(config.google_start_url, window.location.origin);
    url.searchParams.set("redirect_uri", config.redirect_uri);
    window.location.assign(url.toString());
  };

  refs.refresh.onclick = async () => {
    await runButtonAction("refresh", refs.refresh, "Refreshing", "Refreshing session tokens", async () => {
      const { response, body } = await callSandboxApi<AuthTokensPayload>(config.refresh_url, {
        method: "POST",
        body: JSON.stringify({ refresh_token: state.refreshToken }),
      });

      if (response.ok && body && typeof body === "object") {
        applyTokens(body as AuthTokensPayload);
        markOutcome("success", "Tokens refreshed.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Refresh failed."));
    });
  };

  refs.logout.onclick = async () => {
    await runButtonAction("logout", refs.logout, "Logging out", "Logging out", async () => {
      const { response, body } = await callSandboxApi(config.logout_url, { method: "POST" }, true);

      if (response.ok) {
        await disconnectPowerSync();
        clearTokens();
        renderItems(refs.serverItems, [], "Sign in to inspect the server source rows.");
        markOutcome("success", "Logged out.");
        return;
      }

      markOutcome("error", extractErrorMessage(body, "Logout failed."));
    });
  };

  refs.connect.onclick = async () => {
    await runButtonAction("connect", refs.connect, "Connecting", "Connecting to PowerSync", async () => {
      try {
        await connectPowerSync();
        markOutcome("success", "PowerSync connected.");
      } catch (error) {
        markOutcome("error", error instanceof Error ? error.message : "PowerSync connection failed.");
      }
    });
  };

  refs.disconnect.onclick = async () => {
    await runButtonAction("disconnect", refs.disconnect, "Disconnecting", "Disconnecting PowerSync", async () => {
      await disconnectPowerSync();
      markOutcome("success", "PowerSync disconnected.");
    });
  };

  refs.createItem.onclick = async () => {
    await runButtonAction("create-item", refs.createItem, "Creating", "Creating local demo item", async () => {
      await createLocalItem();
      markOutcome("success", "Local demo item created.");
    });
  };

  refs.refreshServer.onclick = async () => {
    await runButtonAction("refresh-server", refs.refreshServer, "Refreshing", "Refreshing source snapshot", async () => {
      await refreshServerItems();
      markOutcome("success", "Server source snapshot refreshed.");
    });
  };

  refs.localItems.addEventListener("click", async (event) => {
    const target = event.target;

    if (!(target instanceof HTMLButtonElement)) {
      return;
    }

    const action = target.dataset.action;
    const itemId = target.dataset.id;

    if (!action || !itemId) {
      return;
    }

    const actionKey = `${action}:${itemId}`;
    const label = action === "update" ? "Updating" : "Deleting";
    const pending = action === "update" ? "Updating local demo item" : "Deleting local demo item";

    await runButtonAction(actionKey, target, label, pending, async () => {
      if (action === "update") {
        await updateLocalItem(itemId);
        markOutcome("success", "Local demo item updated.");
        return;
      }

      await deleteLocalItem(itemId);
      markOutcome("success", "Local demo item deleted.");
    });
  });
}

async function bootstrap(): Promise<void> {
  refs.clientLabel.textContent = config.db_filename;
  syncTokenFields();
  renderLastResponse({});
  renderStatus();
  renderItems(refs.localItems, [], "Connect PowerSync to mirror demo rows locally.");
  renderItems(refs.serverItems, [], "Sign in to inspect the server source rows.");
  renderControls();
  registerHandlers();

  await handleOAuthReturn();

  if (state.accessToken && !state.currentUser) {
    await loadCurrentUser();
  }

  await refreshServerItems();

  if (state.connected && state.accessToken && state.currentUser?.user_id) {
    try {
      await connectPowerSync();
      markOutcome("success", "PowerSync reconnected from stored session state.");
    } catch (error) {
      markOutcome("error", error instanceof Error ? error.message : "PowerSync reconnect failed.");
    }
  }

  renderStatus();
  renderControls();
}

void bootstrap();
