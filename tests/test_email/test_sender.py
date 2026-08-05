"""Tests for the email sender module."""

from unittest.mock import MagicMock, patch

import pytest

from mycoach.config import Settings
from mycoach.email.sender import (
    EmailSendError,
    _format_key_metrics,
    _format_session_details,
    _render_template,
    send_daily_briefing,
    send_email,
    send_no_availability,
    send_post_workout,
    send_weekly_plan,
    send_weekly_recap,
)


def _make_settings(**overrides: object) -> Settings:
    defaults = {
        "email_enabled": True,
        "email_from": "coach@example.com",
        "email_to": "user@example.com",
        "email_resend_api_key": "re_test_key",
        "email_smtp_host": "",
        "env": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# --- Template rendering ---


def test_render_daily_briefing_template() -> None:
    """Daily briefing template renders readiness verdict and formatted metrics."""
    html = _render_template(
        "daily_briefing.html",
        {
            "briefing": {
                "readiness_verdict": "go_hard",
                "recovery_status": "Fully recovered",
                "readiness_explanation": "HRV and sleep both trended up this week",
                "sleep_assessment": "Good sleep",
                "workout_adjustments": None,
                "key_metrics": {"Resting HR": "52 bpm"},
                "sleep_recommendation": "Sleep by 10pm",
            }
        },
    )
    assert "Go Hard" in html
    assert "Fully recovered" in html
    assert "Resting HR" in html
    assert "52" in html
    assert "bpm" in html
    assert "MyCoach" in html


def test_format_key_metrics_maps_labels_and_skips_none() -> None:
    """_format_key_metrics turns raw field names into display rows, dropping absent values."""
    rows = _format_key_metrics(
        {
            "body_battery": 65,
            "hrv_status": None,
            "sleep_score": 78,
            "training_readiness": None,
            "resting_hr": 52,
        }
    )
    assert rows == {"Body Battery": "65", "Sleep Score": "78", "Resting HR": "52 bpm"}
    # Display order is the order emails show, so it must survive the mapping.
    assert list(rows) == ["Body Battery", "Sleep Score", "Resting HR"]


def test_format_session_details_gym_exercises() -> None:
    """Gym details flatten to one row per exercise with sets/reps/weight/RPE."""
    rows = _format_session_details(
        {
            "exercises": [
                {"name": "Bench Press", "sets": 4, "reps": 8, "target_weight_kg": 60, "rpe": 8},
                {"name": "Pull-ups", "sets": 3, "reps": 10},
            ]
        }
    )
    assert rows == {"Bench Press": "4×8 @ 60kg · RPE 8", "Pull-ups": "3×10"}


def test_format_session_details_cardio_humanises_keys() -> None:
    """Cardio details get readable labels, with units where the key is known."""
    rows = _format_session_details(
        {"target_pace_min_per_km": 5.5, "hr_zone": 2, "warmup_minutes": 10, "notes": None}
    )
    assert rows == {"Target Pace": "5.5 min/km", "HR Zone": "2", "Warmup Minutes": "10"}


def test_format_session_details_empty() -> None:
    """Missing or malformed details produce no rows, so the template hides the table."""
    assert _format_session_details(None) == {}
    assert _format_session_details({}) == {}
    assert _format_session_details({"exercises": []}) == {}


def test_render_weekly_plan_template() -> None:
    """Weekly plan template renders sessions and a gym exercise table."""
    html = _render_template(
        "weekly_plan.html",
        {
            "summary": "A balanced week",
            "sessions": [
                {
                    "day_name": "Monday",
                    "title": "Upper Body",
                    "sport": "gym",
                    "duration_minutes": 60,
                    "notes": "Focus on bench",
                    "details": {"Bench Press": "4×8 @ 60kg · RPE 8"},
                }
            ],
            "week_start": "2025-03-03",
        },
    )
    assert "A balanced week" in html
    assert "Upper Body" in html
    assert "gym" in html
    assert "2025-03-03" in html
    assert "Bench Press" in html
    assert "4" in html and "8" in html
    assert "60kg" in html
    assert "RPE 8" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_formats_raw_cardio_details(mock_send: MagicMock) -> None:
    """send_weekly_plan humanises raw detail keys before they reach the template."""
    send_weekly_plan(
        summary="Easy week",
        sessions=[
            {
                "day_name": "Tuesday",
                "title": "Easy Run",
                "sport": "running",
                "duration_minutes": 40,
                "notes": "Keep it conversational pace",
                "details": {"target_pace_min_per_km": 5.5, "hr_zone": 2},
            }
        ],
        week_start="2025-03-03",
        settings=_make_settings(),
    )
    html = mock_send.call_args[0][2]
    assert "Keep it conversational pace" in html
    assert "Target Pace" in html
    assert "5.5 min/km" in html
    assert "target_pace_min_per_km" not in html


def test_render_post_workout_template() -> None:
    """Post-workout template renders analysis fields."""
    html = _render_template(
        "post_workout.html",
        {
            "analysis": {
                "performance_summary": "Great session",
                "planned_vs_actual": "On track",
                "hr_analysis": "Zone 3 dominant",
                "performance_trends": "Improving",
                "key_highlights": ["PR on squat", "Good form"],
                "areas_for_improvement": ["Rest times"],
                "next_session_recommendations": "Increase weight",
                "recovery_notes": "Take it easy tomorrow",
                "training_effect_assessment": None,
            },
            "activity_title": "Leg Day",
        },
    )
    assert "Great session" in html
    assert "PR on squat" in html
    assert "Leg Day" in html


def test_render_weekly_recap_template() -> None:
    """Weekly recap template renders recap fields, including coach recommendations."""
    html = _render_template(
        "weekly_recap.html",
        {
            "recap": {
                "week_summary": "Solid week",
                "adherence_analysis": "80% adherence",
                "performance_highlights": ["Squat PR", "Consistent cardio"],
                "areas_of_concern": ["Missed Friday"],
                "recovery_assessment": "Well recovered",
                "training_load_analysis": "Moderate load",
                "next_week_recommendations": "Push harder",
                "mesocycle_progress": "Week 3 of 4",
                "coach_recommendations": ["Add a third rest day", "Increase protein intake"],
            },
            "week_start": "2025-02-24",
        },
    )
    assert "Solid week" in html
    assert "Squat PR" in html
    assert "2025-02-24" in html
    assert "Add a third rest day" in html
    assert "Increase protein intake" in html


# --- send_email function ---


def test_send_email_disabled() -> None:
    """Email is not sent when email_enabled is False."""
    settings = _make_settings(email_enabled=False)
    result = send_email("user@example.com", "Subject", "<p>Hi</p>", settings)
    assert result is False


def test_send_email_no_backend() -> None:
    """send_email stays safe even if it somehow sees enabled-but-backendless config.

    Startup validation now makes this state unconstructable via ``Settings(...)``
    (see tests/test_config.py), so ``model_construct`` is used to bypass the
    validator and exercise ``send_email``'s defensive fallback branch directly.
    """
    settings = Settings.model_construct(
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="",
        email_smtp_host="",
        env="test",
    )
    result = send_email("user@example.com", "Subject", "<p>Hi</p>", settings)
    assert result is False


@patch("mycoach.email.sender.resend")
def test_send_email_via_resend(mock_resend: MagicMock) -> None:
    """Sends via Resend when API key is configured."""
    mock_resend.Emails.send = MagicMock()
    settings = _make_settings(email_resend_api_key="re_test_key")
    result = send_email("user@example.com", "Subject", "<p>Hi</p>", settings)
    assert result is True
    mock_resend.Emails.send.assert_called_once()
    call_args = mock_resend.Emails.send.call_args[0][0]
    assert call_args["to"] == ["user@example.com"]
    assert call_args["subject"] == "Subject"


@patch("mycoach.email.sender.smtplib.SMTP")
def test_send_email_via_smtp(mock_smtp_class: MagicMock) -> None:
    """Sends via SMTP when SMTP host is configured (no Resend key)."""
    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)
    settings = _make_settings(
        email_resend_api_key="",
        email_smtp_host="smtp.example.com",
        email_smtp_port=587,
        email_smtp_user="user",
        email_smtp_password="pass",
    )
    result = send_email("user@example.com", "Subject", "<p>Hi</p>", settings)
    assert result is True


