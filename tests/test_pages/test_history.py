"""Tests for the history page route."""

import json
from datetime import date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session
from tests.test_email.test_recap_notes import NOTES

pytestmark = pytest.mark.anyio


async def _seed_user() -> User:
    async with test_session() as session:
        user = User(id=1, email="test@example.com", name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_activity(
    sport: str = "gym",
    title: str = "Morning Workout",
    start_time: datetime | None = None,
    data_source: str = "hevy",
    avg_hr: int | None = None,
    calories: int | None = None,
    duration_minutes: int | None = 60,
) -> Activity:
    if start_time is None:
        start_time = datetime(2025, 6, 10, 8, 0)
    async with test_session() as session:
        activity = Activity(
            user_id=1,
            sport=sport,
            title=title,
            start_time=start_time,
            duration_minutes=duration_minutes,
            data_source=data_source,
            avg_hr=avg_hr,
            calories=calories,
        )
        session.add(activity)
        await session.commit()
        await session.refresh(activity)
        return activity


async def test_history_page_empty(client: AsyncClient) -> None:
    """History page renders with no activities."""
    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "History" in resp.text
    assert "No activities yet" in resp.text
    assert "0 activities" in resp.text


async def test_history_page_with_activities(client: AsyncClient) -> None:
    """History page shows activities grouped by date."""
    await _seed_user()
    await _seed_activity(title="Push Day", start_time=datetime(2025, 6, 10, 8, 0))
    await _seed_activity(
        title="Swimming Laps",
        sport="swimming",
        start_time=datetime(2025, 6, 10, 18, 0),
        data_source="garmin",
    )

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Push Day" in resp.text
    assert "Swimming Laps" in resp.text
    assert "Jun 10, 2025" in resp.text
    assert "2 activities" in resp.text


async def test_history_page_sport_filter(client: AsyncClient) -> None:
    """History page filters by sport."""
    await _seed_user()
    await _seed_activity(title="Push Day", sport="gym")
    await _seed_activity(title="Swim", sport="swimming", start_time=datetime(2025, 6, 11, 8, 0))

    # Filter for gym only
    resp = await client.get("/history?sport=gym")
    assert resp.status_code == 200
    assert "Push Day" in resp.text
    # "Swim" title should not appear as an activity card (it's in the filter chips though)
    assert "1 activity" in resp.text
    # Only gym activity card present
    assert resp.text.count("font-semibold text-zinc-50 truncate") == 1


async def test_history_page_gym_details(client: AsyncClient) -> None:
    """History page shows gym exercise details."""
    await _seed_user()
    activity = await _seed_activity(title="Push Day")

    async with test_session() as session:
        detail = GymWorkoutDetail(
            activity_id=activity.id,
            exercise_title="Bench Press",
            set_index=1,
            set_type="normal",
            weight_kg=80.0,
            reps=8,
        )
        session.add(detail)
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Bench Press" in resp.text
    assert "80.0kg" in resp.text
    assert "x 8" in resp.text


async def test_history_groups_renamed_exercise_by_stable_id(client: AsyncClient) -> None:
    await _seed_user()
    activity = await _seed_activity(title="Leg Day")

    async with test_session() as session:
        session.add_all(
            [
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_id="Barbell_Squat",
                    exercise_title="Barbell Squat",
                    set_index=1,
                    weight_kg=100,
                    reps=5,
                ),
                GymWorkoutDetail(
                    activity_id=activity.id,
                    exercise_id="Barbell_Squat",
                    exercise_title="Back Squat",
                    set_index=2,
                    weight_kg=102.5,
                    reps=5,
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/history")

    assert resp.status_code == 200
    assert resp.text.count("Barbell Squat</span>") == 1
    assert "Back Squat</span>" not in resp.text


async def test_history_page_metrics(client: AsyncClient) -> None:
    """History page shows HR and calorie metrics."""
    await _seed_user()
    await _seed_activity(title="Cardio Run", sport="cardio", avg_hr=145, calories=350)

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "145 bpm" in resp.text
    assert "350" in resp.text


async def test_history_page_analysis_badge(client: AsyncClient) -> None:
    """A v4 post-workout analysis shows the Analyzed badge and its v4 panel."""
    await _seed_user()
    activity = await _seed_activity(title="Analyzed Workout")

    async with test_session() as session:
        insight = CoachingInsight(
            user_id=1,
            insight_type="post_workout",
            insight_date=activity.start_time.date(),
            content=json.dumps({"performance_summary": "Solid **volume** today."}),
            activity_external_id=activity.external_id,
        )
        session.add(insight)
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Analyzed" in resp.text
    assert "Solid <strong>volume</strong> today." in resp.text
    assert "Coach notes unavailable" not in resp.text


async def test_a_post_workout_without_coach_notes_is_not_shown_as_analyzed(
    client: AsyncClient,
) -> None:
    """#249: an insight stored as ``null`` (the notes fallback) has nothing to expand."""
    await _seed_user()
    activity = await _seed_activity(title="Fallback Workout")
    async with test_session() as session:
        session.add(
            CoachingInsight(
                user_id=1,
                insight_type="post_workout",
                insight_date=activity.start_time.date(),
                content="null",
                activity_external_id=activity.external_id,
            )
        )
        await session.commit()

    resp = await client.get("/history")

    assert resp.status_code == 200
    assert "Coach notes unavailable" in resp.text
    assert "Analyzed" not in resp.text
    assert f'id="analysis-{activity.external_id}"' not in resp.text
    assert "Re-analyze" in resp.text


async def test_history_page_shows_a_v5_analysis_and_links_its_insight_page(
    client: AsyncClient,
) -> None:
    """A v5 post-workout insight's panel is its full analysis, not an empty v4 panel."""
    from tests.test_pages.test_insight import _notes

    await _seed_user()
    activity = await _seed_activity(title="Pool swim", sport="swimming")
    async with test_session() as session:
        for revision, take in ((1, "First take."), (2, "Held **Z2** throughout.")):
            session.add(
                CoachingInsight(
                    user_id=1,
                    insight_type="post_workout",
                    insight_date=activity.start_time.date(),
                    content=_notes(take),
                    activity_external_id=activity.external_id,
                    revision=revision,
                )
            )
        await session.commit()
        latest = (await session.execute(select(func.max(CoachingInsight.id)))).scalar_one()

    resp = await client.get("/history")

    assert "Held <strong>Z2</strong> throughout." in resp.text
    assert "Aerobic" in resp.text  # the Headline's verdict, from the v5 model
    assert "First take." not in resp.text
    assert f'href="/insights/{latest}"' in resp.text


async def test_history_page_sport_filter_chips(client: AsyncClient) -> None:
    """History page shows sport filter chips for available sports."""
    await _seed_user()
    await _seed_activity(title="Gym", sport="gym")
    await _seed_activity(title="Swim", sport="swimming", start_time=datetime(2025, 6, 11, 8, 0))

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert 'href="/history?sport=gym"' in resp.text
    assert 'href="/history?sport=swimming"' in resp.text


async def test_history_page_pagination(client: AsyncClient) -> None:
    """History page paginates when many activities exist."""
    await _seed_user()
    base = datetime(2025, 1, 1, 8, 0)
    async with test_session() as session:
        for i in range(25):
            session.add(
                Activity(
                    user_id=1,
                    sport="gym",
                    title=f"Workout {i}",
                    start_time=base + timedelta(days=i),
                    data_source="hevy",
                )
            )
        await session.commit()

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "Page 1 of 2" in resp.text
    assert "Next" in resp.text

    resp2 = await client.get("/history?page=2")
    assert resp2.status_code == 200
    assert "Page 2 of 2" in resp2.text
    assert "Prev" in resp2.text


async def test_history_page_sources_badge(client: AsyncClient) -> None:
    """History page shows the activity's provenance: the sources that contributed to it."""
    from mycoach import health
    from mycoach.sources.workout_import import WorkoutImport

    await _seed_user()
    start = datetime(2025, 6, 10, 9, 0)
    await health.record_workout(
        1, "logger", WorkoutImport(title="Linked Workout", start_time=start, external_id="u1")
    )
    await health.record_garmin_activities(
        1,
        [
            {
                "activityId": 7,
                "activityName": "Strength",
                "activityType": {"typeKey": "strength_training"},
                "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": 3600,
            }
        ],
    )
    await health.link_garmin(1)

    resp = await client.get("/history")
    assert resp.status_code == 200
    assert "garmin, logger" in resp.text.lower()


async def test_a_v5_recap_shows_its_lead_and_next_week_actions(client: AsyncClient) -> None:
    """A recap on the v5 contract (#242) holds Coach notes, not the v4 paragraphs."""
    await _seed_user()
    async with test_session() as session:
        session.add(
            CoachingInsight(
                user_id=1,
                insight_date=date(2026, 11, 2),
                insight_type="weekly_recap",
                content=json.dumps(NOTES),
                prompt_version="bundled:v5",
            )
        )
        await session.commit()

    resp = await client.get("/history?recap_week=2026-11-02")

    assert resp.status_code == 200
    assert "Both lifts and the swim are in" in resp.text
    assert "Keep one intervals session in the week." in resp.text
    assert "Resting HR up 5 bpm → Swap intervals for easy Z2" in resp.text
    assert "Coach notes unavailable" not in resp.text


async def _store_recap(content: str | None) -> None:
    async with test_session() as session:
        session.add(
            CoachingInsight(
                user_id=1,
                insight_date=date(2026, 11, 2),
                insight_type="weekly_recap",
                content=content,
                prompt_version="bundled:v5",
            )
        )
        await session.commit()


async def test_a_v4_recap_shows_its_paragraphs(client: AsyncClient) -> None:
    await _seed_user()
    await _store_recap(json.dumps({"week_summary": "A **solid** week.", "mesocycle_progress": "W3"}))

    resp = await client.get("/history?recap_week=2026-11-02")

    assert "A <strong>solid</strong> week." in resp.text
    assert "Coach notes unavailable" not in resp.text


async def test_a_recap_without_coach_notes_says_so(client: AsyncClient) -> None:
    """#249: a recap stored as ``null`` after the notes fallback."""
    await _seed_user()
    await _store_recap("null")

    resp = await client.get("/history?recap_week=2026-11-02")

    assert resp.status_code == 200
    assert "Coach notes unavailable" in resp.text
    assert "Regenerate" in resp.text
