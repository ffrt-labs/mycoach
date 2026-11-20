"""The post-workout email's code-rendered week status and Still open this week (#235)."""

import html as html_lib
import re
from datetime import date
from typing import Any
from unittest.mock import patch

from mycoach.coaching.context import (
    ExtraActivity,
    OpenSession,
    OpenThisWeek,
    PlanAdherence,
    SessionAdherence,
)
from mycoach.coaching.life_context import Episode, LifeContext, RampBack
from mycoach.config import Settings
from mycoach.email.sender import send_post_workout
from mycoach.models.life_context import EpisodeKind, Equipment

ANALYSIS = {"performance_summary": "Steady aerobic swim."}
SWIM = {"external_id": "swim-1", "title": "Pool swim", "sport": "swimming"}
THURSDAY = date(2026, 11, 19)


def _settings() -> Settings:
    return Settings(
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        app_base_url="https://coach.example.com",
        env="test",
    )


def _render(activity: dict[str, Any] | None = SWIM, **views: Any) -> str:
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_post_workout(ANALYSIS, "Pool swim", _settings(), activity=activity, **views)
    return str(mock_send.call_args[0][2])


def _text(html: str) -> str:
    """The email's visible text, whitespace collapsed."""
    html = re.sub(r"<(style|head)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _session(
    day: str,
    title: str,
    status: str = "not_done",
    *,
    intent: str | None = None,
    done_by: str | None = None,
    substitute_id: str | None = None,
) -> SessionAdherence:
    return {
        "id": hash(title) % 1000,
        "day": day,
        "sport": "swimming",
        "title": title,
        "intent": intent,
        "completed": status in ("done", "substituted", "adapted_travel"),
        "status": status,  # type: ignore[typeddict-item]
        "on_planned_day": None,
        "actual_day": None,
        "substitute": None
        if substitute_id is None
        else {
            "external_id": substitute_id,
            "sport": "padel",
            "title": "Padel",
            "day": "Monday",
            "intent": "strength",
            "duration_minutes": 90,
            "avg_hr": 120,
            "training_effect_aerobic": 2.5,
        },
        "done_by": done_by,
    }


def _week(
    sessions: list[SessionAdherence], extras: list[ExtraActivity] | None = None
) -> PlanAdherence:
    return {
        "plan_id": 1,
        "plan_summary": None,
        "total_sessions": len(sessions),
        "completed_sessions": sum(s["completed"] for s in sessions),
        "substituted_sessions": 0,
        "adapted_sessions": 0,
        "excused_sessions": 0,
        "excused_days": {},
        "adherence_pct": 0.0,
        "sessions": sessions,
        "extras": extras or [],
    }


# --- Week status ----------------------------------------------------------------------


def test_an_activity_that_completes_a_session_says_which() -> None:
    week = _week(
        [
            _session("Monday", "Squat Day"),
            _session(
                "Tuesday",
                "Aerobic base swim + technique",
                "done",
                intent="aerobic_base",
                done_by="swim-1",
            ),
        ]
    )

    html = _render(week=week)

    assert "Done Completes Tue · Aerobic base swim + technique aerobic base" in _text(html)
    assert re.search(r'class="chip c-good">Done<', html)
    assert re.search(r'class="chip c-intent-aerobic_base">aerobic base<', html)


def test_an_activity_that_substitutes_a_session_says_which() -> None:
    week = _week(
        [_session("Monday", "Squat Day", "substituted", done_by="swim-1", substitute_id="swim-1")]
    )

    html = _render(week=week)

    assert "Substituted Substitutes Mon · Squat Day" in _text(html)
    assert re.search(r'class="chip c-good">Substituted<', html)


def test_a_substitute_on_a_travel_day_is_adapted() -> None:
    week = _week(
        [
            _session(
                "Monday", "Squat Day", "adapted_travel", done_by="swim-1", substitute_id="swim-1"
            )
        ]
    )

    html = _render(week=week)

    assert "Adapted (travel) Substitutes Mon · Squat Day" in _text(html)
    assert re.search(r'class="chip c-info">Adapted \(travel\)<', html)


