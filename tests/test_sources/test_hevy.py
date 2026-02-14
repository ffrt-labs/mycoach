"""Tests for Hevy CSV parser, mappers, and import endpoint."""

import pytest
from sqlalchemy import select

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.user import User
from mycoach.sources.hevy.csv_parser import parse_hevy_csv
from mycoach.sources.hevy.mappers import import_hevy_workouts

# ── Sample CSV data ──────────────────────────────────────────────────

SAMPLE_CSV = """\
title,start_time,end_time,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe
Push Day,2024-06-10 09:00:00,2024-06-10 10:15:00,Bench Press,,Flat bench,1,warmup,135,10,,,5
Push Day,2024-06-10 09:00:00,2024-06-10 10:15:00,Bench Press,,,2,normal,185,8,,,7
Push Day,2024-06-10 09:00:00,2024-06-10 10:15:00,Bench Press,,,3,normal,185,7,,,8
Push Day,2024-06-10 09:00:00,2024-06-10 10:15:00,Overhead Press,,,1,normal,95,10,,,6
Pull Day,2024-06-11 09:30:00,2024-06-11 10:45:00,Deadlift,,,1,normal,225,5,,,8
Pull Day,2024-06-11 09:30:00,2024-06-11 10:45:00,Deadlift,,,2,normal,225,5,,,9
Pull Day,2024-06-11 09:30:00,2024-06-11 10:45:00,Barbell Row,,Pendlay style,1,normal,135,8,,,7
"""

SAMPLE_CSV_WITH_BOM = "\ufeff" + SAMPLE_CSV

MINIMAL_CSV = """\
title,start_time,end_time,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe
Leg Day,2024-06-12 08:00:00,2024-06-12 09:00:00,Squat,,,1,normal,225,5,,,8
"""

CSV_WITH_ERRORS = """\
title,start_time,end_time,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe
Push Day,2024-06-10 09:00:00,,Bench Press,,,1,normal,185,8,,,7
,2024-06-10 09:00:00,,Bench Press,,,2,normal,185,8,,,7
Push Day,bad-date,,Bench Press,,,3,normal,185,8,,,7
Push Day,2024-06-10 09:00:00,,OHP,,,bad_index,normal,95,10,,,7
Push Day,2024-06-10 09:00:00,,Lateral Raise,,,1,normal,20,12,,,11
"""

MISSING_COLUMNS_CSV = """\
title,start_time,exercise_title
Push Day,2024-06-10 09:00:00,Bench Press
"""

EMPTY_CSV = ""


# ── CSV Parser Tests ─────────────────────────────────────────────────


