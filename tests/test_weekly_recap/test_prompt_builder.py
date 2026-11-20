"""Tests for weekly recap prompt builder functions."""

from datetime import date

from mycoach.coaching.prompt_builder import (
    PROMPT_VARIABLES,
    Operation,
    _format_gym_details,
    _format_gym_history,
    _format_last_week_training_log,
    _format_plan_adherence,
    _format_weekly_gym_details,
    build_weekly_recap_prompt,
    compile_prompt,
)
from tests.test_coaching.prompt_cases import CASES, WEEK_2026_11_02_ADHERENCE


def _renamed_squat_sets() -> list[dict]:
    return [
        {
            "exercise_id": "Barbell_Squat",
            "exercise_title": "Barbell Squat",
            "set_index": 1,
            "weight_kg": 100,
            "reps": 5,
        },
        {
            "exercise_id": "Barbell_Squat",
            "exercise_title": "Back Squat",
            "set_index": 2,
            "weight_kg": 102.5,
            "reps": 5,
        },
    ]


class TestStableExerciseGrouping:
    def test_post_workout_details_group_by_exercise_id(self) -> None:
        result = _format_gym_details(_renamed_squat_sets())

        assert result.count("**Barbell Squat**") == 1
        assert "**Back Squat**" not in result

    def test_last_week_training_log_groups_by_exercise_id(self) -> None:
        result = _format_last_week_training_log(
            [
                {
                    "sport": "gym",
                    "title": "Legs",
                    "start_time": "2024-06-10",
                    "gym_details": _renamed_squat_sets(),
                }
            ]
        )

        assert result.count("**Barbell Squat**") == 1
        assert "**Back Squat**" not in result

    def test_last_week_training_log_shows_prescribed_alongside_performed(self) -> None:
        result = _format_last_week_training_log(
            [
                {
                    "sport": "gym",
                    "title": "Legs",
                    "start_time": "2024-06-10",
                    "gym_details": [
                        {
                            "exercise_id": "Barbell_Squat",
                            "exercise_title": "Barbell Squat",
                            "set_index": 1,
                            "weight_kg": 100,
                            "reps": 5,
                            "prescribed_weight_kg": 102.5,
                            "prescribed_reps": 5,
                        }
                    ],
                }
            ]
        )

        assert "prescribed 102.5kg x5" in result
        assert "performed 100kg x5" in result