def _extra(external_id: str) -> ExtraActivity:
    return {
        "external_id": external_id,
        "sport": "swimming",
        "title": "Pool swim",
        "day": "Thursday",
        "duration_minutes": 30,
        "intent": "recovery",
    }


def test_an_extra_activity_counts_for_training_load_only() -> None:
    week = _week([_session("Monday", "Squat Day")], extras=[_extra("swim-1")])

    html = _render(week=week)

    assert "Extra Training load only, not counted" in _text(html)
    assert re.search(r'class="chip c-muted-outline">Extra<', html)


def test_a_week_with_no_plan_has_no_status_line() -> None:
    assert 'class="week-status' not in _render(week=None)


def test_an_activity_the_completion_does_not_name_has_no_status_line() -> None:
    week = _week([_session("Monday", "Squat Day")], extras=[_extra("other")])

    assert 'class="week-status' not in _render(week=week)


# --- Life context chip ----------------------------------------------------------------


def _trip(equipment: str | None = "hotel_gym") -> Episode:
    return Episode(
        id=2,
        kind=EpisodeKind.TRAVEL,
        started_on=date(2026, 11, 17),
        ended_on=date(2026, 11, 21),
        equipment=None if equipment is None else Equipment(equipment),
    )


def _life(*episodes: Episode, ramp_back: RampBack | None = None) -> LifeContext:
    return LifeContext(today=THURSDAY, episodes=list(episodes), ramp_back=ramp_back)


def _hero(html: str) -> str:
    return html.split('class="px hero"', 1)[1].split("</td>", 1)[0]


def test_a_ramp_back_day_chips_the_verdict_with_the_day() -> None:
    ramp = RampBack(episode_id=1, day=2, length=5, load_pct=60, ends_on=date(2026, 11, 22))

    hero = _hero(_render(life=_life(ramp_back=ramp)))

    assert re.search(r'class="chip c-good">Ramp-back day 2/5<', hero)


def test_a_travel_day_chips_the_verdict_with_the_equipment() -> None:
    hero = _hero(_render(life=_life(_trip())))

    assert re.search(r'class="chip c-info">Travelling · hotel gym<', hero)


def test_a_trip_without_equipment_named_chips_just_travelling() -> None:
    hero = _hero(_render(life=_life(_trip(equipment=None))))

    assert re.search(r'class="chip c-info">Travelling<', hero)


def test_an_ordinary_day_has_no_life_context_chip() -> None:
    html = _render(life=_life())

    assert "Ramp-back" not in html
    assert "Travelling" not in html


# --- Still open this week -------------------------------------------------------------


def _open(day: str, title: str, status: str = "upcoming") -> OpenSession:
    return {
        "id": hash(title) % 1000,
        "day": day,
        "day_of_week": 0,
        "status": status,  # type: ignore[typeddict-item]
        "sport": "gym",
        "track": None,
        "title": title,
        "intent": None,
        "duration_minutes": None,
        "details": None,
        "notes": None,
    }


def _open_week(sessions: list[OpenSession], days_remaining: int) -> OpenThisWeek:
    return {"sessions": sessions, "days_remaining": days_remaining}


def _still_open(html: str) -> str:
    return html.split("Still open this week", 1)[1]


def test_still_open_this_week_closes_the_email_as_compact_chips() -> None:
    open_week = _open_week(
        [_open("Monday", "Squat Day", "overdue"), _open("Thursday", "Threshold swim")],
        days_remaining=5,
    )

    html = _render(open_this_week=open_week)

    block = _still_open(html)
    assert "2 left · 5 days" in _text(block)
    assert re.search(r'class="chip c-watch"[^>]*><b>MON</b> Squat Day · planned day passed<', block)
    assert re.search(r'class="chip c-neutral"[^>]*><b>THU</b> Threshold swim<', block)
    assert block.index("Threshold swim") < block.index("View full report")


def test_still_open_says_so_when_every_session_is_done() -> None:
    html = _render(open_this_week=_open_week([], days_remaining=3))

    assert "Every planned session is done." in _text(_still_open(html))


def test_no_plan_this_week_leaves_still_open_out() -> None:
    assert "Still open this week" not in _render(open_this_week=None)
