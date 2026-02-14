"""Tests for Garmin source: mappers, source orchestration, and sync endpoint."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from mycoach.models.activity import Activity
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from mycoach.sources.garmin.mappers import (
    import_activities,
    import_health_snapshot,
    map_activity,
    map_health_snapshot,
)
from mycoach.sources.garmin.source import GarminSource

# ── Sample Garmin API responses ──────────────────────────────────────

SAMPLE_STATS = {
    "restingHeartRate": 58,
    "maxHeartRate": 172,
    "averageHeartRate": 68,
    "totalSteps": 10234,
    "intensityMinutes": 45,
}

SAMPLE_SLEEP = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 28800,  # 480 minutes
        "sleepScores": {"overall": {"value": 82}},
        "deepSleepSeconds": 5400,  # 90 min
        "lightSleepSeconds": 14400,  # 240 min
        "remSleepSeconds": 7200,  # 120 min
        "awakeSleepSeconds": 1800,  # 30 min
    }
}

SAMPLE_HRV = {
    "hrvSummary": {
        "lastNightAvg": 42.5,
        "weeklyAvg": 40.0,
    }
}

SAMPLE_STRESS = {"overallStressLevel": 32}

SAMPLE_BODY_BATTERY = [
    {"charged": 85, "drained": 20},
    {"charged": 60, "drained": 45},
]

SAMPLE_TRAINING_READINESS = {"score": 72}

SAMPLE_TRAINING_STATUS = {
    "trainingLoad": 345.5,
    "trainingStatus": "PRODUCTIVE",
}

SAMPLE_MAX_METRICS = {"generic": {"vo2MaxValue": 48.5}}

SAMPLE_RESPIRATION = {"avgWakingRespirationValue": 15.2}

SAMPLE_SPO2 = {"averageSpo2": 97.5}

SAMPLE_ACTIVITY_RAW = {
    "activityId": 12345678,
    "activityName": "Morning Swim",
    "activityType": {"typeKey": "lap_swimming"},
    "startTimeLocal": "2024-06-10T07:00:00",
    "duration": 3600.0,
    "averageHR": 142,
    "maxHR": 168,
    "calories": 450,
    "aerobicTrainingEffect": 3.2,
    "anaerobicTrainingEffect": 1.5,
}

SAMPLE_ACTIVITY_GYM = {
    "activityId": 87654321,
    "activityName": "Strength Training",
    "activityType": {"typeKey": "strength_training"},
    "startTimeLocal": "2024-06-10T17:00:00",
    "duration": 4500.0,
    "averageHR": 120,
    "maxHR": 155,
    "calories": 320,
    "aerobicTrainingEffect": 2.0,
    "anaerobicTrainingEffect": 2.8,
}


# ── Health Snapshot Mapper Tests ─────────────────────────────────────


class TestMapHealthSnapshot:
    def test_full_snapshot(self) -> None:
        snapshot = map_health_snapshot(
            user_id=1,
            snapshot_date=date(2024, 6, 10),
            stats=SAMPLE_STATS,
            sleep=SAMPLE_SLEEP,
            hrv=SAMPLE_HRV,
            stress=SAMPLE_STRESS,
            body_battery=SAMPLE_BODY_BATTERY,
            training_readiness=SAMPLE_TRAINING_READINESS,
            training_status=SAMPLE_TRAINING_STATUS,
            max_metrics=SAMPLE_MAX_METRICS,
            respiration=SAMPLE_RESPIRATION,
            spo2=SAMPLE_SPO2,
        )
        assert snapshot.user_id == 1
        assert snapshot.snapshot_date == date(2024, 6, 10)
        assert snapshot.resting_hr == 58
        assert snapshot.max_hr == 172
        assert snapshot.avg_hr == 68
        assert snapshot.hrv_status == 42.5
        assert snapshot.hrv_7day_avg == 40.0
        assert snapshot.sleep_duration_minutes == 480
        assert snapshot.sleep_score == 82
        assert snapshot.sleep_deep_minutes == 90
        assert snapshot.sleep_light_minutes == 240
        assert snapshot.sleep_rem_minutes == 120
        assert snapshot.sleep_awake_minutes == 30
        assert snapshot.body_battery_high == 85
        assert snapshot.body_battery_low == 20
        assert snapshot.avg_stress == 32
        assert snapshot.training_readiness == 72
        assert snapshot.training_load == 345.5
        assert snapshot.training_status == "PRODUCTIVE"
        assert snapshot.vo2_max == 48.5
        assert snapshot.steps == 10234
        assert snapshot.respiration_avg == 15.2
        assert snapshot.spo2_avg == 97.5
        assert snapshot.intensity_minutes == 45
        assert snapshot.data_source == "garmin"
        assert snapshot.raw_data is not None

    def test_stats_only(self) -> None:
        """Handles missing optional data gracefully."""
        snapshot = map_health_snapshot(
            user_id=1,
            snapshot_date=date(2024, 6, 10),
            stats=SAMPLE_STATS,
        )
        assert snapshot.resting_hr == 58
        assert snapshot.steps == 10234
        assert snapshot.sleep_duration_minutes is None
        assert snapshot.hrv_status is None
        assert snapshot.body_battery_high is None
        assert snapshot.vo2_max is None

    def test_empty_stats(self) -> None:
        """Empty stats still creates a valid snapshot."""
        snapshot = map_health_snapshot(
            user_id=1,
            snapshot_date=date(2024, 6, 10),
            stats={},
        )
        assert snapshot.resting_hr is None
        assert snapshot.steps is None


# ── Activity Mapper Tests ────────────────────────────────────────────


class TestMapActivity:
    def test_swimming_activity(self) -> None:
        activity = map_activity(user_id=1, raw=SAMPLE_ACTIVITY_RAW)
        assert activity.sport == "swimming"
        assert activity.title == "Morning Swim"
        assert activity.avg_hr == 142
        assert activity.max_hr == 168
        assert activity.calories == 450
        assert activity.duration_minutes == 60
        assert activity.garmin_activity_id == "12345678"
        assert activity.data_source == "garmin"
        assert activity.training_effect_aerobic == 3.2
        assert activity.end_time is not None

    def test_gym_activity(self) -> None:
        activity = map_activity(user_id=1, raw=SAMPLE_ACTIVITY_GYM)
        assert activity.sport == "gym"
        assert activity.title == "Strength Training"
        assert activity.duration_minutes == 75

    def test_unknown_activity_type(self) -> None:
        raw = {**SAMPLE_ACTIVITY_RAW, "activityType": {"typeKey": "pickleball"}}
        activity = map_activity(user_id=1, raw=raw)
        assert activity.sport == "other"

    def test_missing_fields(self) -> None:
        raw = {
            "activityId": 99999,
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2024-06-10T08:00:00",
        }
        activity = map_activity(user_id=1, raw=raw)
        assert activity.sport == "cardio"
        assert activity.title == "running"
        assert activity.avg_hr is None
        assert activity.duration_minutes is None


# ── DB Import Tests ──────────────────────────────────────────────────


async def _create_user(session):  # type: ignore[no-untyped-def]
    user = User(name="Test User", email="test@example.com", fitness_level="intermediate")
    session.add(user)
    await session.flush()
    return user


class TestImportHealthSnapshot:
    async def test_import_new_snapshot(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            user = await _create_user(session)
            snapshot = map_health_snapshot(
                user_id=user.id,
                snapshot_date=date(2024, 6, 10),
                stats=SAMPLE_STATS,
            )
            created = await import_health_snapshot(session, snapshot)
            await session.commit()

            assert created is True
            result = await session.execute(select(DailyHealthSnapshot))
            rows = result.scalars().all()
            assert len(rows) == 1
            assert rows[0].resting_hr == 58

    async def test_skip_duplicate_snapshot(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            user = await _create_user(session)
            s1 = map_health_snapshot(
                user_id=user.id, snapshot_date=date(2024, 6, 10), stats=SAMPLE_STATS
            )
            await import_health_snapshot(session, s1)
            await session.commit()

            s2 = map_health_snapshot(
                user_id=user.id, snapshot_date=date(2024, 6, 10), stats=SAMPLE_STATS
            )
            created = await import_health_snapshot(session, s2)
            assert created is False


class TestImportActivities:
    async def test_import_new_activities(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            user = await _create_user(session)
            result = await import_activities(
                session, user.id, [SAMPLE_ACTIVITY_RAW, SAMPLE_ACTIVITY_GYM]
            )
            await session.commit()

            assert result.activities_created == 2
            assert result.activities_skipped == 0

            activities = (await session.execute(select(Activity))).scalars().all()
            assert len(activities) == 2

    async def test_skip_duplicate_activity(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            user = await _create_user(session)
            await import_activities(session, user.id, [SAMPLE_ACTIVITY_RAW])
            await session.commit()

            result = await import_activities(session, user.id, [SAMPLE_ACTIVITY_RAW])
            assert result.activities_created == 0
            assert result.activities_skipped == 1

    async def test_activity_missing_id(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            user = await _create_user(session)
            bad = {"activityType": {"typeKey": "running"}, "activityName": "No ID"}
            result = await import_activities(session, user.id, [bad])
            assert result.activities_created == 0
            assert result.errors is not None
            assert len(result.errors) == 1


# ── GarminSource Integration Tests ──────────────────────────────────


class TestGarminSource:
    async def test_fetch_and_import(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        mock_client = MagicMock()
        mock_client.connect.return_value = True
        mock_client.get_stats.return_value = SAMPLE_STATS
        mock_client.get_sleep_data.return_value = SAMPLE_SLEEP
        mock_client.get_hrv_data.return_value = SAMPLE_HRV
        mock_client.get_stress_data.return_value = SAMPLE_STRESS
        mock_client.get_body_battery.return_value = SAMPLE_BODY_BATTERY
        mock_client.get_training_readiness.return_value = SAMPLE_TRAINING_READINESS
        mock_client.get_training_status.return_value = SAMPLE_TRAINING_STATUS
        mock_client.get_max_metrics.return_value = SAMPLE_MAX_METRICS
        mock_client.get_respiration_data.return_value = SAMPLE_RESPIRATION
        mock_client.get_spo2_data.return_value = SAMPLE_SPO2
        mock_client.get_activities_by_date.return_value = [SAMPLE_ACTIVITY_RAW]

        source = GarminSource(client=mock_client)

        async with test_session() as session:
            user = await _create_user(session)
            await session.commit()

            since = datetime(2024, 6, 10)
            result = await source.fetch_and_import(session, user.id, since=since)

            assert result.source_type == "garmin"
            assert result.activities_created == 1
            assert result.health_snapshots_created >= 1

    async def test_auth_failure(self, setup_db) -> None:  # type: ignore[no-untyped-def]
        mock_client = MagicMock()
        mock_client.connect.return_value = False

        source = GarminSource(client=mock_client)
        assert await source.authenticate() is False


# ── API Endpoint Tests ───────────────────────────────────────────────


class TestSyncGarminEndpoint:
    @patch("mycoach.api.routes.sources.GarminSource")
    async def test_sync_success(self, mock_source_cls, client) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import test_session

        async with test_session() as session:
            await _create_user(session)
            await session.commit()

        mock_source = MagicMock()
        mock_source_cls.return_value = mock_source
        mock_source.authenticate = AsyncMock(return_value=True)
        mock_source.fetch_and_import = AsyncMock(
            return_value=MagicMock(
                activities_created=2,
                activities_skipped=0,
                health_snapshots_created=3,
                errors=None,
            )
        )

        resp = await client.post("/api/sources/sync/garmin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["activities_created"] == 2
        assert data["health_snapshots_created"] == 3

    @patch("mycoach.api.routes.sources.GarminSource")
    async def test_sync_auth_failure(self, mock_source_cls, client) -> None:  # type: ignore[no-untyped-def]
        mock_source = MagicMock()
        mock_source_cls.return_value = mock_source
        mock_source.authenticate = AsyncMock(return_value=False)

        resp = await client.post("/api/sources/sync/garmin")
        assert resp.status_code == 503

    @patch("mycoach.api.routes.sources.GarminSource")
    async def test_sync_with_days_param(self, mock_source_cls, client) -> None:  # type: ignore[no-untyped-def]
        mock_source = MagicMock()
        mock_source_cls.return_value = mock_source
        mock_source.authenticate = AsyncMock(return_value=True)
        mock_source.fetch_and_import = AsyncMock(
            return_value=MagicMock(
                activities_created=0,
                activities_skipped=0,
                health_snapshots_created=0,
                errors=None,
            )
        )

        resp = await client.post("/api/sources/sync/garmin?days=14")
        assert resp.status_code == 200
