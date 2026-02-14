from datetime import date, datetime, time

import pytest
from pydantic import ValidationError

from mycoach.schemas.activity import ActivityCreate, GymWorkoutDetailCreate
from mycoach.schemas.availability import AvailabilitySlot, WeeklyAvailabilityCreate
from mycoach.schemas.coaching import (
    CoachingInsightCreate,
    MesocycleConfigCreate,
    MesocycleConfigUpdate,
)
from mycoach.schemas.data_source import DataSourceConfigCreate
from mycoach.schemas.health import DailyHealthSnapshotCreate
from mycoach.schemas.plan import PlannedSessionCreate, WeeklyPlanCreate
from mycoach.schemas.sport_profile import SportProfileCreate, SportProfileUpdate
from mycoach.schemas.system import StatusResponse
from mycoach.schemas.user import UserCreate, UserRead, UserUpdate


class TestUserSchemas:
    def test_user_create_valid(self) -> None:
        user = UserCreate(name="John", email="john@example.com")
        assert user.name == "John"
        assert user.fitness_level == "intermediate"

    def test_user_create_all_fields(self) -> None:
        user = UserCreate(
            name="John",
            email="john@example.com",
            fitness_level="advanced",
            goals="Build muscle",
        )
        assert user.fitness_level == "advanced"
        assert user.goals == "Build muscle"

    def test_user_create_invalid_email(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(name="John", email="not-an-email")

    def test_user_create_invalid_fitness_level(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(name="John", email="john@example.com", fitness_level="pro")

    def test_user_update_partial(self) -> None:
        update = UserUpdate(name="Jane")
        assert update.name == "Jane"
        assert update.email is None

    def test_user_read_from_attributes(self) -> None:
        now = datetime.utcnow()
        user = UserRead(
            id=1,
            name="John",
            email="john@example.com",
            fitness_level="beginner",
            goals=None,
            created_at=now,
            updated_at=now,
        )
        assert user.id == 1


class TestDataSourceSchemas:
    def test_create(self) -> None:
        ds = DataSourceConfigCreate(source_type="garmin")
        assert ds.enabled is True

    def test_create_with_credentials(self) -> None:
        ds = DataSourceConfigCreate(
            source_type="hevy_csv", credentials_encrypted="encrypted_data"
        )
        assert ds.credentials_encrypted == "encrypted_data"


class TestSportProfileSchemas:
    def test_create(self) -> None:
        sp = SportProfileCreate(sport="gym")
        assert sp.skill_level == "intermediate"

    def test_update_partial(self) -> None:
        update = SportProfileUpdate(skill_level="advanced")
        assert update.goals is None

    def test_invalid_skill_level(self) -> None:
        with pytest.raises(ValidationError):
            SportProfileCreate(sport="gym", skill_level="expert")


class TestAvailabilitySchemas:
    def test_slot_valid(self) -> None:
        slot = AvailabilitySlot(
            day_of_week=0, start_time=time(9, 0), duration_minutes=60, preferred_sport="gym"
        )
        assert slot.day_of_week == 0

    def test_slot_invalid_day(self) -> None:
        with pytest.raises(ValidationError):
            AvailabilitySlot(
                day_of_week=7, start_time=time(9, 0), duration_minutes=60, preferred_sport="gym"
            )

    def test_weekly_create(self) -> None:
        avail = WeeklyAvailabilityCreate(
            week_start=date(2025, 1, 6),
            slots=[
                AvailabilitySlot(
                    day_of_week=0, start_time=time(9, 0), duration_minutes=60, preferred_sport="gym"
                )
            ],
        )
        assert len(avail.slots) == 1


class TestHealthSchemas:
    def test_create_minimal(self) -> None:
        h = DailyHealthSnapshotCreate(snapshot_date=date(2025, 1, 15))
        assert h.data_source == "garmin"

    def test_create_full(self) -> None:
        h = DailyHealthSnapshotCreate(
            snapshot_date=date(2025, 1, 15),
            resting_hr=55,
            sleep_score=82,
            body_battery_high=95,
            steps=8000,
        )
        assert h.resting_hr == 55


class TestActivitySchemas:
    def test_activity_create(self) -> None:
        a = ActivityCreate(
            sport="gym",
            title="Upper Body",
            start_time=datetime(2025, 1, 15, 10, 0),
            data_source="hevy",
        )
        assert a.gym_details is None

    def test_activity_with_gym_details(self) -> None:
        a = ActivityCreate(
            sport="gym",
            title="Legs",
            start_time=datetime(2025, 1, 15, 10, 0),
            data_source="hevy",
            gym_details=[
                GymWorkoutDetailCreate(
                    exercise_title="Squat",
                    set_index=1,
                    weight_kg=100.0,
                    reps=5,
                    rpe=8.0,
                )
            ],
        )
        assert len(a.gym_details) == 1  # type: ignore[arg-type]
        assert a.gym_details[0].exercise_title == "Squat"  # type: ignore[index]

    def test_invalid_rpe(self) -> None:
        with pytest.raises(ValidationError):
            GymWorkoutDetailCreate(
                exercise_title="Squat", set_index=1, rpe=11.0
            )


class TestPlanSchemas:
    def test_weekly_plan_create(self) -> None:
        plan = WeeklyPlanCreate(
            week_start=date(2025, 1, 6),
            sessions=[
                PlannedSessionCreate(
                    day_of_week=0, sport="gym", title="Upper Body", duration_minutes=60
                )
            ],
        )
        assert len(plan.sessions) == 1  # type: ignore[arg-type]

    def test_invalid_mesocycle_phase(self) -> None:
        with pytest.raises(ValidationError):
            WeeklyPlanCreate(week_start=date(2025, 1, 6), mesocycle_phase="invalid")


class TestCoachingSchemas:
    def test_insight_create(self) -> None:
        insight = CoachingInsightCreate(
            insight_date=date(2025, 1, 15),
            insight_type="daily_briefing",
            content='{"readiness": "go_hard"}',
        )
        assert insight.prompt_version == "v1"

    def test_invalid_insight_type(self) -> None:
        with pytest.raises(ValidationError):
            CoachingInsightCreate(
                insight_date=date(2025, 1, 15), insight_type="unknown", content="test"
            )

    def test_mesocycle_create(self) -> None:
        mc = MesocycleConfigCreate(sport="gym", start_date=date(2025, 1, 6))
        assert mc.block_length_weeks == 4
        assert mc.phase == "build"

    def test_mesocycle_update_partial(self) -> None:
        update = MesocycleConfigUpdate(current_week=3)
        assert update.phase is None


class TestSystemSchemas:
    def test_status_response(self) -> None:
        resp = StatusResponse(status="ok", environment="development")
        assert resp.status == "ok"