class TestFormatPlanAdherence:
    def test_no_plan(self) -> None:
        result = _format_plan_adherence(None)
        assert "No training plan" in result

    def test_week_of_2026_11_02_reads_completion_first(self) -> None:
        result = _format_plan_adherence(WEEK_2026_11_02_ADHERENCE)

        assert result == (
            "Completion: 4/5 planned sessions done (80%)"
            " — 3 as planned, 1 substituted, 1 not done\n"
            "On planned day: 3/4 (informational only — never a penalty)\n"
            "Done:\n"
            "- Squat [gym] — done Wednesday (planned Monday) [session_id: 1]\n"
            "- Swim 1.7 km [swimming, aerobic base] — done Tuesday (planned Tuesday)"
            " [session_id: 2]\n"
            "- Deadlift [gym] — done Friday (planned Friday) [session_id: 4]\n"
            "Substituted:\n"
            "- Long easy run [running, aerobic base] (planned Sunday)"
            " ← Padel [padel] on Sunday: 101 min, avg HR 118, aerobic TE 2.7,"
            " delivered aerobic base [session_id: 5]\n"
            "Not done:\n"
            "- Intervals 6x3 min [running, intervals] (planned Thursday) [session_id: 3]\n"
            "Extras (unplanned, not counted):\n"
            "- Walk [walking] on Saturday: 45 min, recovery\n"
            "Plan summary: Base block week 3: two lifts, a swim, intervals and a long run."
        )

    def test_no_schedule_rigidity_language(self) -> None:
        result = _format_plan_adherence(WEEK_2026_11_02_ADHERENCE).lower()

        for phrase in ("missed", "moved", "on paper", "2/5", "40%"):
            assert phrase not in result

    def test_a_session_ticked_by_hand_is_done_with_no_day(self) -> None:
        adherence = {
            "plan_summary": None,
            "total_sessions": 1,
            "completed_sessions": 1,
            "substituted_sessions": 0,
            "adherence_pct": 100.0,
            "sessions": [
                {
                    "day": "Monday",
                    "sport": "gym",
                    "title": "Push",
                    "intent": "strength",
                    "completed": True,
                    "status": "done",
                    "on_planned_day": None,
                    "actual_day": None,
                    "substitute": None,
                }
            ],
            "extras": [],
        }

        assert _format_plan_adherence(adherence) == (
            "Completion: 1/1 planned sessions done (100%) — 1 as planned\n"
            "Done:\n"
            "- Push [gym] — ticked off by hand (planned Monday)"
        )

    def test_sick_days_excuse_sessions_out_of_the_denominator(self) -> None:
        def planned(day: str, title: str, status: str, actual: str | None = None) -> dict:  # type: ignore[type-arg]
            return {
                "day": day,
                "sport": "running",
                "title": title,
                "intent": None,
                "completed": status == "done",
                "status": status,
                "on_planned_day": None if actual is None else actual == day,
                "actual_day": actual,
                "substitute": None,
            }

        adherence = {
            "plan_summary": None,
            "total_sessions": 2,
            "completed_sessions": 2,
            "substituted_sessions": 0,
            "excused_sessions": 3,
            "excused_days": {"excused_ill": 3},
            "adherence_pct": 100.0,
            "sessions": [
                planned("Monday", "Easy run", "excused_ill"),
                planned("Tuesday", "Tempo run", "excused_ill"),
                planned("Wednesday", "Hills", "excused_ill"),
                planned("Thursday", "Steady run", "done", "Thursday"),
                planned("Saturday", "Long run", "done", "Saturday"),
            ],
            "extras": [],
        }

        assert _format_plan_adherence(adherence) == (
            "Completion: 2/2 planned sessions done (100%)"
            " — 2 as planned, 3 excused (ill 3 days)\n"
            "On planned day: 2/2 (informational only — never a penalty)\n"
            "Done:\n"
            "- Steady run [running] — done Thursday (planned Thursday)\n"
            "- Long run [running] — done Saturday (planned Saturday)\n"
            "Excused (ill), out of the count:\n"
            "- Easy run [running] (planned Monday)\n"
            "- Tempo run [running] (planned Tuesday)\n"
            "- Hills [running] (planned Wednesday)"
        )

    def test_a_travel_week_names_adapted_and_not_done_travelling_sessions(self) -> None:
        """#221: adapted counts as done; not done while travelling stays in the count."""
        circuit = {
            "external_id": "lg-1",
            "sport": "gym",
            "title": "Hotel leg circuit",
            "intent": None,
            "duration_minutes": 25,
            "avg_hr": None,
            "training_effect_aerobic": None,
        }
        adherence = {
            "plan_summary": None,
            "total_sessions": 3,
            "completed_sessions": 2,
            "substituted_sessions": 0,
            "adapted_sessions": 1,
            "excused_sessions": 1,
            "excused_days": {"excused_travel": 1},
            "adherence_pct": 66.7,
            "sessions": [
                {"day": "Monday", "sport": "running", "title": "Easy run", "intent": None,
                 "completed": True, "status": "done", "on_planned_day": True,
                 "actual_day": "Monday", "substitute": None},
                {"day": "Tuesday", "sport": "gym", "title": "Lower A", "intent": "strength",
                 "completed": True, "status": "adapted_travel", "on_planned_day": True,
                 "actual_day": "Tuesday", "substitute": circuit},
                {"day": "Wednesday", "sport": "gym", "title": "Upper A", "intent": "strength",
                 "completed": False, "status": "excused_travel", "on_planned_day": None,
                 "actual_day": None, "substitute": None},
                {"day": "Thursday", "sport": "running", "title": "Tempo run",
                 "intent": "threshold", "completed": False, "status": "not_done_travelling",
                 "on_planned_day": None, "actual_day": None, "substitute": None},
            ],
            "extras": [],
        }  # fmt: skip

        assert _format_plan_adherence(adherence) == (
            "Completion: 2/3 planned sessions done (66.7%) — 1 as planned, 1 adapted (travel),"
            " 1 not done (travelling), 1 excused (no training while travelling 1 day)\n"
            "On planned day: 2/2 (informational only — never a penalty)\n"
            "Done:\n"
            "- Easy run [running] — done Monday (planned Monday)\n"
            "Adapted (travel), counted as done:\n"
            "- Lower A [gym] (planned Tuesday) ← Hotel leg circuit [gym] on Tuesday: 25 min\n"
            "Not done (travelling), still in the count:\n"
            "- Tempo run [running, threshold] (planned Thursday)\n"
            "Excused (no training while travelling), out of the count:\n"
            "- Upper A [gym] (planned Wednesday)"
        )

    def test_empty_sessions(self) -> None:
        adherence = {
            "plan_summary": "Empty plan",
            "total_sessions": 0,
            "completed_sessions": 0,
            "adherence_pct": 0.0,
            "sessions": [],
        }
        result = _format_plan_adherence(adherence)
        assert "0/0" in result


