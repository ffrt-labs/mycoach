"""Reconciling a Garmin source record with a gym one as canonical-activity linking.

Replaces ``merger.py``'s delete-and-overwrite (map #85, ticket #185, design #109/#110):
the Garmin source record is never touched, its join row moves onto the gym canonical
activity, the now-redundant Garmin-only canonical activity is hard-deleted, and
"merged" is not a value anything takes.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.source_record import CanonicalActivitySource, SourceRecord
from mycoach.models.user import User
from mycoach.sources.intake import intake_source_record
from mycoach.sources.reconcile import link_garmin_to_gym

START = datetime(2024, 6, 10, 9, 0)


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        user = User(id=1, name="Test User", email="test@example.com")
        session.add(user)
        await session.commit()
        return user


async def _gym(
    session,  # noqa: ANN001
    *,
    external_id: str = "uuid-1",
    source: str = "logger",
    start: datetime = START,
    end: datetime | None = None,
    duration: int | None = 60,
    sets: int = 0,
) -> int:
    canonical = Activity(
        user_id=1,
        sport="gym",
        title="Push Day",
        start_time=start,
        end_time=end if end is not None or duration is None else start + timedelta(hours=1),
        duration_minutes=duration,
        data_source=source,
        external_id=external_id,
    )
    result = await intake_source_record(
        session,
        athlete_id=1,
        source=source,
        external_id=external_id,
        raw_payload={"title": "Push Day"},
        canonical=canonical,
    )
    for i in range(sets):
        session.add(
            GymWorkoutDetail(
                activity_id=result.canonical_activity_id,
                exercise_title="Bench Press",
                set_index=i + 1,
                weight_kg=80,
                reps=8,
            )
        )
    return result.canonical_activity_id


async def _garmin(
    session,  # noqa: ANN001
    garmin_id: str,
    start: datetime,
    sport: str = "gym",
) -> int:
    canonical = Activity(
        user_id=1,
        sport=sport,
        title="Strength Training",
        start_time=start,
        end_time=start + timedelta(hours=1),
        duration_minutes=60,
        avg_hr=135,
        max_hr=165,
        calories=450,
        hr_zones='[{"zone": 1, "minutes": 10}]',
        training_effect_aerobic=3.2,
        training_effect_anaerobic=1.5,
        data_source="garmin",
    )
    result = await intake_source_record(
        session,
        athlete_id=1,
        source="garmin",
        external_id=garmin_id,
        raw_payload={"activityId": garmin_id},
        canonical=canonical,
    )
    return result.canonical_activity_id


async def _sources_of(session, canonical_id: int) -> set[str]:  # noqa: ANN001
    rows = await session.execute(
        select(SourceRecord.source)
        .join(CanonicalActivitySource, CanonicalActivitySource.source_record_id == SourceRecord.id)
        .where(CanonicalActivitySource.canonical_activity_id == canonical_id)
    )
    return set(rows.scalars().all())


@pytest.mark.asyncio
async def test_garmin_match_links_onto_the_gym_activity(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        gym_id = await _gym(session, sets=1)
        await _garmin(session, "g123", START + timedelta(minutes=5))
        await session.commit()

    async with test_session() as session:
        result = await link_garmin_to_gym(session, user.id)
        await session.commit()

    assert result.linked == 1
    async with test_session() as session:
        activity = (await session.execute(select(Activity))).scalar_one()  # Garmin-only gone
        assert activity.id == gym_id
        assert activity.title == "Push Day"
        assert activity.external_id == "uuid-1"  # identity carried forward
        assert activity.data_source == "logger"  # never "merged"
        assert (activity.avg_hr, activity.max_hr, activity.calories) == (135, 165, 450)
        assert activity.training_effect_aerobic == 3.2
        assert await _sources_of(session, gym_id) == {"logger", "garmin"}
        details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
        assert [d.activity_id for d in details] == [gym_id]


@pytest.mark.asyncio
async def test_both_source_records_survive_untouched(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session)
        await _garmin(session, "g123", START)
        await session.commit()

    async def snapshot() -> dict[str, tuple]:  # type: ignore[type-arg]
        async with test_session() as session:
            return {
                r.source: (r.id, r.external_id, r.raw_payload, r.received_at)
                for r in (await session.execute(select(SourceRecord))).scalars()
            }

    before = await snapshot()
    async with test_session() as session:
        await link_garmin_to_gym(session, user.id)
        await session.commit()
    after = await snapshot()
    assert after == before
    assert after["garmin"][2] == {"activityId": "g123"}


@pytest.mark.asyncio
async def test_relinking_is_a_no_op(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session)
        await _garmin(session, "g123", START)
        await session.commit()
    for expected in (1, 0):
        async with test_session() as session:
            result = await link_garmin_to_gym(session, user.id)
            await session.commit()
        assert result.linked == expected


@pytest.mark.asyncio
async def test_no_link_when_no_overlap(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session)
        await _garmin(session, "g123", datetime(2024, 6, 10, 14, 0))
        await session.commit()
    async with test_session() as session:
        assert (await link_garmin_to_gym(session, user.id)).linked == 0
        assert len((await session.execute(select(Activity))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_non_gym_garmin_activity_is_not_linked(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session)
        await _garmin(session, "g123", START, sport="cardio")
        await session.commit()
    async with test_session() as session:
        assert (await link_garmin_to_gym(session, user.id)).linked == 0


@pytest.mark.asyncio
async def test_time_tolerance_still_links(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session, end=datetime(2024, 6, 10, 10, 15))
        await _garmin(session, "g123", datetime(2024, 6, 10, 10, 30))
        await session.commit()
    async with test_session() as session:
        assert (await link_garmin_to_gym(session, user.id)).linked == 1


@pytest.mark.asyncio
async def test_best_overlap_wins_and_the_other_stays_unlinked(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        gym_id = await _gym(session)
        await _garmin(session, "g_close", START + timedelta(minutes=2))
        far_id = await _garmin(session, "g_far", START + timedelta(minutes=25))
        await session.commit()
    async with test_session() as session:
        assert (await link_garmin_to_gym(session, user.id)).linked == 1
        assert await _sources_of(session, gym_id) == {"logger", "garmin"}
        garmin_ids = (
            (
                await session.execute(
                    select(SourceRecord.external_id)
                    .join(
                        CanonicalActivitySource,
                        CanonicalActivitySource.source_record_id == SourceRecord.id,
                    )
                    .where(CanonicalActivitySource.canonical_activity_id == gym_id)
                    .where(SourceRecord.source == "garmin")
                )
            )
            .scalars()
            .all()
        )
        assert garmin_ids == ["g_close"]
        assert await _sources_of(session, far_id) == {"garmin"}  # still its own activity


@pytest.mark.asyncio
async def test_no_gym_activities_links_nothing(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _garmin(session, "g123", START)
        await session.commit()
    async with test_session() as session:
        assert (await link_garmin_to_gym(session, user.id)).linked == 0


@pytest.mark.asyncio
async def test_duration_is_filled_from_garmin_when_the_gym_source_has_none(user: User) -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session, duration=None)
        await _garmin(session, "g123", START)
        await session.commit()
    async with test_session() as session:
        await link_garmin_to_gym(session, user.id)
        await session.commit()
        assert (await session.execute(select(Activity.duration_minutes))).scalar_one() == 60


@pytest.mark.asyncio
async def test_the_merge_endpoint_links_instead_of_returning_503(client, user: User) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import test_session

    async with test_session() as session:
        await _gym(session)
        await _garmin(session, "g123", START)
        await session.commit()
    response = await client.post("/api/sources/merge")
    assert response.status_code == 200
    assert response.json() == {"activities_merged": 1, "errors": []}
