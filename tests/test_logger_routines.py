"""Tests for the API-key-guarded routine endpoint the offline logger prefills from."""

import pytest

from mycoach.models.user import User

TOKEN = "secret-token"


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


@pytest.fixture(autouse=True)
def _api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Overrides any value from .env for deterministic auth tests.
    monkeypatch.setenv("MYCOACH_API_TOKEN", TOKEN)


def _routine_payload() -> dict:
    return {
        "name": "PPL",
        "days": [
            {
                "name": "Push Day",
                "day_of_week": 0,
                "order_index": 0,
                "exercises": [
                    {
                        "exercise_name": "Bench Press",
                        "sets": 3,
                        "rep_range": "8-10",
                        "order_index": 0,
                        "notes": "Pause at chest",
                        "superset_group": None,
                    },
                    {
                        "exercise_name": "Overhead Press",
                        "sets": 4,
                        "rep_range": "6-8",
                        "order_index": 1,
                    },
                ],
            }
        ],
    }


class TestLoggerRoutines:
    @pytest.mark.asyncio
    async def test_requires_key(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/routines")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/routines", headers={"X-API-Key": "nope"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_null_when_no_active_routine(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/routines", headers={"X-API-Key": TOKEN})
        assert resp.status_code == 200
        assert resp.json() is None

    @pytest.mark.asyncio
    async def test_returns_active_routine_with_days_and_exercises(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        create_resp = await client.post("/api/routines", json=_routine_payload())
        assert create_resp.status_code == 201

        resp = await client.get("/api/logger/routines", headers={"X-API-Key": TOKEN})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "PPL"
        assert data["is_active"] is True
        assert len(data["days"]) == 1
        day = data["days"][0]
        assert day["name"] == "Push Day"
        assert [e["exercise_name"] for e in day["exercises"]] == ["Bench Press", "Overhead Press"]
        assert day["exercises"][0]["rep_range"] == "8-10"
        assert day["exercises"][0]["sets"] == 3

    @pytest.mark.asyncio
    async def test_only_active_routine_returned(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        await client.post("/api/routines", json=_routine_payload())
        second = _routine_payload()
        second["name"] = "Upper/Lower"
        create_resp = await client.post("/api/routines", json=second)
        assert create_resp.status_code == 201

        resp = await client.get("/api/logger/routines", headers={"X-API-Key": TOKEN})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Upper/Lower"
