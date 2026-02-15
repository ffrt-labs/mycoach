"""Tests for scheduler API endpoints."""

from httpx import AsyncClient


async def test_scheduler_status_no_scheduler(client: AsyncClient) -> None:
    """When scheduler is not running (test mode), should return empty state."""
    resp = await client.get("/api/system/scheduler")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["jobs"] == []
