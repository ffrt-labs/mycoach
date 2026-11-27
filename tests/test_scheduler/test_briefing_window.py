"""Tests for the daily-briefing retry window.

Two halves: the decision logic, which is a pure function of a
``BriefingWindow``'s fields and is tested by constructing them directly, and
``load_briefing_window``, which is tested against real ``job_runs`` rows because
the whole point of the design is that the state is nowhere else.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from mycoach.config import Settings, get_settings
from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun
from mycoach.scheduler.briefing_window import (
    ALERT_AFTER_ATTEMPTS,
    BRIEFING_ALERT_JOB,
    BRIEFING_JOB,
    MAX_FAILED_ATTEMPTS,
    BriefingWindow,
    load_briefing_window,
)
from tests.conftest import test_session

TZ = ZoneInfo("Europe/London")
DAY = date(2026, 8, 25)
OPENS_AT = time(9, 30)


def _window(
    at: time = time(10, 0),
    succeeded: bool = False,
    skipped: int = 0,
    failed: int = 0,
    last_status: str | None = None,
    last_detail: str | None = None,
    alerted: bool = False,
    alert_failures: int = 0,
) -> BriefingWindow:
    if last_status is None and (skipped or failed):
        last_status = "failed" if failed else "skipped"
    return BriefingWindow(
        now=datetime.combine(DAY, at, tzinfo=TZ),
        day=DAY,
        opens_at=OPENS_AT,
        succeeded=succeeded,
        skipped_attempts=skipped,
        failed_attempts=failed,
        last_status=last_status,
        last_detail=last_detail,
        alerted=alerted,
        alert_failures=alert_failures,
    )


class TestShouldGenerate:
    def test_generates_at_the_moment_the_window_opens(self) -> None:
        assert _window(at=OPENS_AT).should_generate is True

    def test_does_not_generate_before_the_window_opens(self) -> None:
        """The cron fires on every quarter hour; only the state gates the start."""
        assert _window(at=time(9, 15)).should_generate is False

    def test_does_not_generate_once_a_briefing_has_succeeded(self) -> None:
        """The whole reason no in-memory state is kept: this is read off job_runs."""
        assert _window(at=time(11, 0), succeeded=True, skipped=3).should_generate is False

    def test_keeps_generating_through_repeated_skips(self) -> None:
        """A skip means the data has not arrived yet, which is what retrying is for."""
        assert _window(at=time(13, 45), skipped=17).should_generate is True

    def test_stops_generating_at_the_cutoff(self) -> None:
        """A briefing about this morning's recovery delivered at 18:00 is wrong, not late."""
        assert _window(at=time(14, 0), skipped=18).should_generate is False

    def test_stops_generating_once_the_failure_budget_is_spent(self) -> None:
        """Re-running a broken pipeline every 15 minutes only reproduces the stack trace."""
        assert _window(at=time(10, 30), failed=MAX_FAILED_ATTEMPTS).should_generate is False

    def test_still_generating_one_failure_short_of_the_budget(self) -> None:
        assert _window(at=time(10, 30), failed=MAX_FAILED_ATTEMPTS - 1).should_generate is True

    def test_skips_do_not_count_against_the_failure_budget(self) -> None:
        """The two outcomes are distinguished durably; the loop must honour that."""
        assert _window(at=time(12, 0), skipped=10, failed=1).should_generate is True