class TestParseHevyCsv:
    def test_parse_valid_csv(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        assert len(result.workouts) == 2
        assert result.rows_parsed == 7
        assert result.rows_skipped == 0

        push_day = result.workouts[0]
        assert push_day.title == "Push Day"
        assert push_day.start_time.year == 2024
        assert push_day.start_time.month == 6
        assert push_day.start_time.day == 10
        assert len(push_day.sets) == 4

        pull_day = result.workouts[1]
        assert pull_day.title == "Pull Day"
        assert len(pull_day.sets) == 3

    def test_weight_conversion_lbs_to_kg(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        bench_set = result.workouts[0].sets[1]  # 185 lbs
        assert bench_set.weight_kg is not None
        assert abs(bench_set.weight_kg - 83.91) < 0.1

    def test_set_types(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        sets = result.workouts[0].sets
        assert sets[0].set_type == "warmup"
        assert sets[1].set_type == "normal"

    def test_exercise_notes(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        assert result.workouts[0].sets[0].exercise_notes == "Flat bench"
        assert result.workouts[1].sets[2].exercise_notes == "Pendlay style"

    def test_rpe_values(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        assert result.workouts[0].sets[0].rpe == 5
        assert result.workouts[0].sets[2].rpe == 8

    def test_duration_computed(self) -> None:
        result = parse_hevy_csv(SAMPLE_CSV)
        # Push Day: 09:00 to 10:15 = 75 minutes (checked in mappers)
        assert result.workouts[0].end_time is not None

    def test_errors_for_bad_rows(self) -> None:
        result = parse_hevy_csv(CSV_WITH_ERRORS)
        # Row 3: missing title, Row 4: bad date, Row 5: bad set_index
        assert result.rows_skipped == 3
        assert len(result.errors) >= 3

    def test_rpe_out_of_range(self) -> None:
        result = parse_hevy_csv(CSV_WITH_ERRORS)
        # Row 6: RPE 11 out of range — should be None but row still parsed
        rpe_errors = [e for e in result.errors if "RPE" in e]
        assert len(rpe_errors) == 1

    def test_missing_required_columns(self) -> None:
        result = parse_hevy_csv(MISSING_COLUMNS_CSV)
        assert len(result.errors) == 1
        assert "Missing required columns" in result.errors[0]
        assert len(result.workouts) == 0

    def test_empty_csv(self) -> None:
        result = parse_hevy_csv(EMPTY_CSV)
        assert len(result.errors) == 1
        assert "empty" in result.errors[0].lower()

    def test_bom_handling(self) -> None:
        # Parser should strip BOM from content
        result = parse_hevy_csv(SAMPLE_CSV_WITH_BOM)
        assert len(result.workouts) == 2
        assert result.rows_parsed == 7


# ── Mapper/Import Tests ──────────────────────────────────────────────


@pytest.fixture
async def user(setup_db: None) -> User:
    """Create a test user in the database."""
    from tests.conftest import test_session

    async with test_session() as session:
        user = User(id=1, name="Test User", email="test@example.com")
        session.add(user)
        await session.commit()
        return user


class TestImportHevyWorkouts:
    @pytest.mark.asyncio
    async def test_import_creates_activities(self, user: User) -> None:
        from tests.conftest import test_session

        parse_result = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            result = await import_hevy_workouts(session, user.id, parse_result)

        assert result.activities_created == 2
        assert result.activities_skipped == 0

        async with test_session() as session:
            activities = (await session.execute(select(Activity))).scalars().all()
            assert len(activities) == 2
            assert activities[0].sport == "gym"
            assert activities[0].data_source == "hevy"

    @pytest.mark.asyncio
    async def test_import_creates_gym_details(self, user: User) -> None:
        from tests.conftest import test_session

        parse_result = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            await import_hevy_workouts(session, user.id, parse_result)

        async with test_session() as session:
            details = (await session.execute(select(GymWorkoutDetail))).scalars().all()
            assert len(details) == 7
            bench_sets = [d for d in details if d.exercise_title == "Bench Press"]
            assert len(bench_sets) == 3

    @pytest.mark.asyncio
    async def test_deduplication(self, user: User) -> None:
        from tests.conftest import test_session

        parse_result = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            result1 = await import_hevy_workouts(session, user.id, parse_result)
        assert result1.activities_created == 2

        # Import same data again
        parse_result2 = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            result2 = await import_hevy_workouts(session, user.id, parse_result2)
        assert result2.activities_created == 0
        assert result2.activities_skipped == 2

        # Total activities should still be 2
        async with test_session() as session:
            count = len((await session.execute(select(Activity))).scalars().all())
            assert count == 2

    @pytest.mark.asyncio
    async def test_duration_calculated(self, user: User) -> None:
        from tests.conftest import test_session

        parse_result = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            await import_hevy_workouts(session, user.id, parse_result)

        async with test_session() as session:
            activity = (await session.execute(select(Activity))).scalars().first()
            assert activity is not None
            assert activity.duration_minutes == 75  # 09:00 to 10:15

    @pytest.mark.asyncio
    async def test_incremental_import(self, user: User) -> None:
        """Import minimal CSV first, then full CSV — only new workouts added."""
        from tests.conftest import test_session

        # Import one workout
        parse1 = parse_hevy_csv(MINIMAL_CSV)
        async with test_session() as session:
            result1 = await import_hevy_workouts(session, user.id, parse1)
        assert result1.activities_created == 1

        # Import full CSV (different workouts)
        parse2 = parse_hevy_csv(SAMPLE_CSV)
        async with test_session() as session:
            result2 = await import_hevy_workouts(session, user.id, parse2)
        assert result2.activities_created == 2
        assert result2.activities_skipped == 0

        # Total should be 3
        async with test_session() as session:
            count = len((await session.execute(select(Activity))).scalars().all())
            assert count == 3


# ── API Endpoint Tests ───────────────────────────────────────────────


class TestHevyImportEndpoint:
    @pytest.mark.asyncio
    async def test_upload_csv(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/sources/import/hevy",
            files={"file": ("workouts.csv", SAMPLE_CSV.encode(), "text/csv")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["activities_created"] == 2
        assert data["rows_parsed"] == 7
        assert data["activities_skipped"] == 0

    @pytest.mark.asyncio
    async def test_upload_csv_dedup(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        # First upload
        await client.post(
            "/api/sources/import/hevy",
            files={"file": ("workouts.csv", SAMPLE_CSV.encode(), "text/csv")},
        )
        # Second upload — should skip all
        response = await client.post(
            "/api/sources/import/hevy",
            files={"file": ("workouts.csv", SAMPLE_CSV.encode(), "text/csv")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["activities_created"] == 0
        assert data["activities_skipped"] == 2

    @pytest.mark.asyncio
    async def test_upload_empty_csv(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/sources/import/hevy",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["activities_created"] == 0
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_upload_csv_with_bom(self, client, user: User) -> None:  # type: ignore[no-untyped-def]
        # Simulate a Windows-exported CSV with BOM bytes
        bom_content = SAMPLE_CSV.encode("utf-8-sig")
        response = await client.post(
            "/api/sources/import/hevy",
            files={"file": ("workouts.csv", bom_content, "text/csv")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["activities_created"] == 2
