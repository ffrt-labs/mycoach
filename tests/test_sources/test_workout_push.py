"""Tests for the universal workout push endpoint and logger API (API-key auth)."""

import pytest

from mycoach.models.user import User


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


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


class TestPushImport:
    @pytest.mark.asyncio
    async def test_valid_batch_creates(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.post(
            "/api/sources/import/workouts", json=_batch()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["activities_created"] == 1
        assert data["activities_skipped"] == 0

    @pytest.mark.asyncio
    async def test_stable_exercise_id_round_trips_through_activity_api(
        self, client, user: User
    ) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["sets"][0]["exercise_id"] = "Barbell_Squat"

        response = await client.post(
            "/api/sources/import/workouts",
            json=batch,
        )
        assert response.status_code == 200

        activities = await client.get("/api/activities?sport=gym")
        first_set = activities.json()["items"][0]["gym_details"][0]
        assert first_set["exercise_id"] == "Barbell_Squat"

    @pytest.mark.asyncio
    async def test_legacy_title_resolves_to_stable_id(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["sets"][0]["exercise_title"] = "Squat (Barbell)"

        response = await client.post(
            "/api/sources/import/workouts",
            json=batch,
        )
        assert response.status_code == 200

        activities = await client.get("/api/activities?sport=gym")
        first_set = activities.json()["items"][0]["gym_details"][0]
        assert first_set["exercise_id"] == "Barbell_Squat"

    @pytest.mark.asyncio
    async def test_unknown_exercise_id_is_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["sets"][0]["exercise_id"] = "invented-by-a-caller"

        response = await client.post(
            "/api/sources/import/workouts",
            json=batch,
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_idempotent_repost(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        first = await client.post("/api/sources/import/workouts", json=_batch())
        assert first.json()["activities_created"] == 1

        second = await client.post("/api/sources/import/workouts", json=_batch())
        body = second.json()
        assert body["activities_created"] == 0
        assert body["activities_skipped"] == 1

    @pytest.mark.asyncio
    async def test_invalid_set_type_rejected(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["sets"][0]["set_type"] = "bogus"
        resp = await client.post(
            "/api/sources/import/workouts", json=batch
        )
        assert resp.status_code == 422


class TestLoggerExercises:

    @pytest.mark.asyncio
    async def test_returns_pinned_catalogue_with_stable_ids(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        resp = await client.get("/api/logger/exercises")

        assert resp.status_code == 200
        exercises = resp.json()["exercises"]
        assert {"id": "Barbell_Squat", "name": "Barbell Squat"} in exercises
        assert {
            "id": "mycoach:bulgarian-split-squat",
            "name": "Bulgarian Split Squat",
        } in exercises
        assert len({exercise["id"] for exercise in exercises}) == len(exercises)

    @pytest.mark.asyncio
    async def test_returns_sorted_distinct_catalogue(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        await client.post("/api/sources/import/workouts", json=_batch())

        resp = await client.get("/api/logger/exercises")
        assert resp.status_code == 200
        exercises = resp.json()["exercises"]
        names = [exercise["name"] for exercise in exercises]
        ids = [exercise["id"] for exercise in exercises]
        assert names == sorted(names, key=str.casefold)
        assert len(ids) == len(set(ids))


class TestPushPrescriptionLink:
    """The wire field the logger stamps, end to end (#69)."""

    async def _prescription(self, user_id: int) -> int:
        from datetime import date

        from mycoach.models.plan import PlannedSession, WeeklyPlan
        from tests.conftest import test_session

        async with test_session() as session:
            plan = WeeklyPlan(
                user_id=user_id, week_start=date(2024, 6, 10), status="active", summary="Test"
            )
            session.add(plan)
            await session.flush()
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.commit()
            return planned.id

    @pytest.mark.asyncio
    async def test_posted_id_completes_the_prescription(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import select

        from mycoach.models.plan import PlannedSession
        from tests.conftest import test_session

        planned_id = await self._prescription(user.id)
        batch = _batch()
        batch["workouts"][0]["planned_session_id"] = planned_id

        resp = await client.post(
            "/api/sources/import/workouts", json=batch
        )
        assert resp.status_code == 200
        assert resp.json()["errors"] == []

        async with test_session() as session:
            planned = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert planned.completed is True
            assert planned.activity_id is not None

    @pytest.mark.asyncio
    async def test_stale_id_reports_but_still_stores_the_workout(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        batch = _batch()
        batch["workouts"][0]["planned_session_id"] = 4242

        resp = await client.post(
            "/api/sources/import/workouts", json=batch
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["activities_created"] == 1
        assert body["errors"]
