"""Scheduled job functions for the daily coaching pipeline.

Each job creates its own async DB session, runs the relevant pipeline step,
and logs results. Jobs are designed to be idempotent — they skip gracefully
if the output already exists for the current day/week.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial

from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import (
    InsufficientHealthData,
    NoAvailabilityConfigured,
    PipelineSkip,
)
from mycoach.config import get_settings
from mycoach.database import async_session
from mycoach.email.sender import (
    EmailSendError,
    send_briefing_unavailable,
    send_daily_briefing,
    send_no_availability,
    send_post_workout,
    send_weekly_plan,
    send_weekly_recap,
)
from mycoach.models.activity import Activity
from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun
from mycoach.models.plan import PlannedSession
from mycoach.models.user import User
from mycoach.scheduler.briefing_window import (
    BRIEFING_ALERT_JOB,
    BRIEFING_JOB,
    GENERATE_CUTOFF,
    BriefingWindow,
    load_briefing_window,
)
from mycoach.sources.base import ImportResult
from mycoach.sources.garmin.source import DeviceUpload, GarminSource
from mycoach.sources.merger import MergeResult

logger = logging.getLogger(__name__)

USER_ID = 1  # Single-user MVP

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from a sync APScheduler job."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class EmailDeliveryError(RuntimeError):
    """An email a job meant to send did not go out.

    Raised by ``_deliver`` so an undelivered email reaches ``_record_run`` as an
    ordinary failure — jobs previously discarded the send outcome, logging
    "email sent" for a send that never happened. The message names the email and
    the cause, because ``JobRun.error`` is all a reader gets.
    """


@dataclass
class _Delivery:
    """Per-run tally of whether any email actually went out."""

    delivered: bool = False


# Set by ``_record_run`` for the duration of one job body, so ``_deliver`` can
# note a successful send from wherever in the body it happens without every job
# threading the fact back through both its return value and its exceptions. That
# matters for the post-workout batch, which can deliver one email and still fail
# the run on another — the exception would otherwise lose the delivery.
_current_delivery: ContextVar[_Delivery | None] = ContextVar(
    "job_email_delivery", default=None
)


def _deliver(send: Callable[[], bool], email_label: str) -> None:
    """Attempt one email send, recording the outcome and failing if nothing went out.

    ``send`` is the zero-argument send call. Either way it fails to deliver is a
    real failure rather than something to log over, and the two ways differ in
    what a reader has to do about them: an ``EmailSendError`` is a backend that
    refused and said why, while a False return is no backend having been
    available to try — a configuration fault. Both are re-raised naming the
    email and the cause, since the run's error column is the only durable
    account. A True return is noted on the run as delivery having happened.

    The "email sent" log line lives here rather than at the call sites so it can
    only ever follow a send that actually reported success — the bug this helper
    exists to prevent was exactly that line being logged unconditionally.
    """
    try:
        sent = send()
    except EmailSendError as e:
        raise EmailDeliveryError(f"{email_label} email send failed — {e}") from e
    if not sent:
        raise EmailDeliveryError(
            f"{email_label} email send failed — no email backend was available to "
            f"attempt it (check MYCOACH_EMAIL_ENABLED and the Resend/SMTP settings)"
        )
    delivery = _current_delivery.get()
    if delivery is not None:
        delivery.delivered = True
    logger.info("Scheduler: %s email sent", email_label)


async def _record_run(job_name: str, coro) -> None:  # type: ignore[no-untyped-def]
    """Run a job body, timing it and recording its outcome durably.

    Writes one append-only ``JobRun`` row — job identifier, start time,
    duration, status (``success`` / ``skipped`` / ``failed``), error detail on
    failure, skip reason on skip, and whether an email was delivered — and
    emits a structured log line carrying the same facts except the skip
    reason, which the log line already carries inline. A ``PipelineSkip`` is a
    deliberate no-op (``skipped``); every other exception is a real failure
    (``failed``). Exceptions never propagate out to the scheduler.

    Delivery is read off the run's ``_Delivery`` tally rather than the body's
    return value, so it is recorded however the body exited — including a batch
    that delivered one email and then failed on another.
    """
    started_at = datetime.utcnow()
    start = time.perf_counter()
    status = "success"
    detail: str | None = None  # skip reason or failure message, for the log line
    failure: Exception | None = None
    delivery = _Delivery()
    token = _current_delivery.set(delivery)
    try:
        await coro
    except PipelineSkip as e:
        status = "skipped"
        detail = str(e)
    except Exception as e:  # noqa: BLE001 - outcome is recorded, not re-raised
        status = "failed"
        detail = str(e)
        failure = e
    finally:
        _current_delivery.reset(token)
    duration_ms = int((time.perf_counter() - start) * 1000)

    # The row's error column is failure detail only, per the spec; a skip's
    # reason is persisted separately in skip_reason instead.
    error = detail if status == "failed" else None
    skip_reason = detail if status == "skipped" else None

    async with async_session() as session:
        session.add(
            JobRun(
                job_name=job_name,
                started_at=started_at,
                duration_ms=duration_ms,
                status=status,
                error=error,
                skip_reason=skip_reason,
                email_delivered=delivery.delivered,
            )
        )
        await session.commit()

    extra = {
        "job_name": job_name,
        "job_status": status,
        "job_error": error,
        "duration_ms": duration_ms,
        "email_delivered": delivery.delivered,
    }
    if status == "failed":
        logger.error("Scheduler: %s failed", job_name, exc_info=failure, extra=extra)
    elif status == "skipped":
        logger.info("Scheduler: %s skipped — %s", job_name, detail, extra=extra)
    else:
        logger.info("Scheduler: %s succeeded", job_name, extra=extra)


def _run_recorded_job(job_name: str, coro) -> None:  # type: ignore[no-untyped-def]
    """Sync entry point for a recorded job — the single call site per job."""
    _run_async(_record_run(job_name, coro))


async def _get_user_email_pref(pref_field: str) -> bool:
    """Check if user has a specific email preference enabled."""
    settings = get_settings()
    if not settings.email_enabled:
        return False
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == USER_ID))
        user = result.scalar_one_or_none()
        if user is None:
            return False
        return bool(getattr(user, pref_field, False))


def job_garmin_sync() -> None:
    """Sync health and activity data from Garmin Connect."""
    logger.info("Scheduler: starting Garmin sync")
    _run_recorded_job("garmin_sync", _garmin_sync())


async def _garmin_sync(days: int | None = None) -> ImportResult:
    """Fetch and import a window of Garmin data, returning what was imported.

    The window is wider than "since the last run" on purpose: Garmin uploads
    can arrive a day or more late, and ``import_health_snapshot`` fills nulls
    on re-fetch, so re-asking for a day we already have can only improve it.
    """
    if days is None:
        days = get_settings().scheduler_sync_lookback_days

    source = GarminSource()
    if not await source.authenticate():
        raise RuntimeError("Garmin authentication failed")

    async with async_session() as session:
        since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        since = since - timedelta(days=days)
        result = await source.fetch_and_import(session, USER_ID, since=since)
        # Merging is disabled ahead of the #109 canonical-activity migration:
        # merge_garmin_hevy's delete-and-overwrite pattern destroys provenance
        # (see #116/#110).
        merge_result = MergeResult()
        await session.commit()
        logger.info(
            "Scheduler: Garmin sync complete — health=%d, activities=%d, merged=%d",
            result.health_snapshots_created,
            result.activities_created,
            merge_result.merged,
        )
        # ``fetch_and_import`` already worked out which endpoints failed and
        # which days came back empty; logging only the three counts threw that
        # away, and re-deriving it later cost four ad-hoc probe scripts. A
        # counts-only line cannot distinguish "Garmin returned nulls" from
        # "every call raised", and those need opposite responses.
        for error in result.errors or []:
            logger.warning("Scheduler: Garmin sync detail — %s", error)
        return result


def job_daily_briefing_poll() -> None:
    """One tick of the daily-briefing retry loop.

    Registered on a 15-minute cron across the whole window rather than as a
    single 09:30 entry. Whether a given tick does anything is decided from
    ``job_runs``, not from the trigger, so a container restart mid-window
    neither duplicates a briefing nor loses the day — see
    ``mycoach.scheduler.briefing_window``.
    """
    _run_async(_daily_briefing_poll())


async def _daily_briefing_poll() -> None:
    """Decide from today's runs whether to generate, alert, both, or neither.

    Deliberately *not* wrapped in ``_record_run``: a tick that decides to do
    nothing must leave no trace, or the 15-minute poll would write ~44 rows a
    day into the very table the decision is read from, and every attempt count
    it derives would be nonsense. Only real work — an attempt, or an alert —
    records a run.
    """
    async with async_session() as session:
        window = await load_briefing_window(session)

    if window.should_generate:
        logger.info(
            "Scheduler: daily briefing attempt %d for %s",
            window.attempts + 1,
            window.day,
        )
        await _record_run(BRIEFING_JOB, _daily_briefing(window.day))
        # Re-read rather than reason forward from the old state: the attempt
        # just made is exactly the one that decides whether an alert is due,
        # and its outcome is only knowable from the row it wrote.
        async with async_session() as session:
            window = await load_briefing_window(session)

    if window.should_alert:
        await _record_run(BRIEFING_ALERT_JOB, _briefing_alert(window))


async def _daily_briefing(today: date | None = None) -> None:
    """Sync fresh Garmin data, then generate the briefing from it.

    The sync is part of the job rather than a separate cron entry because the
    coupling is real: a briefing about last night's sleep is meaningless
    without last night's sleep, and the standalone 06:00 sync runs before
    Garmin has finalised it. Encoding that in the job beats spacing two crons
    and hoping.

    ``today`` is the local day the poll decided to brief on, so every attempt
    in a window targets the same day even if one straddles midnight. Today only
    — an older day is never backfilled.

    The empty-data guard is *not* here. It lives in
    ``CoachingEngine.generate_daily_briefing`` so this job, the retry loop and
    the dashboard button all get the same answer; this body only decorates the
    resulting skip with what the sync saw.
    """
    today = today or date.today()

    # A sync failure is not a reason to withhold a briefing — yesterday's data
    # may well be enough, and the failure is recorded either way. Only the
    # engine's guard, below, decides whether there is anything to brief on.
    sync_errors: list[str] = []
    try:
        # A narrow lookback, unlike the standalone 06:00 sync's seven days. This
        # runs on every tick of a 15-minute poll, and a seven-day window is
        # ~80 Garmin requests — ~1,400 a day on a day with no data, against an
        # account the whole loop depends on staying unthrottled. Recovering
        # older late uploads is the 06:00 sync's job; this one only needs the
        # day it is briefing about and the night either side of it.
        sync_result = await _garmin_sync(
            days=get_settings().scheduler_briefing_sync_lookback_days
        )
        sync_errors = list(sync_result.errors or [])
    except Exception as e:  # recorded, and the briefing may still be viable
        logger.error("Scheduler: pre-briefing Garmin sync failed — %s", e)
        sync_errors = [f"Garmin sync raised: {e}"]

    engine = CoachingEngine()
    async with async_session() as session:
        try:
            insight = await engine.generate_daily_briefing(session, USER_ID, today=today)
        except InsufficientHealthData as e:
            # The sync detail is folded into the skip reason here and nowhere
            # else. It is the answer to the question this whole retry loop is
            # for — "did Garmin return nulls, or did every call raise?" — and
            # ``job_runs.skip_reason`` is the only place a reader will look for
            # it days later.
            raise InsufficientHealthData(_with_sync_detail(str(e), sync_errors)) from e

        logger.info("Scheduler: daily briefing generated")

        if await _get_user_email_pref("email_daily_briefing"):
            content = json.loads(insight.content)
            _deliver(partial(send_daily_briefing, content), "daily briefing")


def _with_sync_detail(reason: str, sync_errors: list[str]) -> str:
    """Append what the pre-briefing sync reported to a skip reason.

    Truncated, because ``skip_reason`` is read by a human in a table: a
    seven-day lookback can produce one line per day per failing endpoint, and
    the first few say everything the rest repeat.
    """
    if not sync_errors:
        return reason
    shown = sync_errors[:3]
    suffix = "; ".join(shown)
    if len(sync_errors) > len(shown):
        suffix += f"; (+{len(sync_errors) - len(shown)} more)"
    return f"{reason} | sync reported: {suffix}"


async def _briefing_alert(window: BriefingWindow) -> None:
    """Email the user that this morning has produced no briefing.

    Runs under ``_record_run`` as ``daily_briefing_alert``, which is what makes
    "at most one email per day" true without any state of its own: the next
    poll sees the recorded run and stands down.

    The last-upload lookup is best-effort and deliberately skipped when the
    fault is ours — asking Garmin when the watch last synced is neither
    relevant to a validation error nor worth the round trip.
    """
    if not await _get_user_email_pref("email_daily_briefing"):
        raise PipelineSkip(
            "daily briefing email is switched off — no alert to send"
        )

    upload = None if window.is_broken else await _last_device_upload()

    _deliver(
        partial(
            send_briefing_unavailable,
            day=window.day.strftime("%A, %B %-d"),
            failures=window.failed_attempts,
            is_broken=window.is_broken,
            can_still_retry=(
                window.now.time() < GENERATE_CUTOFF and not window.budget_exhausted
            ),
            detail=window.last_detail,
            last_upload=upload.uploaded_at if upload else None,
            device_name=upload.device_name if upload else None,
        ),
        "briefing unavailable",
    )


async def _last_device_upload() -> DeviceUpload | None:
    """Ask Garmin when the watch last uploaded, or None if it won't say."""
    source = GarminSource()
    if not await source.authenticate():
        logger.warning("Scheduler: Garmin auth failed during briefing alert")
        return None
    return source.get_last_device_upload()



