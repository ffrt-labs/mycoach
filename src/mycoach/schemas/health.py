from datetime import date, datetime

from pydantic import BaseModel


class DailyHealthSnapshotBase(BaseModel):
    snapshot_date: date

    # Heart rate
    resting_hr: int | None = None
    max_hr: int | None = None
    avg_hr: int | None = None

    # HRV
    hrv_status: float | None = None
    hrv_7day_avg: float | None = None

    # Sleep
    sleep_duration_minutes: int | None = None
    sleep_score: int | None = None
    sleep_deep_minutes: int | None = None
    sleep_light_minutes: int | None = None
    sleep_rem_minutes: int | None = None
    sleep_awake_minutes: int | None = None

    # Body Battery & Stress
    body_battery_high: int | None = None
    body_battery_low: int | None = None
    avg_stress: int | None = None

    # Training metrics
    training_readiness: int | None = None
    training_load: float | None = None
    training_status: str | None = None
    vo2_max: float | None = None

    # Other
    steps: int | None = None
    respiration_avg: float | None = None
    spo2_avg: float | None = None
    intensity_minutes: int | None = None

    data_source: str = "garmin"


class DailyHealthSnapshotCreate(DailyHealthSnapshotBase):
    raw_data: str | None = None


class DailyHealthSnapshotRead(DailyHealthSnapshotBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
