"""Tests for the universal workout push endpoint and logger API (API-key auth)."""

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


def _batch() -> dict:
    return {
        "source": "logger",
        "workouts": [
            {
                "external_id": "client-uuid-1",
                "title": "Push Day",
                "start_time": "2024-06-10T09:00:00",
                "end_time": "2024-06-10T10:00:00",
                "sets": [
                    {
                        "exercise_title": "Bench Press",
                        "set_index": 1,
                        "weight_kg": 80,
                        "reps": 8,
                        "rpe": 7,
                    },
                    {
                        "exercise_title": "Overhead Press",
                        "set_index": 1,
                        "weight_kg": 45,
                        "reps": 10,
                    },
                ],
            }
        ],
    }


class TestPushAuth:
    @pytest.mark.asyncio
    async def test_missing_key_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.post("/api/sources/import/workouts", json=_batch())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.post(
            "/api/sources/import/workouts", json=_batch(), headers={"X-API-Key": "nope"}
        )
        assert resp.status_code == 401


class TestPushImport:
    @pytest.mark.asyncio
    async def test_valid_batch_creates(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.post(
            "/api/sources/import/workouts", json=_batch(), headers={"X-API-Key": TOKEN}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["activities_created"] == 1
        assert data["activities_skipped"] == 0

    @pytest.mark.asyncio
    async def test_idempotent_repost(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        headers = {"X-API-Key": TOKEN}
        first = await client.post("/api/sources/import/workouts", json=_batch(), headers=headers)
        assert first.json()["activities_created"] == 1

        second = await client.post("/api/sources/import/workouts", json=_batch(), headers=headers)
        body = second.json()
        assert body["activities_created"] == 0
        assert body["activities_skipped"] == 1

    @pytest.mark.asyncio
    async def test_invalid_set_type_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["sets"][0]["set_type"] = "bogus"
        resp = await client.post(
            "/api/sources/import/workouts", json=batch, headers={"X-API-Key": TOKEN}
        )
        assert resp.status_code == 422


class TestLoggerExercises:
    @pytest.mark.asyncio
    async def test_requires_key(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/exercises")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_distinct_titles(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        headers = {"X-API-Key": TOKEN}
        await client.post("/api/sources/import/workouts", json=_batch(), headers=headers)

        resp = await client.get("/api/logger/exercises", headers=headers)
        assert resp.status_code == 200
        exercises = resp.json()["exercises"]
        assert "Bench Press" in exercises
        assert "Overhead Press" in exercises
        # sorted + distinct
        assert exercises == sorted(set(exercises))
