import "../../styles/dev-pages.scss";
import "./page.scss";

import { callApi } from "../../lib/api";
import { getPageConfig } from "../../lib/config";
import { requireElement } from "../../lib/dom";
import { escapeHtml } from "../../lib/html";
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

interface AuthTokensPayload {
  access_token?: string;
  refresh_token?: string;
  user?: CurrentUser;
}

interface CurrentUser {
  user_id: string;
  email?: string | null;
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
};

refs.clientLabel.textContent = `Local database: ${config.db_filename}`;

let powersyncModule: PowerSyncModule | undefined;
let db: any;
let localWatch: { unsubscribe?: () => void; close?: () => void } | null = null;

function persistState(): void {
  saveStoredState(storageKey, state);
}

function renderLastResponse(value: unknown): void {
  refs.lastResponse.textContent = JSON.stringify(value, null, 2);
}

function syncTokenFields(): void {
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
  renderAuthStatus();
  persistState();
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
}

function clearTokens(): void {
  delete state.accessToken;
  delete state.refreshToken;
  delete state.currentUser;
  syncTokenFields();
}

function renderAuthStatus(): void {
  if (state.currentUser?.email) {
    refs.authStatus.textContent = `Signed in as ${state.currentUser.email}`;
    return;
  }

  refs.authStatus.textContent = "Signed out.";
}

function renderSyncStatus(message: string): void {
  refs.syncStatus.textContent = message;
}

function renderItems(target: HTMLElement, items: LocalItem[], emptyMessage: string): void {
  if (items.length === 0) {
    target.innerHTML = `<p class="dev-status">${emptyMessage}</p>`;
    return;
  }

  target.innerHTML = items
    .map(
      (item) => `
        <div class="dev-item">
          <strong>${escapeHtml(item.title ?? "Untitled Item")}</strong>
          <div class="dev-status">id: ${escapeHtml(item.item_id ?? item.id)}</div>
          <div class="dev-status">updated: ${escapeHtml(item.updated_at)}</div>
          <p>${escapeHtml(item.notes)}</p>
          ${
            target === refs.localItems
              ? `
            <div class="dev-item-actions">
              <button data-action="update" data-id="${escapeHtml(item.id ?? item.item_id)}">Update</button>
              <button class="secondary" data-action="delete" data-id="${escapeHtml(item.id ?? item.item_id)}">Delete</button>
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
    renderAuthStatus();
    return;
  }

  const { response, body } = await callSandboxApi<CurrentUser>(config.me_url, { method: "GET" }, true);

  if (response.ok && body && typeof body === "object") {
    state.currentUser = body as CurrentUser;
    persistState();
  }

  renderAuthStatus();
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

async function connectPowerSync(): Promise<void> {
  if (db) {
    renderSyncStatus("Already connected.");
    return;
  }

  if (!state.accessToken || !state.currentUser?.user_id) {
    renderSyncStatus("Authenticate before connecting PowerSync.");
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
      const { response, body } = await callSandboxApi<{ token?: string }>(
        config.powersync_token_url,
        { method: "POST" },
        true,
      );

      if (!response.ok || !body || typeof body !== "object" || typeof body.token !== "string") {
        throw new Error("Failed to fetch PowerSync credentials");
      }

      return {
        endpoint: config.powersync_endpoint,
        token: body.token,
      };
    },

    async uploadData(database: any): Promise<void> {
      let transaction = await database.getNextCrudTransaction();

      while (transaction) {
        const { response, body } = await callSandboxApi(
          config.upload_url,
          {
            method: "POST",
            body: JSON.stringify({ batch: transaction.crud }),
          },
          true,
        );

        if (!response.ok) {
          throw new Error(
            typeof body === "object" && body !== null && "error" in body
              ? String((body as { error?: { message?: string } }).error?.message ?? "Failed to upload PowerSync changes")
              : "Failed to upload PowerSync changes",
          );
        }

        await transaction.complete();
        transaction = await database.getNextCrudTransaction();
      }

      await refreshServerItems();
    },
  };

  await db.connect(connector);
  state.connected = true;
  persistState();
  renderSyncStatus(`Connected to ${config.powersync_endpoint}`);
  startWatchingLocalItems();
  await refreshServerItems();
}

