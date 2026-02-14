"""Tests for availability API endpoints."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: AsyncSession) -> User:
    user = User(id=1, name="Test User", email="test@example.com")
    session.add(user)
    await session.commit()
    return user


def _next_monday(ref: date | None = None) -> date:
    d = ref or date.today()
    days_ahead = 7 - d.weekday()
    return d + timedelta(days=days_ahead)


@pytest.mark.asyncio
async def test_set_availability(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    monday = _next_monday()
    resp = await client.post(
        "/api/availability",
        json={
            "week_start": monday.isoformat(),
            "slots": [
                {
                    "day_of_week": 0,
                    "start_time": "07:00:00",
                    "duration_minutes": 90,
                    "preferred_sport": "gym",
                },
                {
                    "day_of_week": 2,
                    "start_time": "18:00:00",
                    "duration_minutes": 60,
                    "preferred_sport": "swimming",
                },
            ],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data) == 2
    assert data[0]["day_of_week"] == 0
    assert data[0]["preferred_sport"] == "gym"
    assert data[1]["day_of_week"] == 2
    assert data[1]["preferred_sport"] == "swimming"


@pytest.mark.asyncio
async def test_set_availability_replaces_existing(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    monday = _next_monday()
    payload = {
        "week_start": monday.isoformat(),
        "slots": [
            {
                "day_of_week": 0,
                "start_time": "07:00:00",
                "duration_minutes": 90,
                "preferred_sport": "gym",
            },
        ],
    }
    await client.post("/api/availability", json=payload)

    # Replace with new slots
    payload["slots"] = [
        {
            "day_of_week": 1,
            "start_time": "08:00:00",
            "duration_minutes": 60,
            "preferred_sport": "padel",
        },
    ]
    resp = await client.post("/api/availability", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert len(data) == 1
    assert data[0]["day_of_week"] == 1
    assert data[0]["preferred_sport"] == "padel"

    # Verify old slots are gone
    resp2 = await client.get(f"/api/availability/{monday.isoformat()}")
    assert len(resp2.json()) == 1


@pytest.mark.asyncio
async def test_set_availability_rejects_non_monday(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    tuesday = _next_monday() + timedelta(days=1)
    resp = await client.post(
        "/api/availability",
        json={"week_start": tuesday.isoformat(), "slots": []},
    )
    assert resp.status_code == 422
    assert "Monday" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_week_availability(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    monday = _next_monday()
    await client.post(
        "/api/availability",
        json={
            "week_start": monday.isoformat(),
            "slots": [
                {
                    "day_of_week": 3,
                    "start_time": "19:00:00",
                    "duration_minutes": 60,
                    "preferred_sport": "padel",
                },
                {
                    "day_of_week": 0,
                    "start_time": "07:00:00",
                    "duration_minutes": 90,
                    "preferred_sport": "gym",
                },
            ],
        },
    )

    resp = await client.get(f"/api/availability/{monday.isoformat()}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Should be ordered by day_of_week
    assert data[0]["day_of_week"] == 0
    assert data[1]["day_of_week"] == 3


@pytest.mark.asyncio
async def test_get_week_availability_rejects_non_monday(client: AsyncClient) -> None:
    wednesday = _next_monday() + timedelta(days=2)
    resp = await client.get(f"/api/availability/{wednesday.isoformat()}")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_week_availability_empty(client: AsyncClient) -> None:
    monday = _next_monday()
    resp = await client.get(f"/api/availability/{monday.isoformat()}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_update_slot(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    monday = _next_monday()
    create_resp = await client.post(
        "/api/availability",
        json={
            "week_start": monday.isoformat(),
            "slots": [
                {
                    "day_of_week": 0,
                    "start_time": "07:00:00",
                    "duration_minutes": 90,
                    "preferred_sport": "gym",
                },
            ],
        },
    )
    slot_id = create_resp.json()[0]["id"]

    resp = await client.put(
        f"/api/availability/{slot_id}",
        json={
            "day_of_week": 1,
            "start_time": "08:30:00",
            "duration_minutes": 60,
            "preferred_sport": "swimming",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["day_of_week"] == 1
    assert data["duration_minutes"] == 60
    assert data["preferred_sport"] == "swimming"


@pytest.mark.asyncio
async def test_update_slot_not_found(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/availability/999",
        json={
            "day_of_week": 0,
            "start_time": "07:00:00",
            "duration_minutes": 90,
            "preferred_sport": "gym",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_slot(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    monday = _next_monday()
    create_resp = await client.post(
        "/api/availability",
        json={
            "week_start": monday.isoformat(),
            "slots": [
                {
                    "day_of_week": 0,
                    "start_time": "07:00:00",
                    "duration_minutes": 90,
                    "preferred_sport": "gym",
                },
                {
                    "day_of_week": 2,
                    "start_time": "18:00:00",
                    "duration_minutes": 60,
                    "preferred_sport": "swimming",
                },
            ],
        },
    )
    slot_id = create_resp.json()[0]["id"]

    resp = await client.delete(f"/api/availability/{slot_id}")
    assert resp.status_code == 204

    # Verify only one slot remains
    resp2 = await client.get(f"/api/availability/{monday.isoformat()}")
    assert len(resp2.json()) == 1


@pytest.mark.asyncio
async def test_delete_slot_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/availability/999")
    assert resp.status_code == 404
