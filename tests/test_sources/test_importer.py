"""Tests for the generic canonical-workout importer."""

from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.prescriptions import prescriptions_for
from mycoach.coaching.workout_log import log_workouts
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.prescription import PrescriptionFulfillment
from mycoach.models.user import User
from mycoach.sources.importer import import_workouts
from mycoach.sources.workout_import import WorkoutImport, WorkoutSetImport


@pytest.fixture
async def user(setup_db: None) -> User:
    from tests.conftest import test_session

    async with test_session() as session:
        u = User(id=1, name="Test User", email="test@example.com")
        session.add(u)
        await session.commit()
        return u


def _workout(external_id: str | None = None, title: str = "Push Day") -> WorkoutImport:
    return WorkoutImport(
        title=title,
        start_time=datetime(2024, 6, 10, 9, 0),
        end_time=datetime(2024, 6, 10, 10, 0),
        external_id=external_id,
        sets=[
            WorkoutSetImport(
                exercise_title="Bench Press", set_index=1, weight_kg=80, reps=8, rpe=7
            ),
            WorkoutSetImport(exercise_title="Bench Press", set_index=2, weight_kg=80, reps=7),
        ],
    )


class TestImportWorkouts:
    @pytest.mark.asyncio
    async def test_creates_activity_and_sets(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout("abc")], source="logger")
            await session.commit()

        assert result.activities_created == 1

        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            assert act.data_source == "logger"
            assert act.external_id == "abc"
            assert act.duration_minutes == 60
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 2

    @pytest.mark.asyncio
    async def test_dedup_by_external_id(self, user: User) -> None:
        """Same external_id is deduplicated even if title/time differ."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("uuid-1")], source="logger")
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(
                session, user.id, [_workout("uuid-1", title="Renamed")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 0
        assert result.activities_skipped == 1

        async with test_session() as session:
            count = len((await session.execute(select(Activity))).scalars().all())
            assert count == 1

    @pytest.mark.asyncio
    async def test_prescription_lands_in_coaching_keyed_by_external_id_and_set_index(
        self, user: User
    ) -> None:
        """What the plan called for is written at log time to the coaching-owned
        fulfillment table, never onto the performed set (map #85, ticket #108)."""
        from tests.conftest import test_session

        workout = WorkoutImport(
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0),
            external_id="uuid-rx",
            sets=[
                WorkoutSetImport(
                    exercise_id="barbell-bench-press",
                    exercise_title="Bench Press",
                    set_index=1,
                    weight_kg=80,
                    reps=8,
                    prescribed_weight_kg=82.5,
                    prescribed_reps=6,
                ),
                WorkoutSetImport(exercise_title="Bench Press", set_index=2, weight_kg=80, reps=7),
            ],
        )

        async with test_session() as session:
            await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        async with test_session() as session:
            rows = (
                (
                    await session.execute(
                        select(PrescriptionFulfillment).order_by(PrescriptionFulfillment.set_index)
                    )
                )
                .scalars()
                .all()
            )
            # Only the prescribed set gets a row; the unprescribed one stays absent.
            assert [(r.activity_external_id, r.exercise_title, r.set_index) for r in rows] == [
                ("uuid-rx", "Bench Press", 1)
            ]
            assert rows[0].exercise_id == "barbell-bench-press"
            assert rows[0].prescribed_weight_kg == 82.5
            assert rows[0].prescribed_reps == 6

            # The performed set carries no prescription column at all.
            assert not hasattr(GymWorkoutDetail, "prescribed_weight_kg")
            assert not hasattr(GymWorkoutDetail, "prescribed_reps")

    @pytest.mark.asyncio
    async def test_set_index_restarts_per_exercise_so_fulfillments_are_keyed_by_exercise_too(
        self, user: User
    ) -> None:
        """The logger numbers sets from 1 within each exercise, so (external id,
        set_index) alone would collide across exercises of one workout."""
        from tests.conftest import test_session

        workout = WorkoutImport(
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0),
            external_id="uuid-two-ex",
            sets=[
                WorkoutSetImport(
                    exercise_title="Bench Press", set_index=1, reps=8,
                    prescribed_weight_kg=80, prescribed_reps=8,
                ),
                WorkoutSetImport(
                    exercise_title="Overhead Press", set_index=1, reps=8,
                    prescribed_weight_kg=40, prescribed_reps=10,
                ),
            ],
        )
        async with test_session() as session:
            await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        async with test_session() as session:
            found = await prescriptions_for(session, ["uuid-two-ex"])
        assert found[("uuid-two-ex", "Bench Press", 1)].weight_kg == 80
        assert found[("uuid-two-ex", "Overhead Press", 1)].weight_kg == 40

    @pytest.mark.asyncio
    async def test_reposting_a_workout_does_not_duplicate_fulfillments(self, user: User) -> None:
        from tests.conftest import test_session

        workout = WorkoutImport(
            title="Push Day",
            start_time=datetime(2024, 6, 10, 9, 0),
            external_id="uuid-rx2",
            sets=[
                WorkoutSetImport(
                    exercise_title="Bench Press",
                    set_index=1,
                    weight_kg=80,
                    reps=8,
                    prescribed_weight_kg=82.5,
                    prescribed_reps=6,
                )
            ],
        )
        for _ in range(2):
            async with test_session() as session:
                await log_workouts(session, user.id, [workout], source="logger")
                await session.commit()

        async with test_session() as session:
            rows = (await session.execute(select(PrescriptionFulfillment))).scalars().all()
            assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_dedup_fallback_title_start_time(self, user: User) -> None:
        """With no external_id, dedup falls back to (title, start_time)."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout()], source="hevy")
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout()], source="hevy")
            await session.commit()

        assert result.activities_skipped == 1

    @pytest.mark.asyncio
    async def test_distinct_external_ids_both_imported(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await import_workouts(
                session, user.id, [_workout("a"), _workout("b", title="Pull Day")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 2

    @pytest.mark.asyncio
    async def test_dedup_against_merged_source(self, user: User) -> None:
        """A workout already merged with Garmin (data_source='merged') is still
        recognised as a duplicate on re-import by its external_id."""
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("uuid-9")], source="logger")
            await session.commit()

        # Simulate the Garmin merge flipping the source to "merged".
        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            act.data_source = "merged"
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout("uuid-9")], source="logger")
            await session.commit()

        assert result.activities_created == 0
        assert result.activities_skipped == 1


class TestPrescriptionLinking:
    """A logged session says which prescription it answers (#69).

    The link rides on ``PlannedSession.activity_external_id`` — there is no forward
    column on ``activities``. A prescription id that no longer resolves is
    expected rather than exceptional (the plan may have been regenerated or
    superseded while the session sat unsynced), so it never costs the log.
    """

    async def _plan(self, session: AsyncSession, user_id: int) -> WeeklyPlan:
        plan = WeeklyPlan(
            user_id=user_id, week_start=date(2024, 6, 10), status="active", summary="Test"
        )
        session.add(plan)
        await session.flush()
        return plan

    @pytest.mark.asyncio
    async def test_links_and_completes_the_prescription(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            plan = await self._plan(session, user.id)
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id
            await session.commit()

            workout = _workout("uuid-link")
            workout.planned_session_id = planned_id
            result = await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert not result.errors

        async with test_session() as session:
            act = (await session.execute(select(Activity))).scalar_one()
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.completed is True
            assert refreshed.activity_external_id == act.external_id

    @pytest.mark.asyncio
    async def test_unknown_prescription_still_imports_the_log(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            workout = _workout("uuid-stale")
            workout.planned_session_id = 4242
            result = await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors is not None
        assert "4242" in result.errors[0]

        async with test_session() as session:
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 2

    @pytest.mark.asyncio
    async def test_another_users_prescription_is_not_linked(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            other = User(id=2, name="Other", email="other@example.com")
            session.add(other)
            await session.flush()
            plan = await self._plan(session, other.id)
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id
            await session.commit()

            workout = _workout("uuid-foreign")
            workout.planned_session_id = planned_id
            result = await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors

        async with test_session() as session:
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.completed is False
            assert refreshed.activity_external_id is None

    @pytest.mark.asyncio
    async def test_a_claimed_prescription_is_left_alone(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            plan = await self._plan(session, user.id)
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=0,
                sport="gym",
                title="Push",
                track="gym",
                activity_external_id="existing-999",
                completed=True,
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id
            await session.commit()

            workout = _workout("uuid-claimed")
            workout.planned_session_id = planned_id
            result = await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors

        async with test_session() as session:
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.activity_external_id == "existing-999"

    @pytest.mark.asyncio
    async def test_no_prescription_is_the_silent_case(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            result = await log_workouts(
                session, user.id, [_workout("uuid-none")], source="logger"
            )
            await session.commit()

        assert result.activities_created == 1
        assert not result.errors

    @pytest.mark.asyncio
    async def test_empty_activity_is_stored_but_never_claims(self, user: User) -> None:
        from tests.conftest import test_session

        async with test_session() as session:
            plan = await self._plan(session, user.id)
            planned = PlannedSession(
                plan_id=plan.id, day_of_week=0, sport="gym", title="Push", track="gym"
            )
            session.add(planned)
            await session.flush()
            planned_id = planned.id
            await session.commit()

            workout = _workout("uuid-empty")
            workout.sets = []
            workout.planned_session_id = planned_id
            result = await log_workouts(session, user.id, [workout], source="logger")
            await session.commit()

        assert result.activities_created == 1
        assert result.errors
        assert "performed sets" in result.errors[0]

        async with test_session() as session:
            assert (await session.execute(select(Activity))).scalar_one() is not None
            refreshed = (
                await session.execute(select(PlannedSession).where(PlannedSession.id == planned_id))
            ).scalar_one()
            assert refreshed.completed is False
            assert refreshed.activity_external_id is None


class TestSourceRecordIntake:
    """Every gym import goes through the shared intake (ticket #185)."""

    @pytest.mark.asyncio
    async def test_import_records_an_immutable_source_record_with_the_workout(
        self, user: User
    ) -> None:
        from mycoach.models.source_record import CanonicalActivitySource, SourceRecord
        from tests.conftest import test_session

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("abc")], source="logger")
            await session.commit()

        async with test_session() as session:
            record = (await session.execute(select(SourceRecord))).scalar_one()
            assert (record.source, record.external_id) == ("logger", "abc")
            assert record.raw_payload["title"] == "Push Day"
            assert [s["exercise_title"] for s in record.raw_payload["sets"]] == [
                "Bench Press",
                "Bench Press",
            ]
            act = (await session.execute(select(Activity))).scalar_one()
            link = (await session.execute(select(CanonicalActivitySource))).scalar_one()
            assert link.canonical_activity_id == act.id
            assert link.source_record_id == record.id

    @pytest.mark.asyncio
    async def test_a_source_without_an_external_id_is_still_idempotent(self, user: User) -> None:
        """Hevy CSV rows carry no id: the (title, start_time) identity is derived, not lost."""
        from mycoach.models.source_record import SourceRecord
        from tests.conftest import test_session

        for expected_created in (1, 0):
            async with test_session() as session:
                result = await import_workouts(session, user.id, [_workout(None)], source="hevy")
                await session.commit()
            assert result.activities_created == expected_created

        async with test_session() as session:
            assert len((await session.execute(select(SourceRecord))).scalars().all()) == 1
            assert len((await session.execute(select(Activity))).scalars().all()) == 1
            # the derived source identity is also the canonical external id
            assert (await session.execute(select(Activity.external_id))).scalar_one() is not None

    @pytest.mark.asyncio
    async def test_replay_after_a_garmin_link_is_still_a_no_op(self, user: User) -> None:
        """The old dedup looked for ``merged``; now the source record is the memory."""
        from mycoach.sources.reconcile import link_garmin_to_gym
        from tests.conftest import test_session
        from tests.test_sources.test_reconcile import _garmin

        async with test_session() as session:
            await import_workouts(session, user.id, [_workout("abc")], source="logger")
            await _garmin(session, "g1", datetime(2024, 6, 10, 9, 0))
            await session.commit()
        async with test_session() as session:
            assert (await link_garmin_to_gym(session, user.id)).linked == 1
            await session.commit()

        async with test_session() as session:
            result = await import_workouts(session, user.id, [_workout("abc")], source="logger")
            await session.commit()
        assert (result.activities_created, result.activities_skipped) == (0, 1)
        async with test_session() as session:
            assert len((await session.execute(select(Activity))).scalars().all()) == 1
            assert len((await session.execute(select(GymWorkoutDetail))).scalars().all()) == 2

    @pytest.mark.asyncio
    async def test_an_exercise_id_outside_the_catalogue_is_stored_as_custom(
        self, user: User
    ) -> None:
        """``exercise_id`` is a FK to the catalogue mirror; an unknown id cannot be stored."""
        from tests.conftest import test_session

        workout = _workout("abc")
        workout.sets[0].exercise_id = "not-in-the-catalogue"
        async with test_session() as session:
            await import_workouts(session, user.id, [workout], source="logger")
            await session.commit()
        async with test_session() as session:
            ids = (await session.execute(select(GymWorkoutDetail.exercise_id))).scalars().all()
            assert None in ids
            assert "not-in-the-catalogue" not in ids
