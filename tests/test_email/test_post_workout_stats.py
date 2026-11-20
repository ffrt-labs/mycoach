"""The post-workout email's code-rendered numbers: stat grid, zones and trend (#239)."""

import json
import re
from typing import Any
from unittest.mock import patch

from mycoach.email.sender import send_post_workout
from tests.test_email.test_post_workout_notes import NOTES, _section, _settings, _text
from tests.test_email.test_post_workout_week import _session, _week

SWIM: dict[str, Any] = {
    "external_id": "swim-today",
    "title": "Pool swim",
    "sport": "swimming",
    "start_time": "2026-11-17 07:00:00",
    "distance_meters": 1500.0,
    "moving_duration_seconds": 2166.0,
    "duration_minutes": 40.0,
    "avg_swolf": 51.0,
    "avg_strokes_per_length": 15.1,
    "training_effect_aerobic": 2.4,
    "epoc": 46.3,
    "sources": ["garmin"],
}

LAST_SWIM: dict[str, Any] = {
    "external_id": "swim-last",
    "sport": "swimming",
    "start_time": "2026-11-05 07:00:00",
    "distance_meters": 1500.0,
    "moving_duration_seconds": 2265.0,
    "avg_swolf": 54.0,
    "avg_strokes_per_length": 16.3,
}

SWIM_PLAN: dict[str, Any] = {
    "title": "Aerobic base swim + technique",
    "sport": "swimming",
    "duration_minutes": 45,
    "details": {"distance_km": 2},
    "intent": "aerobic_base",
}


def _render(
    activity: dict[str, Any] | None = None, content: dict[str, Any] | None = NOTES, **views: Any
) -> str:
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_post_workout(content, "Pool swim", _settings(), activity=activity or SWIM, **views)
    return str(mock_send.call_args[0][2])


def _grid(html: str) -> str:
    return _text(html.split('class="stat-grid"', 1)[1].split("</table>", 1)[0].split(">", 1)[1])


# --- Stat grid ------------------------------------------------------------------------


def test_a_swim_shows_its_six_tiles_against_the_plan_and_the_last_swim() -> None:
    grid = _grid(_render(planned=SWIM_PLAN, history=[LAST_SWIM]))

    assert grid == (
        "Distance 1500 m of 2000 planned "
        "Moving time 36:06 of 45 min planned "
        "Pace 2:24 /100m −7 s vs last "
        "SWOLF 51 −3 vs last "
        "Strokes / length 15.1 −1.2 vs last "
        "Training effect 2.4 Maintaining · load 46"
    )


RUN: dict[str, Any] = {
    "external_id": "run-today",
    "title": "Easy run",
    "sport": "running",
    "distance_meters": 8040.0,
    "moving_duration_seconds": 2532.0,
    "avg_hr": 142,
    "avg_cadence": 168.4,
    "training_effect_aerobic": 3.1,
    "sources": ["garmin"],
}


def test_a_run_shows_distance_time_pace_heart_rate_cadence_and_training_effect() -> None:
    grid = _grid(_render(RUN))

    assert grid == (
        "Distance 8.04 km "
        "Time 42:12 "
        "Pace 5:15 /km "
        "Avg HR 142 bpm "
        "Cadence 168 spm "
        "Training effect 3.1 Improving"
    )


GYM: dict[str, Any] = {
    "external_id": "gym-today",
    "title": "Squat Day",
    "sport": "gym",
    "duration_minutes": 52.0,
    "training_effect_aerobic": 2.1,
    "sources": ["logger"],
}

GYM_PLAN: dict[str, Any] = {
    "title": "Squat Day",
    "sport": "gym",
    "duration_minutes": 60,
    "details": '{"exercises": [{"name": "Squat", "sets": 3, "reps": 5}, '
    '{"name": "Romanian Deadlift", "sets": 3, "reps": 8}]}',
    "intent": "strength",
}


def _set(
    title: str,
    index: int,
    kg: float | None,
    reps: int | None,
    rpe: float | None = None,
    **more: Any,
) -> dict[str, Any]:
    return {
        "exercise_title": title,
        "set_index": index,
        "set_type": "normal",
        "weight_kg": kg,
        "reps": reps,
        "rpe": rpe,
        "prescribed_weight_kg": None,
        "prescribed_reps": None,
        **more,
    }


GYM_SETS = [
    _set("Squat", 0, 60, 8, set_type="warmup"),
    _set("Squat", 1, 100, 5, 7, prescribed_weight_kg=100, prescribed_reps=5),
    _set("Squat", 2, 100, 5, 8, prescribed_weight_kg=100, prescribed_reps=5),
    _set("Romanian Deadlift", 0, 80, 8, 7, prescribed_weight_kg=80, prescribed_reps=8),
    _set("Romanian Deadlift", 1, 80, 8, None, prescribed_weight_kg=80, prescribed_reps=8),
]


