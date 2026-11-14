"""The Body page and its dashboard shortcut (ticket #198)."""

from datetime import datetime

import pytest
from httpx import AsyncClient

from mycoach import health

pytestmark = pytest.mark.anyio

PROTOCOL = (
    "Morning, after the bathroom, before food. Waist at the navel, end of a normal exhale, "
    "average 2 readings."
)


async def test_the_page_has_the_form_the_protocol_hint_and_height(client: AsyncClient) -> None:
    resp = await client.get("/body")

    assert resp.status_code == 200
    for field in ('name="measured_at"', 'name="weight_kg"', 'name="waist_cm"', 'name="note"',
                  'name="height_cm"'):  # fmt: skip
        assert field in resp.text
    assert PROTOCOL in resp.text
    assert "No measurements yet" in resp.text


async def test_the_page_lists_entries_in_local_time_with_delete(client: AsyncClient) -> None:
    # 07:00 UTC in July is 08:00 in London.
    recorded = await health.record_body_measurement(
        1, measured_at=datetime(2026, 7, 10, 7, 0), weight_kg=82.4, note="post-holiday"
    )
    await health.set_athlete_height(1, 181.5)

    resp = await client.get("/body")

    assert "82.4" in resp.text
    assert "post-holiday" in resp.text
    assert "10 Jul 2026, 08:00" in resp.text
    assert f"deleteMeasurement({recorded.id})" in resp.text
    assert 'value="181.5"' in resp.text


async def test_body_is_in_the_nav(client: AsyncClient) -> None:
    resp = await client.get("/body")

    assert 'href="/body"' in resp.text


async def test_the_dashboard_has_a_log_weight_waist_shortcut(client: AsyncClient) -> None:
    resp = await client.get("/")

    assert 'href="/body"' in resp.text
    assert "Log weight/waist" in resp.text
