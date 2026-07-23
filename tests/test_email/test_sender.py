"""Tests for the email sender module."""

from unittest.mock import MagicMock, patch

from mycoach.config import Settings
from mycoach.email.sender import (
    _render_template,
    send_daily_briefing,
    send_email,
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
    """Daily briefing template renders with readiness verdict."""
    html = _render_template(
        "daily_briefing.html",
        {
            "briefing": {
                "readiness_verdict": "go_hard",
                "recovery_status": "Fully recovered",
                "sleep_assessment": "Good sleep",
                "workout_adjustments": None,
                "key_metrics": {"resting_hr": "52 bpm"},
                "sleep_recommendation": "Sleep by 10pm",
            }
        },
    )
    assert "Go Hard" in html
    assert "Fully recovered" in html
    assert "52 bpm" in html
    assert "MyCoach" in html


def test_render_weekly_plan_template() -> None:
    """Weekly plan template renders sessions."""
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
                    "details": {"bench_press": "4x8"},
                }
            ],
            "week_start": "2025-03-03",
        },
    )
    assert "A balanced week" in html
    assert "Upper Body" in html
    assert "gym" in html
    assert "2025-03-03" in html


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
    """Weekly recap template renders recap fields."""
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
            },
            "week_start": "2025-02-24",
        },
    )
    assert "Solid week" in html
    assert "Squat PR" in html
    assert "2025-02-24" in html


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
def test_send_email_resend_failure(mock_resend: MagicMock) -> None:
    """Returns False and logs when Resend raises."""
    mock_resend.Emails.send.side_effect = Exception("API error")
    settings = _make_settings(email_resend_api_key="re_test_key")
    result = send_email("user@example.com", "Subject", "<p>Hi</p>", settings)
    assert result is False


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