def test_a_gym_session_shows_sets_against_the_prescription_top_set_volume_rpe_and_time() -> None:
    grid = _grid(_render(GYM, planned=GYM_PLAN, sets=GYM_SETS))

    assert grid == ("Sets 4 /6 Top set 100 kg × 5 Squat Volume 2280 kg Avg RPE 7.3 Duration 52 min")


def test_gym_sets_have_no_planned_count_without_a_prescription_fulfillment() -> None:
    unprescribed = [{**s, "prescribed_weight_kg": None, "prescribed_reps": None} for s in GYM_SETS]

    grid = _grid(_render(GYM, planned=GYM_PLAN, sets=unprescribed))

    assert grid.startswith("Sets 4 Top set")


def test_a_gym_session_with_garmin_data_shows_its_training_effect() -> None:
    grid = _grid(_render({**GYM, "sources": ["garmin", "logger"]}, sets=GYM_SETS))

    assert grid.endswith("Duration 52 min Training effect 2.1 Maintaining")


def test_a_tile_with_no_value_is_left_out_never_shown_as_a_dash() -> None:
    sparse = {"external_id": "swim-2", "sport": "swimming", "distance_meters": 1200.0}

    grid = _grid(_render(sparse, history=[LAST_SWIM]))

    assert grid == "Distance 1200 m"


def test_stat_tiles_sit_three_to_a_row_with_the_last_row_padded() -> None:
    html = _render({**SWIM, "avg_swolf": None})  # five tiles: a row of 3, then 2 + 1 empty
    grid = html.split('class="stat-grid"', 1)[1].split("</table>", 1)[0]

    rows = grid.split("<tr>")[1:]
    assert [len(re.findall(r"<td ", row)) for row in rows] == [3, 3]
    assert set(re.findall(r'<td [^>]*style="width:([\d.]+)%', grid)) == {"33.33"}


def test_an_activity_with_no_numbers_has_no_stat_grid() -> None:
    assert 'class="stat-grid"' not in _render({"external_id": "x", "sport": "swimming"})


def test_the_numbers_are_the_same_with_or_without_coach_notes() -> None:
    """No tile or table value comes from what the coach wrote."""
    views: dict[str, Any] = {"planned": SWIM_PLAN, "history": [LAST_SWIM]}
    with_notes = _render(ZONED_SWIM, **views)
    without = _render(ZONED_SWIM, content=None, **views)

    assert _grid(with_notes) == _grid(without)
    for marker in ('class="zone-seg ', 'class="trend-today'):
        assert with_notes.count(marker) == without.count(marker) > 0


# --- Heart rate zones -----------------------------------------------------------------

ZONES = json.dumps(
    [
        {"zoneNumber": 1, "secsInZone": 336},
        {"zoneNumber": 2, "secsInZone": 1038},
        {"zoneNumber": 3, "secsInZone": 480},
        {"zoneNumber": 4, "secsInZone": 0},
        {"zoneNumber": 5, "secsInZone": 0},
    ]
)
ZONED_SWIM = {**SWIM, "hr_zones": ZONES}


def test_the_zone_bar_shows_each_zones_share_and_minutes_then_the_zone_tip() -> None:
    zones = _text(_section(_render(ZONED_SWIM), "Heart rate zones"))

    assert zones.startswith(
        "18% 56% 26% Z1 5.6 min Z2 17.3 min Z3 8.0 min Z4–5 0 min "
        "Watch Above the keep-Z2 target; no Z4-5."
    )


def test_the_zone_bar_widths_follow_the_minutes() -> None:
    zones = _section(_render(ZONED_SWIM), "Heart rate zones")

    assert re.findall(r'class="zone-seg [^"]*" style="width:(\d+)%', zones) == ["18", "56", "26"]


def test_the_target_comes_from_the_planned_sessions_intent() -> None:
    html = _render(ZONED_SWIM, planned=SWIM_PLAN)

    assert "Target: Z2" in _text(html.split("Heart rate zones", 1)[1].split("Z1", 1)[0])


def test_the_target_falls_back_to_the_intent_of_the_session_the_activity_did() -> None:
    week = _week(
        [_session("Tuesday", "Threshold swim", "done", intent="threshold", done_by="swim-today")]
    )

    html = _render(ZONED_SWIM, week=week)

    assert "Target: Z3–4" in _text(html.split("Heart rate zones", 1)[1].split("Z1", 1)[0])


def test_an_intent_with_no_zone_target_has_no_target_label() -> None:
    html = _render(ZONED_SWIM, planned={**SWIM_PLAN, "intent": "skill"})

    assert "Target" not in _section(html, "Heart rate zones")


def test_the_zone_bar_goes_out_without_coach_notes() -> None:
    zones = _text(_section(_render(ZONED_SWIM, content=None), "Heart rate zones"))

    assert zones.startswith("18% 56% 26% Z1 5.6 min")
    assert "Watch" not in zones


def test_no_zone_data_and_no_zone_tip_leaves_the_section_out() -> None:
    assert "Heart rate zones" not in _render(SWIM, content=None)


# --- Trend ----------------------------------------------------------------------------

