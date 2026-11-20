"""Tests for the email sender module."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from mycoach.config import Settings
from mycoach.email.sender import (
    EmailSendError,
    _render_template,
    send_briefing_unavailable,
    send_daily_briefing,
    send_email,
    send_garmin_auth_failed,
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
    """Daily briefing template renders the code's verdict and the coach's lead."""
    html = _render_template(
        "daily_briefing.html",
        {
            "headline": {
                "verdict": "Go hard",
                "chip": None,
                "summary": "Fully recovered",
            }
        },
    )
    assert "Go hard" in html
    assert "Fully recovered" in html
    assert "MyCoach" in html




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
        None,
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
    send_weekly_plan(summary="Good week", sessions=[], week_start="2025-03-03", settings=settings)
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
    send_daily_briefing(None, settings=settings)
    html = mock_send.call_args[0][2]
    assert 'href="https://coach.example.com/"' in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_plan_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_weekly_plan's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_weekly_plan(summary="Good week", sessions=[], week_start="2025-03-03", settings=settings)
    html = mock_send.call_args[0][2]
    assert 'href="https://coach.example.com/"' in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_post_workout_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_post_workout's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_post_workout(
        content={"performance_summary": "Great"}, activity_title="Leg Day", settings=settings
    )
    html = mock_send.call_args[0][2]
    assert 'href="https://coach.example.com/"' in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_weekly_recap_wires_dashboard_url(mock_send: MagicMock) -> None:
    """send_weekly_recap's CTA points at settings.app_base_url, not a dead '#' link."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_weekly_recap(content={"week_summary": "Solid"}, week_start="2025-02-24", settings=settings)
    html = mock_send.call_args[0][2]
    assert 'href="https://coach.example.com/"' in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_send_garmin_auth_failed_cta_opens_the_dashboard(mock_send: MagicMock) -> None:
    """The dashboard is served at '/', so the CTA must not point at a dead /dashboard path."""
    settings = _make_settings(app_base_url="https://coach.example.com")
    send_garmin_auth_failed("token expired", settings=settings)
    html = mock_send.call_args[0][2]
    assert 'href="https://coach.example.com/"' in html
    assert "/dashboard" not in html


# --- Briefing-unavailable alert ---
#
# The whole point of this email is that it names something the reader can act
# on, and that it names the *right* thing: a "check your Bluetooth" message for
# what was actually a validation error inside our own pipeline is worse than no
# email at all.


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_names_the_real_gap(mock_send: MagicMock) -> None:
    settings = _make_settings(timezone="Europe/London")
    result = send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=True,
        last_upload=datetime(2026, 8, 19, 4, 20, tzinfo=UTC),
        device_name="Forerunner 255 Music",
        settings=settings,
    )
    assert result is True
    html = mock_send.call_args[0][2]
    assert "hasn&#39;t uploaded to Garmin since Aug 19 at 05:20" in html
    assert "Bluetooth" in html
    assert "Forerunner 255 Music" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_renders_the_upload_time_in_the_users_timezone(
    mock_send: MagicMock,
) -> None:
    """05:20 BST, not 04:20 UTC — the reader compares it against their morning."""
    settings = _make_settings(timezone="Europe/London")
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=True,
        last_upload=datetime(2026, 8, 19, 4, 20, tzinfo=UTC),
        settings=settings,
    )
    assert "Aug 19 at 05:20" in mock_send.call_args[0][2]


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_counts_errors_not_skips(mock_send: MagicMock) -> None:
    """Four data-less skips plus one real error is one error, not five.

    Reporting five would send the reader hunting for a fault that isn't there.
    """
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=1,
        is_broken=True,
        can_still_retry=True,
        settings=_make_settings(),
    )
    html = mock_send.call_args[0][2]
    assert "1 attempt hit an error" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_quotes_no_count_when_the_watch_is_at_fault(
    mock_send: MagicMock,
) -> None:
    """How many times we asked is our business; when the watch last synced is theirs."""
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=True,
        settings=_make_settings(),
    )
    html = mock_send.call_args[0][2]
    assert "0 attempts" not in html
    assert "hit an error" not in html
    assert "Bluetooth" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_does_not_blame_the_watch_for_our_own_fault(
    mock_send: MagicMock,
) -> None:
    settings = _make_settings()
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=3,
        is_broken=True,
        can_still_retry=False,
        detail="hrv_status: Input should be a valid number",
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "Bluetooth" not in html
    assert "fault on our side" in html
    assert "hrv_status" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_says_more_attempts_are_coming(mock_send: MagicMock) -> None:
    settings = _make_settings()
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=True,
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "every 15 minutes" in html
    assert "14:00" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_says_when_the_day_is_over(mock_send: MagicMock) -> None:
    settings = _make_settings()
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=False,
        settings=settings,
    )
    html = mock_send.call_args[0][2]
    assert "cutoff has passed" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_briefing_alert_works_without_an_upload_time(mock_send: MagicMock) -> None:
    """Garmin may not say; the alert still has to go."""
    settings = _make_settings()
    result = send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=0,
        is_broken=False,
        can_still_retry=True,
        settings=settings,
    )
    assert result is True
    html = mock_send.call_args[0][2]
    assert "Last upload" not in html
    assert "Bluetooth" in html


# --- HRV line (#197): rendered by code from the DB, never by the LLM ---

_HRV_WITH_BAND = {
    "hrv_status": 48.0,
    "hrv_7day_avg": 52.0,
    "hrv_status_text": "BALANCED",
    "hrv_baseline_low": 47.0,
    "hrv_baseline_high": 58.0,
    "hrv_baseline_low_upper": 43.0,
}


@patch("mycoach.email.sender.send_email", return_value=True)
def test_daily_briefing_shows_the_hrv_7_day_avg_against_the_band(mock_send: MagicMock) -> None:
    send_daily_briefing(
        None, settings=_make_settings(), hrv=_HRV_WITH_BAND
    )
    html = mock_send.call_args[0][2]
    assert "HRV 52 ms 7-day avg · your range 47–58 · Balanced · last night 48 ms" in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_daily_briefing_says_building_baseline_before_garmin_has_a_band(
    mock_send: MagicMock,
) -> None:
    send_daily_briefing(
        None,
        settings=_make_settings(),
        hrv={"hrv_status": 48.0, "hrv_7day_avg": 52.0},
    )
    html = mock_send.call_args[0][2]
    assert "HRV 52 ms 7-day avg · Building baseline · last night 48 ms" in html
    assert "your range" not in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_daily_briefing_has_no_hrv_line_without_hrv_data(mock_send: MagicMock) -> None:
    send_daily_briefing(None, settings=_make_settings(), hrv={})
    html = mock_send.call_args[0][2]
    assert "7-day avg" not in html
    assert "Building baseline" not in html


@patch("mycoach.email.sender.send_email", return_value=True)
def test_no_briefing_email_still_shows_the_hrv_line(mock_send: MagicMock) -> None:
    """The LLM failed, but the numbers come from the DB, so they still reach the athlete."""
    send_briefing_unavailable(
        day="Tuesday, August 25",
        failures=2,
        is_broken=True,
        can_still_retry=False,
        settings=_make_settings(),
        hrv=_HRV_WITH_BAND,
    )
    html = mock_send.call_args[0][2]
    assert "HRV 52 ms 7-day avg · your range 47–58 · Balanced · last night 48 ms" in html
