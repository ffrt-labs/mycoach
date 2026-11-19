"""The activity intent classifier (#216): what stimulus an unplanned activity delivered."""

import json
from typing import Any

from mycoach.coaching.substitution import classify_activity_intent
from mycoach.models.plan import SessionIntent


def _zones(*minutes: float) -> str:
    """Garmin's stored shape: one ``{"zoneNumber", "secsInZone"}`` per zone, Z1 first."""
    return json.dumps([{"zoneNumber": i + 1, "secsInZone": m * 60} for i, m in enumerate(minutes)])


def _activity(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sport": "padel",
        "duration_minutes": 60,
        "avg_hr": None,
        "max_hr": None,
        "hr_zones": None,
        "training_effect_aerobic": None,
        "training_effect_anaerobic": None,
        "has_performed_sets": False,
    }
    return base | overrides


# The athlete's week of 2026-11-02: Sunday's run replaced by padel.
PADEL_2026_11_08 = _activity(
    sport="padel",
    duration_minutes=101,
    avg_hr=118,
    max_hr=152,
    hr_zones=_zones(38, 46, 13, 4, 0),
    training_effect_aerobic=2.7,
    training_effect_anaerobic=0.6,
)


def test_the_padel_that_replaced_sundays_run_was_aerobic_base() -> None:
    assert classify_activity_intent(PADEL_2026_11_08) is SessionIntent.AEROBIC_BASE


def test_an_activity_without_hr_zones_is_unclassifiable() -> None:
    assert classify_activity_intent(_activity(avg_hr=130, training_effect_aerobic=3.0)) is None


def test_an_aerobic_session_with_a_high_anaerobic_load_is_not_aerobic_base() -> None:
    hard = PADEL_2026_11_08 | {"training_effect_anaerobic": 2.0}
    assert classify_activity_intent(hard) is not SessionIntent.AEROBIC_BASE


def test_an_easy_walk_mostly_in_z1_is_recovery() -> None:
    walk = _activity(
        sport="cardio",
        duration_minutes=45,
        hr_zones=_zones(36, 8, 1, 0, 0),
        training_effect_aerobic=1.2,
        training_effect_anaerobic=0.0,
    )
    assert classify_activity_intent(walk) is SessionIntent.RECOVERY


def test_a_mostly_z1_session_with_real_aerobic_load_is_not_recovery() -> None:
    long_easy = _activity(
        duration_minutes=150,
        hr_zones=_zones(100, 40, 10, 0, 0),
        training_effect_aerobic=3.1,
        training_effect_anaerobic=0.3,
    )
    assert classify_activity_intent(long_easy) is SessionIntent.AEROBIC_BASE


def test_a_tempo_ride_mostly_in_z3_and_z4_is_threshold() -> None:
    tempo = _activity(
        sport="cardio",
        duration_minutes=60,
        hr_zones=_zones(5, 15, 20, 18, 2),
        training_effect_aerobic=3.8,
        training_effect_anaerobic=1.5,
    )
    assert classify_activity_intent(tempo) is SessionIntent.THRESHOLD


def test_meaningful_z5_time_is_intervals() -> None:
    reps = _activity(
        sport="running",
        duration_minutes=50,
        hr_zones=_zones(10, 15, 10, 8, 7),
        training_effect_aerobic=3.5,
        training_effect_anaerobic=1.8,
    )
    assert classify_activity_intent(reps) is SessionIntent.INTERVALS


def test_a_high_anaerobic_training_effect_is_intervals() -> None:
    match = PADEL_2026_11_08 | {"training_effect_anaerobic": 2.5}
    assert classify_activity_intent(match) is SessionIntent.INTERVALS


def test_a_gym_session_with_performed_sets_is_strength_without_any_hr() -> None:
    lift = _activity(sport="gym", has_performed_sets=True)
    assert classify_activity_intent(lift) is SessionIntent.STRENGTH


def test_a_mixed_session_matching_no_rule_is_unclassifiable() -> None:
    mixed = _activity(hr_zones=_zones(10, 15, 15, 10, 2), training_effect_anaerobic=1.0)
    assert classify_activity_intent(mixed) is None
