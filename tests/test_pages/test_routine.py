"""Tests for the gym routine builder page route."""

from pathlib import Path

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "mycoach" / "templates"


async def test_routine_page_renders(client: AsyncClient) -> None:
    """Routine page renders with the add-day control and days container present."""
    resp = await client.get("/routine")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="add-day"' in resp.text
    assert 'id="days-container"' in resp.text


def test_no_template_uses_domcontentloaded() -> None:
    """No template wires its script via DOMContentLoaded.

    base.html sets hx-boost="true" on <body>, so nav clicks swap the body via
    htmx AJAX instead of a full page load. DOMContentLoaded has already fired
    by the time htmx executes a swapped-in inline <script>, so a listener
    registered that way never runs and every handler inside it is dead on
    boosted navigation. Every page-level script must run eagerly (e.g. an
    IIFE) instead. This regression bit /routine and /mesocycles.
    """
    offenders = []
    for template in TEMPLATES_DIR.glob("*.html"):
        if "DOMContentLoaded" in template.read_text():
            offenders.append(template.name)
    assert not offenders, f"Templates still use DOMContentLoaded: {offenders}"