class TestFormatWeeklyGymDetails:
    def test_empty(self) -> None:
        result = _format_weekly_gym_details([])
        assert "No gym sessions" in result

    def test_formats_sets(self) -> None:
        details = [
            {
                "session_date": "2024-06-10",
                "session_title": "Upper Body",
                "exercise_title": "Bench Press",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 8,
                "rpe": 7,
            },
            {
                "session_date": "2024-06-10",
                "session_title": "Upper Body",
                "exercise_title": "Bench Press",
                "set_index": 2,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 7,
                "rpe": 9,
            },
        ]
        result = _format_weekly_gym_details(details)
        assert "Bench Press" in result
        assert "80kg×8" in result
        assert "RPE7" in result

    def test_groups_display_label_changes_by_exercise_id(self) -> None:
        result = _format_weekly_gym_details(
            [
                {
                    "session_date": "2024-06-10",
                    "session_title": "Legs",
                    "exercise_id": "Barbell_Squat",
                    "exercise_title": "Barbell Squat",
                    "set_index": 1,
                    "set_type": "normal",
                    "weight_kg": 100,
                    "reps": 5,
                    "rpe": None,
                },
                {
                    "session_date": "2024-06-10",
                    "session_title": "Legs",
                    "exercise_id": "Barbell_Squat",
                    "exercise_title": "Back Squat",
                    "set_index": 2,
                    "set_type": "normal",
                    "weight_kg": 102.5,
                    "reps": 5,
                    "rpe": None,
                },
            ]
        )

        assert result.count("Barbell Squat [exercise_id: Barbell_Squat]:") == 1
        assert "Back Squat" not in result

    def test_groups_by_session(self) -> None:
        details = [
            {
                "session_date": "2024-06-10",
                "session_title": "A",
                "exercise_title": "Squat",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 100.0,
                "reps": 5,
                "rpe": None,
            },
            {
                "session_date": "2024-06-12",
                "session_title": "B",
                "exercise_title": "Deadlift",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 140.0,
                "reps": 3,
                "rpe": 8,
            },
        ]
        result = _format_weekly_gym_details(details)
        assert "Squat" in result
        assert "Deadlift" in result
        assert "2024-06-10" in result
        assert "2024-06-12" in result


class TestFormatGymHistory:
    def test_empty(self) -> None:
        result = _format_gym_history([])
        assert "No gym history" in result

    def test_groups_renamed_display_labels_by_exercise_id(self) -> None:
        result = _format_gym_history(
            [
                {
                    "week_start": "2024-06-03",
                    "exercise_id": "Barbell_Squat",
                    "exercise_title": "Barbell Squat",
                    "best_weight_kg": 100,
                    "best_reps": 5,
                    "total_sets": 3,
                    "avg_rpe": 8,
                },
                {
                    "week_start": "2024-06-10",
                    "exercise_id": "Barbell_Squat",
                    "exercise_title": "Back Squat",
                    "best_weight_kg": 102.5,
                    "best_reps": 5,
                    "total_sets": 3,
                    "avg_rpe": 8,
                },
            ]
        )

        assert result.count("**Barbell Squat**") == 1
        assert "**Back Squat**" not in result
        assert "Week Jun 03" in result
        assert "Week Jun 10" in result

    def test_formats_week_over_week(self) -> None:
        history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.5,
            },
            {
                "week_start": "2024-06-10",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 7,
                "total_sets": 3,
                "avg_rpe": 9.0,
            },
        ]
        result = _format_gym_history(history)
        assert "Bench Press" in result
        assert "80kg × 8" in result
        assert "80kg × 7" in result
        assert "avg RPE 7.5" in result

    def test_groups_by_exercise(self) -> None:
        history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Squat",
                "best_weight_kg": 120.0,
                "best_reps": 5,
                "total_sets": 4,
                "avg_rpe": 8.0,
            },
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 80.0,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.0,
            },
        ]
        result = _format_gym_history(history)
        assert "Squat" in result
        assert "Bench Press" in result