OLDER_SWIM: dict[str, Any] = {
    "external_id": "swim-older",
    "sport": "swimming",
    "start_time": "2026-10-29 07:00:00",
    "distance_meters": 1600.0,
    "moving_duration_seconds": 2208.0,
    "avg_swolf": 48.0,
    "avg_strokes_per_length": 14.5,
}


def test_the_trend_table_shows_today_last_and_best_per_metric_then_the_trend_note() -> None:
    html = _render(history=[LAST_SWIM, OLDER_SWIM])

    trend = _text(_section(html, "Trend · last 3 swims"))

    assert trend.startswith(
        "Metric Today 05 Nov Best "
        "Pace /100m 2:24 2:31 2:18 "
        "SWOLF 51 54 48 "
        "Strokes / length 15.1 16.3 14.5 "
        "Distance 1500 1500 1600 "
        "Efficiency is recovering after the early-November dip."
    )


def test_a_metric_the_last_swim_lacks_shows_a_dash_in_its_column() -> None:
    last = {**LAST_SWIM, "avg_swolf": None}

    trend = _text(_section(_render(history=[last]), "Trend · last 2 swims"))

    assert "SWOLF 51 — 51 " in trend


def test_todays_value_is_marked_good_when_it_beats_the_last_one() -> None:
    trend = _section(_render(history=[LAST_SWIM, OLDER_SWIM]), "Trend · last 3 swims")

    today = re.findall(r'class="trend-today (tone-good|strong) [^"]*"[^>]*>([^<]+)<', trend)
    assert today == [
        ("tone-good", "2:24"),
        ("tone-good", "51"),
        ("tone-good", "15.1"),
        ("strong", "1500"),
    ]


def test_a_run_trends_pace_and_distance() -> None:
    last_run = {**RUN, "external_id": "run-last", "start_time": "2026-11-10 07:00:00"}

    trend = _text(_section(_render(RUN, history=[last_run]), "Trend · last 2 runs"))

    assert trend.startswith(
        "Metric Today 10 Nov Best Pace /km 5:15 5:15 5:15 Distance 8.04 8.04 8.04"
    )


def test_the_trend_table_goes_out_without_coach_notes() -> None:
    trend = _text(_section(_render(content=None, history=[LAST_SWIM]), "Trend · last 2 swims"))

    assert trend.startswith("Metric Today 05 Nov Best Pace /100m")


def test_no_history_leaves_the_table_out_but_keeps_the_trend_note() -> None:
    trend = _text(_section(_render(), "Trend"))

    assert trend.startswith("Efficiency is recovering")
    assert "Today" not in trend


# --- Dark mode ------------------------------------------------------------------------


def _dark(html: str) -> str:
    from tests.test_email.test_layout import _media_block

    return _media_block(html, "prefers-color-scheme: dark")


def _dark_rule(html: str, css_class: str) -> re.Match[str]:
    rule = re.search(r"\." + re.escape(css_class) + r"\s*\{([^}]*)\}", _dark(html))
    assert rule, f"no dark rule for .{css_class}"
    return rule


def test_a_tile_sub_lines_tone_keeps_its_colour_in_dark_mode() -> None:
    """The dark ``.muted`` override must not wipe a good/watch/alert sub-line."""
    from mycoach.email.tones import CHIP_STYLES

    html = _render(planned=SWIM_PLAN, history=[LAST_SWIM])
    subs = re.findall(r'<div class="muted tone-(\w+)" style="([^"]*)">', html)

    assert subs and {tone for tone, _ in subs} <= {"good", "watch", "alert"}
    assert all("color:" not in style for _, style in subs), "the tone colour is a class"
    muted = re.search(r"\.muted\s*\{", _dark(html))
    for tone, _ in subs:
        rule = _dark_rule(html, f"tone-{tone}")
        assert f"color:{CHIP_STYLES[tone].dark.fg} !important" in rule.group(1)
        assert muted and muted.start() < rule.start(), "the tone rule comes after .muted"


def test_the_zone_bar_colours_come_from_classes_with_dark_overrides() -> None:
    html = _render(ZONED_SWIM)
    zones = _section(html, "Heart rate zones")

    segs = re.findall(r'class="zone-seg (zone-\w+)" style="([^"]*)"', zones)
    assert [css for css, _ in segs] == ["zone-z1", "zone-z2", "zone-z3"]
    assert all("background:" not in s and "color:" not in s for _, s in segs)
    for css in ("zone-z1", "zone-z2", "zone-z3", "zone-z45"):
        rule = _dark_rule(html, css).group(1)
        assert "background:" in rule and "!important" in rule


def test_the_trend_tables_improved_value_keeps_its_colour_in_dark_mode() -> None:
    from mycoach.email.tones import CHIP_STYLES

    html = _render(history=[LAST_SWIM, OLDER_SWIM])
    trend = _section(html, "Trend · last 3 swims")

    assert 'class="trend-today tone-good ' in trend
    assert "#047857" not in trend
    rule = _dark_rule(html, "tone-good").group(1)
    assert f"color:{CHIP_STYLES['good'].dark.fg} !important" in rule