async function disconnectPowerSync(): Promise<void> {
  if (!db) {
    renderSyncStatus("Disconnected.");
    return;
  }

  localWatch?.unsubscribe?.();
  localWatch?.close?.();

  await db.close({ disconnect: true });
  db = null;
  localWatch = null;
  delete state.connected;
  persistState();
  renderSyncStatus("Disconnected.");
  renderItems(refs.localItems, [], "No local synced items yet.");
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
      },
    },
  );
}

async function refreshServerItems(): Promise<void> {
  if (!state.accessToken) {
    renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
    return;
  }

  const { response, body } = await callSandboxApi<{ items?: LocalItem[] }>(config.items_url, { method: "GET" }, true);

  if (!response.ok || !body || typeof body !== "object" || !Array.isArray(body.items)) {
    return;
  }

  renderItems(refs.serverItems, body.items, "No source rows yet.");
}

async function createLocalItem(): Promise<void> {
  if (!db || !state.currentUser?.user_id) {
    renderSyncStatus("Connect PowerSync before creating items.");
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

    if (response.ok && body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }

    history.replaceState(null, "", config.redirect_uri);
    return;
  }

  if (error) {
    renderLastResponse({ status: 302, body: { error } });
    history.replaceState(null, "", config.redirect_uri);
  }
}

function registerHandlers(): void {
  requireElement<HTMLButtonElement>("register").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.register_url, {
      method: "POST",
      body: JSON.stringify({
        email: refs.email.value,
        password: refs.password.value,
        display_name: refs.displayName.value,
        client_type: refs.clientType.value || "web",
        device_label: refs.deviceLabel.value || null,
      }),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
      await refreshServerItems();
    }
  };

  requireElement<HTMLButtonElement>("login").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.login_url, {
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
      await refreshServerItems();
    }
  };

  requireElement<HTMLButtonElement>("refresh").onclick = async () => {
    const { body } = await callSandboxApi<AuthTokensPayload>(config.refresh_url, {
      method: "POST",
      body: JSON.stringify({ refresh_token: state.refreshToken ?? refs.refreshToken.value }),
    });

    if (body && typeof body === "object") {
      applyTokens(body as AuthTokensPayload);
    }
  };

  requireElement<HTMLButtonElement>("logout").onclick = async () => {
    await callSandboxApi(config.logout_url, { method: "POST" }, true);
    await disconnectPowerSync();
    clearTokens();
    renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
  };

  requireElement<HTMLButtonElement>("google-login").onclick = () => {
    const url = new URL(config.google_start_url, window.location.origin);
    url.searchParams.set("redirect_uri", config.redirect_uri);
    window.location.assign(url.toString());
  };

  requireElement<HTMLButtonElement>("connect").onclick = () => {
    void connectPowerSync();
  };
  requireElement<HTMLButtonElement>("disconnect").onclick = () => {
    void disconnectPowerSync();
  };
  requireElement<HTMLButtonElement>("create-item").onclick = () => {
    void createLocalItem();
  };
  requireElement<HTMLButtonElement>("refresh-server").onclick = () => {
    void refreshServerItems();
  };

  refs.accessToken.addEventListener("input", () => {
    state.accessToken = refs.accessToken.value.trim();
    syncTokenFields();
  });

  refs.refreshToken.addEventListener("input", () => {
    state.refreshToken = refs.refreshToken.value.trim();
    syncTokenFields();
  });

  refs.localItems.addEventListener("click", async (event) => {
    const target = event.target;

    if (!(target instanceof HTMLElement)) {
      return;
    }

    const action = target.dataset.action;
    const itemId = target.dataset.id;

    if (!action || !itemId) {
      return;
    }

    if (action === "update") {
      await updateLocalItem(itemId);
      return;
    }

    if (action === "delete") {
      await deleteLocalItem(itemId);
    }
  });
}

async function bootstrap(): Promise<void> {
  registerHandlers();
  refs.accessToken.value = state.accessToken ?? "";
  refs.refreshToken.value = state.refreshToken ?? "";
  renderAuthStatus();
  renderItems(refs.localItems, [], "No local synced items yet.");
  renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
  renderSyncStatus(state.connected ? "Reconnecting..." : "Disconnected.");
  await handleOAuthReturn();
  await loadCurrentUser();
  await refreshServerItems();

  if (state.connected && state.accessToken && state.currentUser?.user_id) {
    await connectPowerSync();
  }
}

void bootstrap();
