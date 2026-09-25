"""Tests for GET /api/logger/exercises — catalogue plus last-performed (#70).

Contract settled on #70 (amended for #56): ``exercises`` stays the pinned
catalogue; a sibling ``last_performed`` carries only the exercises actually
in history, keyed by ``exercise_id`` with the display title alongside so a
null-id custom exercise still has a home.
"""

from datetime import datetime

import pytest
from httpx import AsyncClient

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.user import User


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


async def _log_gym(start_time: datetime, sets: list[dict]) -> None:
    """Persist one gym Activity with the given sets, in order."""
    from tests.conftest import test_session

    async with test_session() as session:
        activity = Activity(
            user_id=1,
            sport="gym",
            title="Push Day",
            start_time=start_time,
            data_source="logger",
        )
        session.add(activity)
        await session.flush()
        for index, s in enumerate(sets):
            session.add(
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_id=s.get("exercise_id"),
                    exercise_title=s["exercise_title"],
                    set_index=s.get("set_index", index),
                    set_type=s.get("set_type", "normal"),
                    weight_kg=s.get("weight_kg"),
                    reps=s.get("reps"),
                )
            )
        await session.commit()


async def _get(client: AsyncClient) -> dict:
    response = await client.get("/api/logger/exercises")
    assert response.status_code == 200
    return response.json()


def _by_title(payload: dict, title: str) -> dict | None:
    return next((e for e in payload["last_performed"] if e["title"] == title), None)


async def test_a_performed_exercise_carries_its_sets(user: User, client: AsyncClient) -> None:
    """One logged exercise comes back with its date and its sets, in order."""
    await _log_gym(
        datetime(2026, 9, 11, 18, 0),
        [
            {
                "exercise_id": "Barbell_Bench_Press",
                "exercise_title": "Bench Press",
                "set_type": "warmup",
                "weight_kg": 40.0,
                "reps": 10,
            },
            {
                "exercise_id": "Barbell_Bench_Press",
                "exercise_title": "Bench Press",
                "weight_kg": 80.0,
                "reps": 8,
            },
        ],
    )

    bench = _by_title(await _get(client), "Bench Press")

    assert bench == {
        "exercise_id": "Barbell_Bench_Press",
        "title": "Bench Press",
        "date": "2026-09-11",
        "sets": [
            {"set_index": 0, "set_type": "warmup", "weight_kg": 40.0, "reps": 10},
            {"set_index": 1, "set_type": "normal", "weight_kg": 80.0, "reps": 8},
        ],
    }


async def test_only_the_most_recent_session_counts(user: User, client: AsyncClient) -> None:
    """An older session never overrides the last one — this is a reference row."""
    await _log_gym(
        datetime(2026, 9, 4, 18, 0),
        [{"exercise_id": "Barbell_Squat", "exercise_title": "Squat", "weight_kg": 90.0, "reps": 5}],
    )
    await _log_gym(
        datetime(2026, 9, 11, 18, 0),
        [
            {
                "exercise_id": "Barbell_Squat",
                "exercise_title": "Squat",
                "weight_kg": 100.0,
                "reps": 5,
            }
        ],
    )

    squat = _by_title(await _get(client), "Squat")

    assert squat is not None
    assert squat["date"] == "2026-09-11"
    assert [s["weight_kg"] for s in squat["sets"]] == [100.0]


async def test_a_custom_exercise_is_keyed_by_its_title(user: User, client: AsyncClient) -> None:
    """A null-id custom exercise still gets a reference row — title is its only handle."""
    await _log_gym(
        datetime(2026, 9, 8, 18, 0),
        [
            {
                "exercise_id": None,
                "exercise_title": "Bulgarian. Split Squat",
                "weight_kg": 20.0,
                "reps": 12,
            }
        ],
    )

    custom = _by_title(await _get(client), "Bulgarian. Split Squat")

    assert custom is not None
    assert custom["exercise_id"] is None
    assert custom["sets"] == [{"set_index": 0, "set_type": "normal", "weight_kg": 20.0, "reps": 12}]


async def test_the_catalogue_is_untouched_and_carries_no_history(
    user: User, client: AsyncClient
) -> None:
    """Only exercises actually trained appear; the catalogue list is unchanged."""
    await _log_gym(
        datetime(2026, 9, 11, 18, 0),
        [
            {
                "exercise_id": "Barbell_Squat",
                "exercise_title": "Squat",
                "weight_kg": 100.0,
                "reps": 5,
            }
        ],
    )

    payload = await _get(client)

    assert len(payload["last_performed"]) == 1
    assert len(payload["exercises"]) > 800
    assert set(payload["exercises"][0]) == {"id", "name"}


async def test_the_reference_rows_come_back_in_a_stable_order(
    user: User, client: AsyncClient
) -> None:
    """The wire order is by title — a cache the logger diffs shouldn't churn."""
    await _log_gym(
        datetime(2026, 9, 11, 18, 0),
        [
            {"exercise_id": "Barbell_Squat", "exercise_title": "Squat", "weight_kg": 100.0},
            {"exercise_id": "Barbell_Bench_Press", "exercise_title": "Bench Press", "reps": 8},
            {"exercise_id": None, "exercise_title": "Ad-hoc Thing", "reps": 1},
        ],
    )

    payload = await _get(client)

    assert [e["title"] for e in payload["last_performed"]] == [
        "Ad-hoc Thing",
        "Bench Press",
        "Squat",
    ]
