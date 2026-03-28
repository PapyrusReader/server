"""Shared template and asset helpers for development-only pages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from papyrus.config import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_PAGES_ROOT = REPO_ROOT / "frontend" / "dev-pages"
DEV_PAGES_TEMPLATE_DIR = DEV_PAGES_ROOT / "templates"
DEV_PAGES_DIST_DIR = DEV_PAGES_ROOT / "dist"
DEV_PAGES_STATIC_URL = "/__dev/static"

templates = Jinja2Templates(directory=str(DEV_PAGES_TEMPLATE_DIR))


@dataclass(frozen=True)
class DevPageAssets:
    """Resolved asset URLs for a rendered development page."""

    script_urls: list[str]
    css_urls: list[str]


@dataclass(frozen=True)
class ManifestEntry:
    """Minimal Vite manifest entry shape used by the backend renderer."""

    file: str
    css: list[str]
    imports: list[str]
    src: str | None


def _resolve_manifest_path(settings: Settings) -> Path:
    manifest_path = settings.dev_pages_manifest_file

    if manifest_path.is_absolute():
        return manifest_path

    return REPO_ROOT / manifest_path


@lru_cache(maxsize=4)
def _load_manifest(manifest_path_str: str) -> dict[str, ManifestEntry]:
    manifest_path = Path(manifest_path_str)
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    return {
        key: ManifestEntry(
            file=value["file"],
            css=list(value.get("css", [])),
            imports=list(value.get("imports", [])),
            src=value.get("src"),
        )
        for key, value in raw_manifest.items()
    }


def _find_entry(manifest: dict[str, ManifestEntry], entry_module: str) -> tuple[str, ManifestEntry]:
    if entry_module in manifest:
        return entry_module, manifest[entry_module]

    for key, value in manifest.items():
        if value.src == entry_module or key.endswith(entry_module):
            return key, value

    raise KeyError(entry_module)


def _collect_css(
    manifest: dict[str, ManifestEntry],
    entry_key: str,
    *,
    seen_entries: set[str] | None = None,
    seen_css: set[str] | None = None,
) -> list[str]:
    entry_seen = seen_entries or set()
    css_seen = seen_css or set()

    if entry_key in entry_seen:
        return []

    entry_seen.add(entry_key)
    entry = manifest[entry_key]
    css_urls: list[str] = []

    for css_file in entry.css:
        if css_file in css_seen:
            continue

        css_seen.add(css_file)
        css_urls.append(f"{DEV_PAGES_STATIC_URL}/{css_file.lstrip('/')}")

    for imported_key in entry.imports:
        if imported_key not in manifest:
            continue

        css_urls.extend(
            _collect_css(
                manifest,
                imported_key,
                seen_entries=entry_seen,
                seen_css=css_seen,
            )
        )

    return css_urls


def _should_use_vite(settings: Settings) -> bool:
    if settings.dev_pages_use_vite:
        return True

    return settings.debug and not _resolve_manifest_path(settings).exists()


def get_dev_page_assets(entry_module: str, settings: Settings | None = None) -> DevPageAssets:
    """Resolve the JS and CSS URLs for a dev page entry."""
    settings = settings or get_settings()

    if _should_use_vite(settings):
        vite_url = settings.dev_pages_vite_url
        return DevPageAssets(
            script_urls=[f"{vite_url}/@vite/client", f"{vite_url}/{entry_module}"],
            css_urls=[],
        )

    manifest_path = _resolve_manifest_path(settings)
    manifest = _load_manifest(str(manifest_path))
    entry_key, entry = _find_entry(manifest, entry_module)

    return DevPageAssets(
        script_urls=[f"{DEV_PAGES_STATIC_URL}/{entry.file.lstrip('/')}"],
        css_urls=_collect_css(manifest, entry_key),
    )


def render_dev_page(
    request: Request,
    *,
    template_name: str,
    page_title: str,
    page_id: str,
    body_class: str,
    entry_module: str,
    page_config: dict[str, Any],
) -> HTMLResponse:
    """Render a development page shell with either Vite or built assets."""
    settings = get_settings()
    assets = get_dev_page_assets(entry_module, settings)
    context = {
        "request": request,
        "page_title": page_title,
        "page_id": page_id,
        "body_class": body_class,
        "page_config_json": json.dumps(page_config),
        "dev_page_assets": assets,
    }
    return templates.TemplateResponse(request=request, name=template_name, context=context)