class TestShouldAlert:
    def test_silent_through_the_grace_period(self) -> None:
        """The one-hour grace kills essentially every false alarm in the history."""
        assert _window(at=time(10, 15), skipped=ALERT_AFTER_ATTEMPTS - 1).should_alert is False

    def test_alerts_once_the_grace_period_has_passed(self) -> None:
        assert _window(at=time(10, 30), skipped=ALERT_AFTER_ATTEMPTS).should_alert is True

    def test_never_alerts_twice_in_a_day(self) -> None:
        assert _window(at=time(12, 0), skipped=10, alerted=True).should_alert is False

    def test_does_not_alert_when_the_briefing_landed(self) -> None:
        assert _window(at=time(11, 0), succeeded=True, skipped=6).should_alert is False

    def test_alerts_after_the_generate_cutoff(self) -> None:
        """Past 14:00 nothing more will be generated — which is exactly worth saying."""
        assert _window(at=time(15, 0), skipped=20).should_alert is True

    def test_alerts_when_the_failure_budget_is_spent(self) -> None:
        """A dead pipeline is the case the user most needs to hear about.

        The budget stops generation at three attempts — one short of the grace
        period — so keying only off the attempt count would leave exactly this
        day silent from 09:30 to midnight.
        """
        assert _window(at=time(10, 15), failed=MAX_FAILED_ATTEMPTS).should_generate is False
        assert _window(at=time(10, 15), failed=MAX_FAILED_ATTEMPTS).should_alert is True

    def test_alerts_past_the_cutoff_even_if_nothing_was_ever_attempted(self) -> None:
        """A container down all morning is silence too, and silence is the bug."""
        assert _window(at=time(14, 15)).should_alert is True

    def test_does_not_alert_before_the_cutoff_with_nothing_attempted(self) -> None:
        """Between ticks, zero attempts is just the gap — not news."""
        assert _window(at=time(9, 45)).should_alert is False

    def test_gives_up_on_an_email_backend_that_keeps_refusing(self) -> None:
        """A misconfigured backend must not become forty rejected sends a day."""
        assert (
            _window(at=time(12, 0), skipped=10, alert_failures=MAX_FAILED_ATTEMPTS).should_alert
            is False
        )

    def test_stops_evaluating_at_the_evening_cutoff(self) -> None:
        assert _window(at=time(20, 0), skipped=30).should_alert is False


class TestIsBroken:
    def test_a_skip_is_not_broken(self) -> None:
        """Absent data is the user's watch, not our pipeline."""
        assert _window(skipped=5).is_broken is False

    def test_a_failure_is_broken(self) -> None:
        """A 'check your Bluetooth' email for a Pydantic error is worse than none."""
        assert _window(failed=1).is_broken is True

    def test_reads_the_latest_attempt_not_the_tally(self) -> None:
        """A day that failed once at 09:30 and has skipped since is a data problem."""
        assert _window(skipped=4, failed=1, last_status="skipped").is_broken is False


class TestBanner:
    def test_silent_before_anything_has_been_attempted(self) -> None:
        assert _window(at=time(9, 30)).banner is None

    def test_silent_once_the_briefing_exists(self) -> None:
        assert _window(at=time(11, 0), succeeded=True, skipped=4).banner is None

    def test_names_the_wait_rather_than_blaming_the_sync(self) -> None:
        """The old text told the user to sync while the sync had in fact succeeded."""
        banner = _window(at=time(10, 30), skipped=4).banner
        assert banner is not None
        assert "Waiting on Garmin" in banner
        assert "09:30" in banner and "14:00" in banner

    def test_says_so_when_the_fault_is_ours(self) -> None:
        banner = _window(at=time(10, 0), failed=1, last_detail="hrv_status: not a float").banner
        assert banner is not None
        assert "error" in banner
        assert "hrv_status: not a float" in banner

    def test_counts_failures_not_attempts_against_the_budget(self) -> None:
        """Skips are not errors; "attempt 4 of 3" is a lie about both."""
        banner = _window(at=time(10, 30), skipped=3, failed=1, last_status="failed").banner
        assert banner is not None
        assert f"1 of {MAX_FAILED_ATTEMPTS}" in banner

    def test_says_nothing_about_a_window_that_went_by_unattempted(self) -> None:
        """With no runs recorded there is nothing to report, and a fresh
        install with no Garmin account reads identically. The email covers the
        day the container slept through; the page does not guess."""
        assert _window(at=time(15, 0)).banner is None

    def test_says_retries_have_stopped_once_the_budget_is_spent(self) -> None:
        banner = _window(at=time(11, 0), failed=MAX_FAILED_ATTEMPTS).banner
        assert banner is not None
        assert "retries have stopped" in banner

    def test_reports_the_missed_day_after_the_cutoff(self) -> None:
        banner = _window(at=time(16, 0), skipped=18).banner
        assert banner is not None
        assert "never returned" in banner