@patch("mycoach.email.sender.resend")
def test_send_email_resend_failure_carries_the_backend_reason(mock_resend: MagicMock) -> None:
    """A Resend rejection raises with the backend's own words, not a bare False.

    The reason is the only thing that says *why* the send failed; collapsing it
    to False leaves whoever reads the failed run guessing.
    """
    mock_resend.Emails.send.side_effect = Exception("domain example.com is not verified")
    settings = _make_settings(email_resend_api_key="re_test_key")

    with pytest.raises(EmailSendError) as excinfo:
        send_email("user@example.com", "Subject", "<p>Hi</p>", settings)

    message = str(excinfo.value)
    assert "resend" in message.lower()
    assert "domain example.com is not verified" in message


@patch("mycoach.email.sender.smtplib.SMTP")
def test_send_email_smtp_failure_carries_the_backend_reason(mock_smtp_class: MagicMock) -> None:
    """An SMTP rejection raises with the server's own words, like the Resend path."""
    mock_smtp_class.side_effect = OSError("connection refused")
    settings = _make_settings(email_resend_api_key="", email_smtp_host="smtp.example.com")

    with pytest.raises(EmailSendError) as excinfo:
        send_email("user@example.com", "Subject", "<p>Hi</p>", settings)

    message = str(excinfo.value)
    assert "smtp" in message.lower()
    assert "connection refused" in message


