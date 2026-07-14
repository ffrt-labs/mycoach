"""Tests for the offline companion logger PWA route and its install-critical assets."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_logger_page_renders(client: AsyncClient) -> None:
    """Logger shell renders and links its own scoped manifest/service worker."""
    resp = await client.get("/logger")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "/static/logger/manifest.json" in resp.text
    assert "/static/logger/icon-192.png" in resp.text  # apple-touch-icon


async def test_logger_service_worker_scope(client: AsyncClient) -> None:
    """Service worker is served with the header that widens its scope to /logger.

    Without `Service-Worker-Allowed: /logger`, a script at /logger/sw.js can
    only control clients under /logger/ (trailing slash), not the shell at
    /logger itself — install would silently fail to work offline.
    """
    resp = await client.get("/logger/sw.js")
    assert resp.status_code == 200
    assert resp.headers["service-worker-allowed"] == "/logger"
    assert resp.headers["cache-control"] == "no-cache"


async def test_logger_manifest_has_png_icons(client: AsyncClient) -> None:
    """Manifest lists 192px and 512px PNG icons, per Chrome's Android install criteria.

    An SVG-only icon list previously risked the "Install app" prompt never
    appearing on Android's WebAPK path.
    """
    resp = await client.get("/static/logger/manifest.json")
    assert resp.status_code == 200
    manifest = resp.json()

    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes

    png_icons = [icon for icon in manifest["icons"] if icon["type"] == "image/png"]
    assert len(png_icons) == 2


async def test_logger_icon_files_served(client: AsyncClient) -> None:
    """The PNG icon files referenced by the manifest actually exist and serve correctly."""
    icon_192_resp = await client.get("/static/logger/icon-192.png")
    assert icon_192_resp.status_code == 200
    assert icon_192_resp.headers["content-type"] == "image/png"

    icon_512_resp = await client.get("/static/logger/icon-512.png")
    assert icon_512_resp.status_code == 200
    assert icon_512_resp.headers["content-type"] == "image/png"