@pytest.fixture
def settings() -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "timezone": "Europe/London",
            "scheduler_timezone": "Europe/London",
            "scheduler_briefing_hour": 9,
            "scheduler_briefing_minute": 30,
        }
    )


def _run(
    job_name: str,
    status: str,
    at: datetime,
    error: str | None = None,
    skip_reason: str | None = None,
) -> JobRun:
    return JobRun(
        job_name=job_name,
        started_at=at,
        duration_ms=1000,
        status=status,
        error=error,
        skip_reason=skip_reason,
    )


class TestLoadBriefingWindow:
    """The state is a query, never a variable — that is the hard requirement."""

    async def test_empty_day_has_nothing_attempted(self, settings: Settings) -> None:
        async with test_session() as session:
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(9, 30), tzinfo=TZ), settings=settings
            )
        assert window.attempts == 0
        assert window.succeeded is False
        assert window.last_status is None

    async def test_counts_skips_and_failures_separately(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add_all(
                [
                    _run(
                        BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30), skip_reason="no data"
                    ),
                    _run(BRIEFING_JOB, "failed", datetime(2026, 8, 25, 8, 45), error="boom"),
                    _run(
                        BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 9, 0), skip_reason="no data"
                    ),
                ]
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(10, 30), tzinfo=TZ), settings=settings
            )

        assert window.skipped_attempts == 2
        assert window.failed_attempts == 1
        assert window.attempts == 3
        assert window.last_status == "skipped"
        assert window.last_detail == "no data"

    async def test_reads_the_error_of_a_failed_last_attempt(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add(
                _run(BRIEFING_JOB, "failed", datetime(2026, 8, 25, 8, 45), error="hrv_status")
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(10, 30), tzinfo=TZ), settings=settings
            )

        assert window.last_detail == "hrv_status"
        assert window.is_broken is True

    async def test_a_success_today_closes_the_window(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add_all(
                [
                    _run(BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30)),
                    _run(BRIEFING_JOB, "success", datetime(2026, 8, 25, 9, 15)),
                ]
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(10, 30), tzinfo=TZ), settings=settings
            )

        assert window.succeeded is True
        assert window.should_generate is False

    async def test_a_recorded_alert_run_is_the_once_per_day_lock(self, settings: Settings) -> None:
        """No 'have I emailed yet' flag exists; the alert's own run row is it."""
        async with test_session() as session:
            session.add_all(
                [
                    _run(BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30 + i))
                    for i in range(0, ALERT_AFTER_ATTEMPTS)
                ]
            )
            session.add(_run(BRIEFING_ALERT_JOB, "success", datetime(2026, 8, 25, 9, 30)))
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(11, 0), tzinfo=TZ), settings=settings
            )

        assert window.alerted is True
        assert window.should_alert is False

    async def test_an_alert_that_failed_to_send_is_retried(self, settings: Settings) -> None:
        """Otherwise a rejected send would silence the day it was meant to report."""
        async with test_session() as session:
            session.add_all(
                [
                    _run(BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30 + i))
                    for i in range(0, ALERT_AFTER_ATTEMPTS)
                ]
            )
            session.add(_run(BRIEFING_ALERT_JOB, "failed", datetime(2026, 8, 25, 9, 30)))
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(11, 0), tzinfo=TZ), settings=settings
            )

        assert window.alerted is False
        assert window.alert_failures == 1
        assert window.should_alert is True

    async def test_a_skipped_alert_settles_the_day(self, settings: Settings) -> None:
        """Email switched off means there was deliberately nothing to send.

        Retrying that on every tick would write ~40 rows a day into the table
        the whole decision is read from.
        """
        async with test_session() as session:
            session.add_all(
                [
                    _run(BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30 + i))
                    for i in range(0, ALERT_AFTER_ATTEMPTS)
                ]
            )
            session.add(
                _run(
                    BRIEFING_ALERT_JOB,
                    "skipped",
                    datetime(2026, 8, 25, 9, 30),
                    skip_reason="daily briefing email is switched off",
                )
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(11, 0), tzinfo=TZ), settings=settings
            )

        assert window.alerted is True
        assert window.should_alert is False

    async def test_a_manually_generated_briefing_stands_the_loop_down(
        self, settings: Settings
    ) -> None:
        """The button and the loop must not disagree about whether today is done.

        Otherwise a briefing generated by hand at 10:00 would leave the poll
        re-syncing Garmin and recording a duplicate skip every 15 minutes until
        the cutoff.
        """
        async with test_session() as session:
            session.add(
                CoachingInsight(
                    user_id=1,
                    insight_date=DAY,
                    insight_type="daily_briefing",
                    content='{"readiness_verdict": "moderate"}',
                )
            )
            session.add(_run(BRIEFING_JOB, "skipped", datetime(2026, 8, 25, 8, 30)))
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(10, 30), tzinfo=TZ), settings=settings
            )

        assert window.succeeded is True
        assert window.should_generate is False
        assert window.should_alert is False

    async def test_yesterdays_briefing_does_not_stand_today_down(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add(
                CoachingInsight(
                    user_id=1,
                    insight_date=DAY - timedelta(days=1),
                    insight_type="daily_briefing",
                    content="{}",
                )
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(9, 30), tzinfo=TZ), settings=settings
            )

        assert window.succeeded is False

    async def test_another_insight_type_does_not_stand_today_down(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add(
                CoachingInsight(
                    user_id=1,
                    insight_date=DAY,
                    insight_type="weekly_recap",
                    content="{}",
                )
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(9, 30), tzinfo=TZ), settings=settings
            )

        assert window.succeeded is False

    async def test_yesterdays_runs_do_not_count(self, settings: Settings) -> None:
        """Otherwise a bad Monday would suppress Tuesday's briefing entirely."""
        async with test_session() as session:
            session.add_all(
                [
                    _run(BRIEFING_JOB, "success", datetime(2026, 8, 24, 8, 30)),
                    _run(BRIEFING_JOB, "failed", datetime(2026, 8, 24, 9, 0)),
                ]
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(9, 30), tzinfo=TZ), settings=settings
            )

        assert window.succeeded is False
        assert window.attempts == 0

    async def test_other_jobs_runs_do_not_count(self, settings: Settings) -> None:
        async with test_session() as session:
            session.add_all(
                [
                    _run("garmin_sync", "success", datetime(2026, 8, 25, 8, 30)),
                    _run("weekly_recap", "failed", datetime(2026, 8, 25, 8, 40)),
                ]
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=datetime.combine(DAY, time(10, 30), tzinfo=TZ), settings=settings
            )

        assert window.attempts == 0
        assert window.succeeded is False

    async def test_the_local_day_bounds_are_converted_to_utc(self) -> None:
        """``started_at`` is naive UTC; the day is local. Getting this wrong loses runs.

        In a UTC+11 zone, 09:30 local on the 25th is 22:30 UTC on the *24th* —
        a run recorded then must still count as today's.
        """
        tz = ZoneInfo("Australia/Sydney")
        settings = get_settings().model_copy(
            update={"timezone": "Australia/Sydney", "scheduler_timezone": "Australia/Sydney"}
        )
        async with test_session() as session:
            local_930 = datetime.combine(DAY, time(9, 30), tzinfo=tz)
            session.add(
                _run(
                    BRIEFING_JOB,
                    "skipped",
                    local_930.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
                )
            )
            await session.commit()
            window = await load_briefing_window(
                session, now=local_930 + timedelta(minutes=15), settings=settings
            )

        assert window.attempts == 1
