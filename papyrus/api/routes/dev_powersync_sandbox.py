"""Development-only PowerSync sandbox routes."""

from __future__ import annotations

import tarfile
from functools import lru_cache
from io import BytesIO
from re import sub
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.schemas.powersync_demo import (
    PowerSyncDemoItem,
    PowerSyncDemoItemList,
    PowerSyncSandboxConfigResponse,
    PowerSyncUploadRequest,
    PowerSyncUploadResponse,
)
from papyrus.services import powersync_demo as powersync_demo_service

router = APIRouter(tags=["Dev"])
DBSession = Annotated[AsyncSession, Depends(get_db)]
POWERSYNC_WEB_SDK_VERSION = "1.37.0"
POWERSYNC_NPM_TARBALL_URL = f"https://registry.npmjs.org/@powersync/web/-/web-{POWERSYNC_WEB_SDK_VERSION}.tgz"


def _normalize_client_label(raw_value: str | None) -> str:
    candidate = sub(r"[^a-zA-Z0-9_-]+", "-", (raw_value or "one").strip()).strip("-").lower()

    if not candidate:
        return "one"

    return candidate[:40]


def _build_redirect_uri(request: Request) -> str:
    redirect_uri = str(request.url_for("powersync_sandbox_page"))
    client = request.query_params.get("client")

    if client:
        redirect_uri = f"{redirect_uri}?client={client}"

    return redirect_uri


def _validate_dist_asset_path(asset_path: str) -> str:
    normalized = asset_path.strip()

    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise KeyError(asset_path)

    return normalized


@lru_cache(maxsize=1)
def _load_powersync_dist_assets() -> dict[str, bytes]:
    with urlopen(POWERSYNC_NPM_TARBALL_URL, timeout=20) as response:
        archive_bytes = response.read()

    assets: dict[str, bytes] = {}

    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith("package/dist/"):
                continue

            extracted = archive.extractfile(member)

            if extracted is None:
                continue

            relative_path = member.name.removeprefix("package/dist/")
            assets[relative_path] = extracted.read()

    return assets


@lru_cache(maxsize=64)
def _fetch_dist_asset(asset_path: str) -> bytes:
    normalized = _validate_dist_asset_path(asset_path)
    assets = _load_powersync_dist_assets()

    if normalized not in assets:
        raise KeyError(normalized)

    return assets[normalized]


def _asset_media_type(asset_path: str) -> str:
    if asset_path.endswith(".wasm"):
        return "application/wasm"

    if asset_path.endswith(".map"):
        return "application/json"

    return "text/javascript"


