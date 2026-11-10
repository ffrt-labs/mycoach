"""The adherence formulas: how a performed session compares to its prescription."""

from typing import Any

import pytest

from mycoach.coaching.prescriptions import Prescription
from mycoach.models.activity import GymWorkoutDetail
from mycoach.observability.adherence import cardio_adherence, gym_adherence


def performed(
    exercise_id: str | None,
    *,
    weight: float | None = None,
    reps: int | None = None,
    rpe: float | None = None,
    set_type: str = "normal",
) -> GymWorkoutDetail:
    return GymWorkoutDetail(
        exercise_id=exercise_id,
        exercise_title=exercise_id or "?",
        set_index=0,
        set_type=set_type,
        weight_kg=weight,
        reps=reps,
        rpe=rpe,
    )


def prescribed(
    exercise_id: str,
    sets: int,
    *,
    reps: str = "8",
    weight: float | None = None,
    rpe: float | None = None,
) -> dict[str, Any]:
    return {
        "exercise_id": exercise_id,
        "name": exercise_id,
        "sets": sets,
        "reps": reps,
        "target_weight_kg": weight,
        "rpe": rpe,
    }


class TestGymAdherence:
    def test_all_sets_done_as_prescribed(self) -> None:
        details = {"exercises": [prescribed("bench", 2, reps="8", weight=100, rpe=8)]}
        sets = [performed("bench", weight=100, reps=8, rpe=8) for _ in range(2)]

        assert gym_adherence(details, sets) == {
            "adherence_set_completion": 1.0,
            "adherence_weight_delta_pct": 0.0,
            "adherence_reps_delta": 0.0,
            "adherence_rpe_delta": 0.0,
        }

    def test_heavier_fewer_reps_harder(self) -> None:
        details = {"exercises": [prescribed("bench", 2, reps="10", weight=100, rpe=7)]}
        sets = [
            performed("bench", weight=110, reps=8, rpe=9),
            performed("bench", weight=105, reps=9, rpe=8),
        ]

        values = gym_adherence(details, sets)

        assert values["adherence_weight_delta_pct"] == pytest.approx(7.5)
        assert values["adherence_reps_delta"] == pytest.approx(-1.5)
        assert values["adherence_rpe_delta"] == pytest.approx(1.5)

    def test_unmatched_exercise_is_ignored_and_a_skipped_one_counts_as_undone(self) -> None:
        details = {
            "exercises": [prescribed("bench", 3, weight=100), prescribed("row", 1, weight=50)]
        }
        sets = [
            performed("bench", weight=100, reps=8),
            performed("bench", weight=100, reps=8),
            performed("curl", weight=20, reps=30),
        ]

        values = gym_adherence(details, sets)

        # 2 of 4 prescribed sets; the curl matches nothing and moves no delta.
        assert values["adherence_set_completion"] == pytest.approx(0.5)
        assert values["adherence_weight_delta_pct"] == 0.0
        assert values["adherence_reps_delta"] == 0.0

    def test_extra_sets_do_not_push_completion_past_one(self) -> None:
        details = {"exercises": [prescribed("bench", 1)]}
        sets = [performed("bench", reps=8), performed("bench", reps=8)]

        assert gym_adherence(details, sets)["adherence_set_completion"] == 1.0

    def test_warmups_are_not_performed_sets(self) -> None:
        details = {"exercises": [prescribed("bench", 2)]}
        sets = [performed("bench", reps=8, set_type="warmup"), performed("bench", reps=8)]

        assert gym_adherence(details, sets)["adherence_set_completion"] == 0.5

    def test_rpe_missing_on_either_side_is_skipped(self) -> None:
        details = {"exercises": [prescribed("bench", 1, rpe=8), prescribed("row", 1, rpe=None)]}
        sets = [performed("bench", reps=8, rpe=None), performed("row", reps=8, rpe=9)]

        assert "adherence_rpe_delta" not in gym_adherence(details, sets)

    def test_rpe_delta_uses_only_sets_with_both_sides(self) -> None:
        details = {"exercises": [prescribed("bench", 2, rpe=8)]}
        sets = [performed("bench", reps=8, rpe=9), performed("bench", reps=8, rpe=None)]

        assert gym_adherence(details, sets)["adherence_rpe_delta"] == 1.0

    def test_reps_against_a_range_are_measured_from_its_midpoint(self) -> None:
        details = {"exercises": [prescribed("bench", 3, reps="8-10")]}
        sets = [
            performed("bench", reps=10),
            performed("bench", reps=6),
            performed("bench", reps=8),
        ]

        # Against the midpoint 9: +1, -3 and -1, a mean of -1.
        assert gym_adherence(details, sets)["adherence_reps_delta"] == pytest.approx(-1.0)

    def test_the_sets_own_prescription_wins_over_the_plan_details(self) -> None:
        details = {"exercises": [prescribed("bench", 1, reps="8", weight=100)]}
        sets = [performed("bench", weight=90, reps=10)]
        # The fulfillment is coaching's, keyed by the set's exercise and index (performed() uses title=id, 0).
        fulfilled = {("bench", 0): Prescription(weight_kg=90, reps=12)}

        values = gym_adherence(details, sets, fulfilled)

        assert values["adherence_weight_delta_pct"] == 0.0
        assert values["adherence_reps_delta"] == -2.0

    def test_no_prescribed_weight_emits_no_weight_delta(self) -> None:
        details = {"exercises": [prescribed("bench", 1, weight=None)]}
        sets = [performed("bench", weight=80, reps=8)]

        assert "adherence_weight_delta_pct" not in gym_adherence(details, sets)

    def test_no_prescribed_exercises_emits_nothing(self) -> None:
        assert gym_adherence(None, [performed("bench", reps=8)]) == {}


class TestCardioAdherence:
    def test_ratio_of_actual_to_planned(self) -> None:
        assert cardio_adherence(planned_minutes=40, actual_minutes=30) == {
            "adherence_duration_ratio": 0.75
        }

    def test_no_planned_duration_emits_nothing(self) -> None:
        assert cardio_adherence(planned_minutes=None, actual_minutes=30) == {}
        assert cardio_adherence(planned_minutes=0, actual_minutes=30) == {}

    def test_no_actual_duration_emits_nothing(self) -> None:
        assert cardio_adherence(planned_minutes=40, actual_minutes=None) == {}