def job_weekly_plan() -> None:
    """Generate the weekly training plan (runs Sunday evening for next week)."""
    logger.info("Scheduler: generating weekly plan")
    _run_recorded_job("weekly_plan", _weekly_plan())


async def _weekly_plan() -> None:
    engine = CoachingEngine()
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)

    async with async_session() as session:
        try:
            plan = await engine.generate_weekly_plan(session, USER_ID, next_monday)
        except NoAvailabilityConfigured:
            # Caught here, before it propagates to _record_run, so the "we
            # couldn't plan your week" email can still go out — the skip is
            # re-raised afterwards so the run is recorded as skipped as usual.
            if await _get_user_email_pref("email_weekly_plan"):
                _deliver(
                    partial(send_no_availability, week_start=str(next_monday)),
                    "no-availability",
                )
            raise
        logger.info("Scheduler: weekly plan generated for %s", next_monday)

        if await _get_user_email_pref("email_weekly_plan"):
            result = await session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan.id)
                .order_by(PlannedSession.day_of_week)
            )
            sessions = result.scalars().all()
            session_dicts = [
                {
                    "day_name": DAY_NAMES[s.day_of_week],
                    "title": s.title,
                    "sport": s.sport,
                    "duration_minutes": s.duration_minutes,
                    "notes": s.notes,
                    "details": json.loads(s.details) if s.details else None,
                }
                for s in sessions
            ]
            _deliver(
                partial(
                    send_weekly_plan,
                    summary=plan.summary or "",
                    sessions=session_dicts,
                    week_start=str(next_monday),
                    availability_source=plan.availability_source,
                ),
                "weekly plan",
            )


