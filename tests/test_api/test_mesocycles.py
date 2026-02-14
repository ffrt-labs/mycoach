"""Tests for mesocycle API endpoints and context function."""

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import get_mesocycle_context
from mycoach.models.coaching import MesocycleConfig
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user(session: AsyncSession) -> User:
    user = User(id=1, name="Test User", email="test@example.com")
    session.add(user)
    await session.commit()
    return user


# ── API endpoint tests ──


@pytest.mark.asyncio
async def test_create_mesocycle(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    resp = await client.post(
        "/api/mesocycles",
        json={
            "sport": "gym",
            "block_length_weeks": 4,
            "current_week": 1,
            "phase": "build",
            "start_date": "2026-02-09",
            "progression_rules": '{"weight_increment_kg": 2.5}',
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["sport"] == "gym"
    assert data["block_length_weeks"] == 4
    assert data["current_week"] == 1
    assert data["phase"] == "build"
    assert data["user_id"] == 1
    assert data["progression_rules"] == '{"weight_increment_kg": 2.5}'


@pytest.mark.asyncio
async def test_create_mesocycle_duplicate_sport(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    payload = {
        "sport": "gym",
        "block_length_weeks": 4,
        "current_week": 1,
        "phase": "build",
        "start_date": "2026-02-09",
    }
    await client.post("/api/mesocycles", json=payload)
    resp = await client.post("/api/mesocycles", json=payload)
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_mesocycles(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post(
        "/api/mesocycles",
        json={"sport": "swimming", "start_date": "2026-02-09"},
    )
    await client.post(
        "/api/mesocycles",
        json={"sport": "gym", "start_date": "2026-02-09"},
    )

    resp = await client.get("/api/mesocycles")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Ordered by sport name
    assert data[0]["sport"] == "gym"
    assert data[1]["sport"] == "swimming"


@pytest.mark.asyncio
async def test_list_mesocycles_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/mesocycles")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_mesocycle_by_sport(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post(
        "/api/mesocycles",
        json={"sport": "gym", "start_date": "2026-02-09", "phase": "deload"},
    )
    resp = await client.get("/api/mesocycles/gym")
    assert resp.status_code == 200
    assert resp.json()["sport"] == "gym"
    assert resp.json()["phase"] == "deload"


@pytest.mark.asyncio
async def test_get_mesocycle_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/mesocycles/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_mesocycle(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post(
        "/api/mesocycles",
        json={"sport": "gym", "start_date": "2026-02-09"},
    )

    resp = await client.put(
        "/api/mesocycles/gym",
        json={"current_week": 3, "phase": "peak"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_week"] == 3
    assert data["phase"] == "peak"


@pytest.mark.asyncio
async def test_update_mesocycle_not_found(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/mesocycles/nonexistent",
        json={"current_week": 2},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_mesocycle(client: AsyncClient) -> None:
    async with test_session() as session:
        await _create_user(session)

    await client.post(
        "/api/mesocycles",
        json={"sport": "gym", "start_date": "2026-02-09"},
    )
    resp = await client.delete("/api/mesocycles/gym")
    assert resp.status_code == 204

    # Verify it's gone
    resp2 = await client.get("/api/mesocycles/gym")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_mesocycle_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/mesocycles/nonexistent")
    assert resp.status_code == 404


# ── Context function tests ──


@pytest.mark.asyncio
async def test_mesocycle_context_no_configs() -> None:
    async with test_session() as session:
        result = await get_mesocycle_context(session, user_id=1)
    assert result is None


@pytest.mark.asyncio
async def test_mesocycle_context_single_sport() -> None:
    async with test_session() as session:
        user = User(id=1, name="Test", email="t@t.com")
        session.add(user)
        cfg = MesocycleConfig(
            user_id=1,
            sport="gym",
            block_length_weeks=4,
            current_week=2,
            phase="build",
            start_date=date(2026, 2, 9),
            progression_rules='{"weight_increment_kg": 2.5}',
        )
        session.add(cfg)
        await session.commit()

        result = await get_mesocycle_context(session, user_id=1)

    assert result is not None
    assert "gym" in result
    assert "Week 2/4" in result
    assert "build phase" in result
    assert "Progression rules" in result
    assert "DELOAD" not in result


@pytest.mark.asyncio
async def test_mesocycle_context_deload_week() -> None:
    async with test_session() as session:
        user = User(id=1, name="Test", email="t@t.com")
        session.add(user)
        cfg = MesocycleConfig(
            user_id=1,
            sport="gym",
            block_length_weeks=4,
            current_week=4,
            phase="deload",
            start_date=date(2026, 2, 9),
        )
        session.add(cfg)
        await session.commit()

        result = await get_mesocycle_context(session, user_id=1)

    assert result is not None
    assert "DELOAD WEEK" in result


@pytest.mark.asyncio
async def test_mesocycle_context_multiple_sports() -> None:
    async with test_session() as session:
        user = User(id=1, name="Test", email="t@t.com")
        session.add(user)
        session.add(
            MesocycleConfig(
                user_id=1, sport="gym", block_length_weeks=4,
                current_week=2, phase="build", start_date=date(2026, 2, 9),
            )
        )
        session.add(
            MesocycleConfig(
                user_id=1, sport="swimming", block_length_weeks=6,
                current_week=3, phase="build", start_date=date(2026, 1, 12),
            )
        )
        await session.commit()

        result = await get_mesocycle_context(session, user_id=1)

    assert result is not None
    assert "gym" in result
    assert "swimming" in result
    assert "Week 2/4" in result
    assert "Week 3/6" in result