def _sandbox_html(request: Request) -> str:
    app_js_url = str(request.url_for("powersync_sandbox_app_js"))

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Papyrus PowerSync Sandbox</title>
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
        max-width: 1280px;
        margin: 0 auto;
        padding: 32px 20px 48px;
      }}
      h1, h2 {{ margin: 0 0 12px; }}
      p {{ margin: 0 0 16px; color: var(--muted); }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
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
        min-height: 96px;
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
      .status {{
        font-size: 0.95rem;
        color: var(--muted);
      }}
      .items {{
        display: grid;
        gap: 10px;
      }}
      .item {{
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 12px;
        background: #fff;
      }}
      .item strong {{
        display: block;
        margin-bottom: 4px;
      }}
      .item-actions {{
        display: flex;
        gap: 8px;
        margin-top: 10px;
      }}
      .item-actions button {{
        margin-top: 0;
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
        <h1>PowerSync Sandbox</h1>
        <p>Dev-only page for validating Papyrus auth, PowerSync connectivity, queue uploads, and cross-tab sync.</p>
      </div>

      <div class="grid">
        <section class="panel">
          <h2>Auth</h2>
          <label for="email">Email</label>
          <input id="email" type="email" value="powersync@example.com" />
          <label for="password">Password</label>
          <input id="password" type="password" value="SecureP@ss123" />
          <label for="display-name">Display Name</label>
          <input id="display-name" type="text" value="PowerSync User" />
          <label for="client-type">Client Type</label>
          <input id="client-type" type="text" value="web" />
          <label for="device-label">Device Label</label>
          <input id="device-label" type="text" value="powersync-sandbox" />
          <div class="inline">
            <button id="register">Register</button>
            <button id="login">Login</button>
            <button id="refresh">Refresh</button>
          </div>
          <div class="inline">
            <button id="logout" class="secondary">Logout</button>
            <button id="google-login" class="secondary">Google Login</button>
          </div>
          <p id="auth-status" class="status">Signed out.</p>
        </section>

        <section class="panel">
          <h2>PowerSync</h2>
          <div class="inline">
            <button id="connect">Connect</button>
            <button id="disconnect" class="secondary">Disconnect</button>
          </div>
          <label for="title">New Item Title</label>
          <input id="title" type="text" value="Example synced item" />
          <label for="notes">Notes</label>
          <textarea id="notes">Created in the PowerSync sandbox.</textarea>
          <div class="inline">
            <button id="create-item">Create Item</button>
            <button id="refresh-server" class="secondary">Refresh Server Snapshot</button>
          </div>
          <p id="sync-status" class="status">Disconnected.</p>
        </section>

        <section class="panel">
          <h2>Session</h2>
          <label for="access-token">Access Token</label>
          <textarea id="access-token"></textarea>
          <label for="refresh-token">Refresh Token</label>
          <textarea id="refresh-token"></textarea>
        </section>

        <section class="panel">
          <h2>Local Synced Items</h2>
          <p id="client-label" class="status"></p>
          <div id="local-items" class="items"></div>
        </section>

        <section class="panel">
          <h2>Server Source Snapshot</h2>
          <div id="server-items" class="items"></div>
        </section>

        <section class="panel" style="grid-column: 1 / -1;">
          <h2>Last Response</h2>
          <pre id="last-response">{{}}</pre>
        </section>
      </div>
    </main>

    <script type="module" src="{app_js_url}"></script>
  </body>
</html>"""


def _sandbox_app_js() -> str:
    return """
const configResponse = await fetch(`/__dev/powersync-sandbox/config${window.location.search}`);
const config = await configResponse.json();
const storageKey = `papyrus-powersync-sandbox:${config.db_filename}`;
const state = JSON.parse(localStorage.getItem(storageKey) || "{}");

const refs = {
  email: document.getElementById("email"),
  password: document.getElementById("password"),
  displayName: document.getElementById("display-name"),
  clientType: document.getElementById("client-type"),
  deviceLabel: document.getElementById("device-label"),
  title: document.getElementById("title"),
  notes: document.getElementById("notes"),
  accessToken: document.getElementById("access-token"),
  refreshToken: document.getElementById("refresh-token"),
  authStatus: document.getElementById("auth-status"),
  syncStatus: document.getElementById("sync-status"),
  clientLabel: document.getElementById("client-label"),
  localItems: document.getElementById("local-items"),
  serverItems: document.getElementById("server-items"),
  lastResponse: document.getElementById("last-response"),
};

refs.clientLabel.textContent = `Local database: ${config.db_filename}`;

let powersyncModule;
let db;
let localWatch;

function saveState() {
  localStorage.setItem(storageKey, JSON.stringify(state));
}

function renderLastResponse(value) {
  refs.lastResponse.textContent = JSON.stringify(value, null, 2);
}

function setTokens(payload) {
  if (payload?.access_token) state.accessToken = payload.access_token;
  if (payload?.refresh_token) state.refreshToken = payload.refresh_token;
  if (payload?.user) state.currentUser = payload.user;
  refs.accessToken.value = state.accessToken || "";
  refs.refreshToken.value = state.refreshToken || "";
  renderAuthStatus();
  saveState();
}

function clearTokens() {
  delete state.accessToken;
  delete state.refreshToken;
  delete state.currentUser;
  refs.accessToken.value = "";
  refs.refreshToken.value = "";
  renderAuthStatus();
  saveState();
}

function renderAuthStatus() {
  if (state.currentUser?.email) {
    refs.authStatus.textContent = `Signed in as ${state.currentUser.email}`;
    return;
  }

  refs.authStatus.textContent = "Signed out.";
}

function renderSyncStatus(message) {
  refs.syncStatus.textContent = message;
}

function renderItems(target, items, emptyMessage) {
  if (!items.length) {
    target.innerHTML = `<p class="status">${emptyMessage}</p>`;
    return;
  }

  target.innerHTML = items.map((item) => `
    <div class="item">
      <strong>${escapeHtml(item.title || "Untitled Item")}</strong>
      <div class="status">id: ${escapeHtml(item.item_id || item.id)}</div>
      <div class="status">updated: ${escapeHtml(item.updated_at || "")}</div>
      <p>${escapeHtml(item.notes || "")}</p>
      ${target === refs.localItems ? `
        <div class="item-actions">
          <button data-action="update" data-id="${escapeHtml(item.id || item.item_id)}">Update</button>
          <button class="secondary" data-action="delete" data-id="${escapeHtml(item.id || item.item_id)}">Delete</button>
        </div>
      ` : ""}
    </div>
  `).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function callApi(url, options = {}, useAuth = false) {
  const headers = new Headers(options.headers || {});

  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  if (useAuth && state.accessToken) {
    headers.set("Authorization", `Bearer ${state.accessToken}`);
  }

  const response = await fetch(url, { ...options, headers });
  const text = await response.text();
  let body;

  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }

  renderLastResponse({ status: response.status, body });
  return { response, body };
}

async function loadCurrentUser() {
  if (!state.accessToken) {
    renderAuthStatus();
    return;
  }

  const { response, body } = await callApi(config.me_url, { method: "GET" }, true);

  if (response.ok && body) {
    state.currentUser = body;
    saveState();
  }

  renderAuthStatus();
}

async function loadPowerSyncModule() {
  if (!powersyncModule) {
    powersyncModule = await import(config.sdk_module_url);
  }

  return powersyncModule;
}

function buildSchema(module) {
  const demoItems = new module.Table({
    owner_user_id: module.column.text,
    title: module.column.text,
    notes: module.column.text,
    created_at: module.column.text,
    updated_at: module.column.text,
  });

  return new module.Schema({ demo_items: demoItems });
}

async function connectPowerSync() {
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
    async fetchCredentials() {
      const { response, body } = await callApi(config.powersync_token_url, { method: "POST" }, true);

      if (!response.ok || !body?.token) {
        throw new Error(body?.error?.message || "Failed to fetch PowerSync credentials");
      }

      return {
        endpoint: config.powersync_endpoint,
        token: body.token,
      };
    },

    async uploadData(database) {
      let transaction = await database.getNextCrudTransaction();

      while (transaction) {
        const { response, body } = await callApi(
          config.upload_url,
          {
            method: "POST",
            body: JSON.stringify({ batch: transaction.crud }),
          },
          true,
        );

        if (!response.ok) {
          throw new Error(body?.error?.message || "Failed to upload PowerSync changes");
        }

        await transaction.complete();
        transaction = await database.getNextCrudTransaction();
      }

      await refreshServerItems();
    },
  };

  await db.connect(connector);
  state.connected = true;
  saveState();
  renderSyncStatus(`Connected to ${config.powersync_endpoint}`);
  startWatchingLocalItems();
  await refreshServerItems();
}

async function disconnectPowerSync() {
  if (!db) {
    renderSyncStatus("Disconnected.");
    return;
  }

  if (localWatch?.unsubscribe) {
    localWatch.unsubscribe();
  }

  if (localWatch?.close) {
    localWatch.close();
  }

  await db.close({ disconnect: true });
  db = null;
  localWatch = null;
  delete state.connected;
  saveState();
  renderSyncStatus("Disconnected.");
  renderItems(refs.localItems, [], "No local synced items yet.");
}

function normalizeWatchRows(result) {
  const rows = result?.rows?._array ?? result?.rows ?? [];

  if (Array.isArray(rows)) {
    return rows.map((row) => ({
      id: row.id,
      title: row.title,
      notes: row.notes,
      owner_user_id: row.owner_user_id,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }));
  }

  return [];
}

function startWatchingLocalItems() {
  if (!db) {
    return;
  }

  localWatch = db.watch(
    "SELECT id, owner_user_id, title, notes, created_at, updated_at FROM demo_items ORDER BY updated_at DESC, id DESC",
    [],
    {
      onResult: (result) => {
        state.localItems = normalizeWatchRows(result);
        renderItems(refs.localItems, state.localItems || [], "No local synced items yet.");
      },
    },
  );
}

async function refreshServerItems() {
  if (!state.accessToken) {
    renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
    return;
  }

  const { response, body } = await callApi(config.items_url, { method: "GET" }, true);

  if (!response.ok || !body?.items) {
    return;
  }

  renderItems(refs.serverItems, body.items, "No source rows yet.");
}

async function createLocalItem() {
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

async function updateLocalItem(itemId) {
  if (!db) {
    return;
  }

  await db.execute(
    "UPDATE demo_items SET title = ?, updated_at = datetime('now') WHERE id = ?",
    [`Updated at ${new Date().toISOString()}`, itemId],
  );
}

async function deleteLocalItem(itemId) {
  if (!db) {
    return;
  }

  await db.execute("DELETE FROM demo_items WHERE id = ?", [itemId]);
}

async function handleOAuthReturn() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error");

  if (code) {
    const { response, body } = await callApi(config.exchange_url, {
      method: "POST",
      body: JSON.stringify({
        code,
        client_type: refs.clientType.value || "web",
        device_label: refs.deviceLabel.value || null,
      }),
    });

    if (response.ok && body) {
      setTokens(body);
    }

    history.replaceState(null, "", config.redirect_uri);
    return;
  }

  if (error) {
    renderLastResponse({ status: 302, body: { error } });
    history.replaceState(null, "", config.redirect_uri);
  }
}

document.getElementById("register").onclick = async () => {
  const { body } = await callApi(config.register_url, {
    method: "POST",
    body: JSON.stringify({
      email: refs.email.value,
      password: refs.password.value,
      display_name: refs.displayName.value,
      client_type: refs.clientType.value || "web",
      device_label: refs.deviceLabel.value || null,
    }),
  });

  if (body) {
    setTokens(body);
    await refreshServerItems();
  }
};

document.getElementById("login").onclick = async () => {
  const { body } = await callApi(config.login_url, {
    method: "POST",
    body: JSON.stringify({
      email: refs.email.value,
      password: refs.password.value,
      client_type: refs.clientType.value || "web",
      device_label: refs.deviceLabel.value || null,
    }),
  });

  if (body) {
    setTokens(body);
    await refreshServerItems();
  }
};

document.getElementById("refresh").onclick = async () => {
  const { body } = await callApi(config.refresh_url, {
    method: "POST",
    body: JSON.stringify({ refresh_token: state.refreshToken || refs.refreshToken.value }),
  });

  if (body) {
    setTokens(body);
  }
};

document.getElementById("logout").onclick = async () => {
  await callApi(config.logout_url, { method: "POST" }, true);
  await disconnectPowerSync();
  clearTokens();
  renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
};

document.getElementById("google-login").onclick = async () => {
  const url = new URL(config.google_start_url, window.location.origin);
  url.searchParams.set("redirect_uri", config.redirect_uri);
  window.location.assign(url.toString());
};

document.getElementById("connect").onclick = connectPowerSync;
document.getElementById("disconnect").onclick = disconnectPowerSync;
document.getElementById("create-item").onclick = createLocalItem;
document.getElementById("refresh-server").onclick = refreshServerItems;

refs.accessToken.addEventListener("input", () => {
  state.accessToken = refs.accessToken.value.trim();
  saveState();
});

refs.refreshToken.addEventListener("input", () => {
  state.refreshToken = refs.refreshToken.value.trim();
  saveState();
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

renderAuthStatus();
renderItems(refs.localItems, [], "No local synced items yet.");
renderItems(refs.serverItems, [], "Authenticate to inspect the source database.");
renderSyncStatus(state.connected ? "Reconnecting..." : "Disconnected.");
setTokens(state);
await handleOAuthReturn();
await loadCurrentUser();
await refreshServerItems();

if (state.connected && state.accessToken && state.currentUser?.user_id) {
  await connectPowerSync();
}
"""


@router.get("/__dev/powersync-sandbox", response_class=HTMLResponse, name="powersync_sandbox_page")
async def powersync_sandbox_page(request: Request) -> HTMLResponse:
    """Render the development-only PowerSync validation sandbox."""
    return HTMLResponse(_sandbox_html(request))


@router.get("/__dev/powersync-sandbox/app.js", name="powersync_sandbox_app_js")
async def powersync_sandbox_app_js() -> Response:
    """Serve the browser module for the PowerSync sandbox."""
    return Response(_sandbox_app_js(), media_type="text/javascript")


@router.get("/__dev/powersync-sandbox/worker/{asset_path:path}", name="powersync_sandbox_worker_asset")
async def powersync_sandbox_worker_asset(asset_path: str) -> Response:
    """Serve PowerSync worker assets from the sandbox origin."""
    try:
        content = _fetch_dist_asset(f"worker/{asset_path}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown PowerSync worker asset") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch PowerSync worker asset") from exc

    headers = {"Cache-Control": "public, max-age=86400", "X-Papyrus-Vendor-Path": asset_path}
    return Response(content, media_type=_asset_media_type(asset_path), headers=headers)


@router.get(
    "/__dev/powersync-sandbox/config",
    response_model=PowerSyncSandboxConfigResponse,
)
async def powersync_sandbox_config(request: Request, client: str | None = None) -> PowerSyncSandboxConfigResponse:
    """Return runtime config for the PowerSync sandbox page."""
    settings = get_settings()
    client_label = _normalize_client_label(client)
    api_prefix = settings.api_prefix
    redirect_uri = _build_redirect_uri(request)

    return PowerSyncSandboxConfigResponse(
        register_url=f"{api_prefix}/auth/register",
        login_url=f"{api_prefix}/auth/login",
        refresh_url=f"{api_prefix}/auth/refresh",
        logout_url=f"{api_prefix}/auth/logout",
        google_start_url=f"{api_prefix}/auth/oauth/google/start",
        exchange_url=f"{api_prefix}/auth/exchange-code",
        me_url=f"{api_prefix}/users/me",
        powersync_token_url=f"{api_prefix}/auth/powersync-token",
        powersync_endpoint=settings.powersync_service_url,
        items_url="/__dev/powersync-demo/items",
        upload_url="/__dev/powersync-demo/upload",
        redirect_uri=redirect_uri,
        sdk_module_url=f"https://esm.sh/@powersync/web@{POWERSYNC_WEB_SDK_VERSION}?bundle",
        db_worker_url=str(request.url_for("powersync_sandbox_worker_asset", asset_path="WASQLiteDB.umd.js")),
        sync_worker_url=str(
            request.url_for("powersync_sandbox_worker_asset", asset_path="SharedSyncImplementation.umd.js")
        ),
        db_filename=f"powersync-demo-{client_label}.db",
    )


@router.get(
    "/__dev/powersync-demo/items",
    response_model=PowerSyncDemoItemList,
)
async def list_powersync_demo_items(user_id: CurrentUserId, db: DBSession) -> PowerSyncDemoItemList:
    """Return the authenticated user's PowerSync demo rows from the source database."""
    items = await powersync_demo_service.list_demo_items(db, user_id)
    return PowerSyncDemoItemList(items=[PowerSyncDemoItem.model_validate(item) for item in items])


@router.post(
    "/__dev/powersync-demo/upload",
    response_model=PowerSyncUploadResponse,
)
async def upload_powersync_demo_items(
    request: PowerSyncUploadRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> PowerSyncUploadResponse:
    """Apply a PowerSync upload queue batch to the demo source table."""
    applied_count = await powersync_demo_service.apply_upload_batch(db, user_id, request.batch)
    return PowerSyncUploadResponse(applied_count=applied_count)


@router.get("/__dev/powersync-sandbox/{asset_name}")
async def powersync_sandbox_root_asset(asset_name: str) -> Response:
    """Serve root dist assets such as WASM binaries from the sandbox origin."""
    if not (asset_name.endswith(".wasm") or asset_name.endswith(".map")):
        raise HTTPException(status_code=404, detail="Unknown PowerSync root asset")

    try:
        content = _fetch_dist_asset(asset_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown PowerSync root asset") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch PowerSync root asset") from exc

    headers = {"Cache-Control": "public, max-age=86400", "X-Papyrus-Vendor-Path": asset_name}
    return Response(content, media_type=_asset_media_type(asset_name), headers=headers)