class TestBuildWeeklyRecapPrompt:
    def test_builds_full_prompt(self) -> None:
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence={
                "plan_summary": "Test",
                "total_sessions": 2,
                "completed_sessions": 1,
                "adherence_pct": 50.0,
                "sessions": [
                    {"day": "Monday", "sport": "gym", "title": "Push", "completed": True},
                    {"day": "Wednesday", "sport": "gym", "title": "Pull", "completed": False},
                ],
            },
            weekly_activities=[
                {
                    "sport": "gym",
                    "title": "Push",
                    "start_time": "2024-06-10",
                    "duration_minutes": 60,
                }
            ],
            health_trends=[],
            mesocycle_context="Week 2 of 4",
            weekly_gym_details=[],
            gym_history=[],
            sport_profiles=[],
        )
        assert "2024-06-10" in result
        assert "2024-06-16" in result
        assert "1/2" in result
        assert "Week 2 of 4" in result

    def test_no_plan_no_mesocycle(self) -> None:
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence=None,
            weekly_activities=[],
            health_trends=[],
        )
        assert "No training plan" in result
        assert "No mesocycle" in result

    def test_gym_details_included(self) -> None:
        gym_details = [
            {
                "session_date": "2024-06-10",
                "session_title": "Push",
                "exercise_title": "Bench Press",
                "set_index": 1,
                "set_type": "normal",
                "weight_kg": 80.0,
                "reps": 8,
                "rpe": 7,
            }
        ]
        gym_history = [
            {
                "week_start": "2024-06-03",
                "exercise_title": "Bench Press",
                "best_weight_kg": 77.5,
                "best_reps": 8,
                "total_sets": 3,
                "avg_rpe": 7.0,
            }
        ]
        result = build_weekly_recap_prompt(
            week_start=date(2024, 6, 10),
            plan_adherence=None,
            weekly_activities=[],
            health_trends=[],
            weekly_gym_details=gym_details,
            gym_history=gym_history,
            version="v2",
        )
        assert "Bench Press" in result
        assert "80kg×8" in result
        assert "77.5kg × 8" in result


class TestRecapPromptV4:
    """The v4 recap prompt does not induce schedule-rigidity language (#217).

    The v2 recap of the week of 2026-11-02 said "2/5 planned sessions done...
    40% on paper... Mon squat moved to Wed... Fix: fewer, firmer slots".
    """

    RIGID = ("on paper", "moved", "shifted", "missed", "firmer", "40%", "2/5")

    @staticmethod
    def _user_message() -> str:
        operation, inputs = CASES["weekly_recap_2026_11_02"]
        variables = PROMPT_VARIABLES[Operation(operation)](**inputs)
        return compile_prompt(Operation(operation), variables).user

    @staticmethod
    def _without_rules(message: str) -> str:
        """The message minus its Completion Rules section, the one place that names
        the banned language in order to ban it."""
        head, _, rest = message.partition("## Completion Rules")
        assert rest, "the v4 recap prompt has a Completion Rules section"
        _, _, tail = rest.partition("\n## ")
        return head + tail

    def test_headline_is_completion(self) -> None:
        message = self._user_message()
        assert "Completion: 4/5 planned sessions done (80%)" in message
        assert "The email leads with the Completion line" in message

    def test_nothing_outside_the_rules_names_rigid_language(self) -> None:
        rest = self._without_rules(self._user_message()).lower()
        for phrase in self.RIGID:
            assert phrase not in rest, phrase

    def test_rules_ban_moved_language_and_rigidity_advice(self) -> None:
        message = self._user_message()
        rules = message.partition("## Completion Rules")[2].partition("\n## ")[0]
        assert "not call it moved, shifted or missed" in rules
        assert "fewer or firmer slots" in rules
        assert "back-to-back heavy sessions" in rules
        assert "stimulus it delivered" in rules
        assert "volume note, not a completion one" in rules