# --- Convenience sender functions ---


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_daily_briefing_calls_send_email(mock_send: MagicMock) -> None:
    """send_daily_briefing renders template and calls send_email."""
    settings = _make_settings()
    result = send_daily_briefing(
        {"readiness_verdict": "moderate", "recovery_status": "OK", "sleep_assessment": "Fine"},
        settings=settings,
    )
    assert result is True
    mock_send.assert_called_once()
    assert "Daily Briefing" in mock_send.call_args[0][1]  # subject


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_calls_send_email(mock_send: MagicMock) -> None:
    """send_weekly_plan renders template and calls send_email."""
    settings = _make_settings()
    result = send_weekly_plan(
        summary="Good week",
        sessions=[{"day_name": "Mon", "title": "Gym", "sport": "gym"}],
        week_start="2025-03-03",
        settings=settings,
    )
    assert result is True
    mock_send.assert_called_once()


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_includes_default_line_when_planned_from_default(
    mock_send: MagicMock,
) -> None:
    """The 'planned from your standing schedule' line shows when availability_source is default."""
    settings = _make_settings()
    send_weekly_plan(
        summary="Good week",
        sessions=[],
        week_start="2025-03-03",
        availability_source="default",
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "standing schedule" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_omits_default_line_when_declared(mock_send: MagicMock) -> None:
    """The default-schedule line is omitted when the week was declared by the user."""
    settings = _make_settings()
    send_weekly_plan(
        summary="Good week",
        sessions=[],
        week_start="2025-03-03",
        availability_source="declared",
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "standing schedule" not in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_omits_default_line_when_source_unknown(mock_send: MagicMock) -> None:
    """Plans generated before availability_source existed (None) don't show the line."""
    settings = _make_settings()
    send_weekly_plan(
        summary="Good week", sessions=[], week_start="2025-03-03", settings=settings
    )
    html = mock_send.call_args[0][2]
    assert "standing schedule" not in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_no_availability_names_the_week_and_links_default_schedule(
    mock_send: MagicMock,
) -> None:
    """The no-availability email names the week and links straight to the default tab."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    result = send_no_availability(week_start="2025-03-03", settings=settings)
    assert result is True
    html = mock_send.call_args[0][2]
    subject = mock_send.call_args[0][1]
    assert "No Plan Generated" in subject
    assert "2025-03-03" in html
    assert "https://coach.example.com/availability?week=default" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_post_workout_calls_send_email(mock_send: MagicMock) -> None:
    """send_post_workout renders template and calls send_email."""
    settings = _make_settings()
    result = send_post_workout(
        content={"performance_summary": "Great"},
        activity_title="Leg Day",
        settings=settings,
    )
    assert result is True
    assert "Leg Day" in mock_send.call_args[0][1]


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_recap_calls_send_email(mock_send: MagicMock) -> None:
    """send_weekly_recap renders template and calls send_email."""
    settings = _make_settings()
    result = send_weekly_recap(
        content={"week_summary": "Solid"},
        week_start="2025-02-24",
        settings=settings,
    )
    assert result is True


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_daily_briefing_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_daily_briefing's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_daily_briefing({"readiness_verdict": "moderate"}, settings=settings)
    html = mock_send.call_args[0][2]
    assert "https://coach.example.com/dashboard" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_daily_briefing_formats_raw_key_metrics(mock_send: MagicMock) -> None:
    """send_daily_briefing formats the raw Pydantic-shaped key_metrics before rendering."""
    settings = _make_settings()
    send_daily_briefing(
        {"readiness_verdict": "moderate", "key_metrics": {"resting_hr": 52, "hrv_status": None}},
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "Resting HR" in html
    assert "52" in html
    assert "bpm" in html
    assert "HRV Status" not in html  # None values are skipped


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_weekly_plan's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_weekly_plan(summary="Good week", sessions=[], week_start="2025-03-03", settings=settings)
    html = mock_send.call_args[0][2]
    assert "https://coach.example.com/dashboard" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_post_workout_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_post_workout's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_post_workout(
        content={"performance_summary": "Great"}, activity_title="Leg Day", settings=settings
    )
    html = mock_send.call_args[0][2]
    assert "https://coach.example.com/dashboard" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_recap_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_weekly_recap's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_weekly_recap(content={"week_summary": "Solid"}, week_start="2025-02-24", settings=settings)
    html = mock_send.call_args[0][2]
    assert "https://coach.example.com/dashboard" in html