def job_weekly_recap() -> None:
    """Generate the weekly recap (runs Monday morning for the previous week)."""
    logger.info("Scheduler: generating weekly recap")
    _run_recorded_job("weekly_recap", _weekly_recap())


async def _weekly_recap() -> None:
    engine = CoachingEngine()
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)

    async with async_session() as session:
        insight = await engine.generate_weekly_recap(session, USER_ID, last_monday)
        logger.info("Scheduler: weekly recap generated for week of %s", last_monday)

        if await _get_user_email_pref("email_weekly_recap"):
            content = json.loads(insight.content)
            _deliver(
                partial(send_weekly_recap, content, week_start=str(last_monday)),
                "weekly recap",
            )


def job_post_workout_analysis() -> None:
    """Analyze recent activities that don't have a post-workout insight yet.

    Runs after Garmin sync. Finds activities from the last 2 days without
    an existing CoachingInsight, generates analysis for each, and sends email.
    """
    logger.info("Scheduler: starting post-workout analysis scan")
    _run_recorded_job("post_workout_analysis", _post_workout_analysis())


async def _post_workout_analysis() -> None:
    engine = CoachingEngine()
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = since - timedelta(days=2)

    async with async_session() as session:
        # Find activities from the last 2 days that have no post-workout insight
        existing_ids_stmt = (
            select(CoachingInsight.activity_id)
            .where(
                CoachingInsight.user_id == USER_ID,
                CoachingInsight.insight_type == "post_workout",
                CoachingInsight.activity_id.isnot(None),
            )
        )
        activities_stmt = (
            select(Activity)
            .where(
                Activity.user_id == USER_ID,
                Activity.start_time >= since,
                Activity.id.notin_(existing_ids_stmt),
            )
            .order_by(Activity.start_time)
        )
        result = await session.execute(activities_stmt)
        activities = list(result.scalars().all())

        if not activities:
            raise PipelineSkip("no new activities to analyse")

        logger.info("Scheduler: found %d activities to analyze", len(activities))
        send_email = await _get_user_email_pref("email_post_workout")

        analysed = 0
        skipped = 0
        failures: list[str] = []

        for activity in activities:
            # Per-activity resilience: one activity's skip or failure must not
            # abort the batch. A whole-batch skip (no activities) is raised above.
            try:
                insight = await engine.generate_post_workout_analysis(
                    session, USER_ID, activity.id
                )
                logger.info(
                    "Scheduler: post-workout analysis generated for activity %d (%s)",
                    activity.id,
                    activity.title,
                )

                if send_email:
                    content = json.loads(insight.content)
                    # A rejected send lands in ``failures`` below, like any other
                    # per-activity failure, so one undelivered email neither
                    # aborts the batch nor passes as delivered.
                    _deliver(
                        partial(send_post_workout, content, activity.title),
                        f"post-workout activity {activity.id}",
                    )
                # Tallied last so an activity counts exactly once: a failure
                # anywhere above (including the email send) lands in ``failures``
                # instead, keeping the totals in the error message honest.
                analysed += 1
            except PipelineSkip as e:
                skipped += 1
                logger.info(
                    "Scheduler: post-workout analysis skipped for activity %d — %s",
                    activity.id,
                    e,
                )
            except Exception as e:  # noqa: BLE001 - tallied, then reported once below
                failures.append(f"activity {activity.id}: {e}")
                logger.exception(
                    "Scheduler: post-workout analysis failed for activity %d", activity.id
                )

    _raise_post_workout_outcome(analysed, skipped, failures)


def _raise_post_workout_outcome(analysed: int, skipped: int, failures: list[str]) -> None:
    """Collapse the per-activity outcomes into the run's single outcome.

    Called once, after the whole batch, so the run records one coherent result
    rather than one per activity. The recording helper reads the outcome off
    this call: returning normally records ``success``, raising ``PipelineSkip``
    records ``skipped``, and raising anything else records ``failed`` with the
    message as the error detail.

    ``analysed`` counts activities that produced an insight, ``skipped`` those
    that already had one, and ``failures`` holds one message per activity that
    blew up. The empty-batch case never reaches here — it is raised as a skip
    before the loop.
    """
    total = analysed + skipped + len(failures)
    if failures:
        # A partial failure is still a failure: the run is the only durable
        # signal, so a nightly batch quietly losing one activity must not be
        # filed as a success.
        raise RuntimeError(
            f"{len(failures)} of {total} activities failed — " + "; ".join(failures)
        )
    if analysed == 0:
        # Everything already had an insight — deliberate no-op, not work done.
        raise PipelineSkip(f"all {skipped} activities already analysed")
