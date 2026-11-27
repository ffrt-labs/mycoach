"""What the daily-briefing retry loop should do right now, read off ``job_runs``.

A briefing used to be generated once, at 09:30, and a day where Garmin had not
yet uploaded produced a skip that was both invisible and final. The watch
uploads over Bluetooth only, and even a healthy one lands its data anywhere
between 05:09 and 17:44 — so a single attempt loses roughly one briefing in
five to timing alone, and a backlogged one loses days at a stretch.

The fix is a poll: try every 15 minutes from the configured briefing time,
generate on the first attempt that has usable data, and email the user if the
data still has not arrived an hour in.

**All state is derived, none is held.** Every question this module answers —
"has a briefing succeeded today?", "how many attempts since the window
opened?", "have we already emailed?" — is a query against ``job_runs``, an
append-only table already written on every run. That is a hard requirement
rather than a preference: the scheduler has no jobstore and rebuilds its jobs
from ``create_scheduler()`` on every boot, so in-memory retry state would mean
either a duplicate briefing or a silently dropped window each time the
container restarts. The container did restart mid-incident.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.config import Settings, get_settings
from mycoach.models.coaching import CoachingInsight
from mycoach.models.job_run import JobRun

logger = logging.getLogger(__name__)

USER_ID = 1  # Single-user MVP

BRIEFING_JOB = "daily_briefing"
BRIEFING_ALERT_JOB = "daily_briefing_alert"

#: Stop *generating* here. A briefing about this morning's recovery delivered
#: at 18:00 is wrong, not late.
GENERATE_CUTOFF = time(14, 0)

#: Stop *evaluating* here. Between the two cutoffs the loop no longer tries to
#: generate but still watches, so a day that never got data is still alerted on.
EVALUATE_CUTOFF = time(20, 0)

#: Poll interval, in minutes. Must divide 60 — the cron trigger is built from it.
POLL_INTERVAL_MINUTES = 15

#: Attempts that must pass before the user is emailed. Four polls at 15-minute
#: spacing is a one-hour grace period, which kills essentially every false alarm
#: in the observed history while still leaving time to fix a phone-side sync and
#: get a real briefing before the 14:00 cutoff.
ALERT_AFTER_ATTEMPTS = 4

#: How many *failed* attempts (as opposed to skips) to allow before giving up on
#: generating. A skip means the data has not arrived and may yet; a failure means
#: the pipeline is broken, and re-running a broken pipeline every 15 minutes
#: until 14:00 burns LLM spend to reproduce the same stack trace.
MAX_FAILED_ATTEMPTS = 3


@dataclass(frozen=True)
class BriefingWindow:
    """Today's briefing state as of ``now``, and what follows from it.

    Built by :func:`load_briefing_window`; the properties are pure functions of
    the fields, so the decision logic is testable without a database and the
    dashboard can render exactly what the scheduler acted on.
    """

    now: datetime
    """Local wall-clock time the decision is being made at."""

    day: date
    """The local date being briefed. Today only — an older day is never
    backfilled, because a briefing about a past morning is not a briefing."""

    opens_at: time
    """When the window opened — the configured briefing time."""

    succeeded: bool
    """Today has a briefing — either a ``daily_briefing`` run reached ``success``
    or the insight simply exists, which is what a press of the dashboard's
    button leaves behind. Reading both is the same lesson as the unified data
    guard: the manual path and the scheduled path must not be able to disagree,
    or the loop would spend the rest of the morning re-syncing Garmin and
    recording duplicate skips against a briefing the user already has."""

    skipped_attempts: int
    """Attempts that ended in ``skipped`` — no usable data yet."""

    failed_attempts: int
    """Attempts that ended in ``failed`` — the pipeline itself broke."""

    last_status: str | None
    """Status of the most recent attempt today, or None if there was none."""

    last_detail: str | None
    """Skip reason or error of the most recent attempt, whichever applies."""

    alerted: bool
    """Today's alert is settled — it went out, or there was deliberately none to
    send (the user has briefing email switched off). Either way the matter is
    closed for the day; only a send that actually *failed* is worth retrying."""

    alert_failures: int
    """Alert sends that were attempted and refused. Budgeted like generation
    failures, so a misconfigured email backend cannot turn a 15-minute poll into
    forty rejected sends a day."""

    @property
    def attempts(self) -> int:
        """Every attempt made today, however it ended."""
        return self.skipped_attempts + self.failed_attempts

    @property
    def is_open(self) -> bool:
        """Has the window opened and not yet closed for evaluation?"""
        return self.opens_at <= self.now.time() < EVALUATE_CUTOFF

    @property
    def budget_exhausted(self) -> bool:
        """Have real failures used up the retry budget?"""
        return self.failed_attempts >= MAX_FAILED_ATTEMPTS

    @property
    def should_generate(self) -> bool:
        """Should this poll attempt a briefing?"""
        return (
            not self.succeeded
            and self.is_open
            and self.now.time() < GENERATE_CUTOFF
            and not self.budget_exhausted
        )

    @property
    def should_alert(self) -> bool:
        """Should this poll email the user?

        Once per day at most. Normally that means waiting out the grace period,
        but two other states alert regardless of how few attempts have been
        made, because in both of them no further attempt is coming:

        * the failure budget is spent — three failures stop generation at three
          attempts, one short of the grace period, so keying only off the
          attempt count would leave a broken pipeline silent all day; and
        * the 14:00 cutoff has passed without a briefing — including the case
          where the container was down all morning and made no attempt at all.

        Silence in either case is the exact fault this whole window exists to
        remove.
        """
        return (
            not self.succeeded
            and not self.alerted
            and self.alert_failures < MAX_FAILED_ATTEMPTS
            and self.is_open
            and (
                self.attempts >= ALERT_AFTER_ATTEMPTS
                or self.budget_exhausted
                or self.now.time() >= GENERATE_CUTOFF
            )
        )

    @property
    def banner(self) -> str | None:
        """What the dashboard should say about today's briefing, or None.

        The dashboard used to render one fixed sentence — "Sync your Garmin
        data, then generate today's read" — while the sync had in fact
        succeeded and the watch was the thing at fault. Rendering this instead
        means the page tells the same story as ``job_runs``, continuously, from
        the moment the window opens.

        None means there is nothing to say: the briefing exists, the window has
        not opened, or nothing has been attempted yet. That last case covers a
        morning the container slept through, which the page deliberately does
        *not* claim to explain — with no runs recorded there is nothing to
        report, and a fresh install with no Garmin account at all would read
        the same. ``should_alert`` covers that day by email, where the silence
        actually mattered.
        """
        if self.succeeded or self.attempts == 0 or self.now.time() < self.opens_at:
            return None

        detail = f" Last attempt: {self.last_detail}" if self.last_detail else ""

        if self.budget_exhausted:
            return (
                f"Briefing generation failed {self.failed_attempts} times today and "
                f"retries have stopped.{detail}"
            )
        if self.is_broken:
            # failed_attempts, not attempts: the budget counts failures only, so
            # a day of skips with one error must not read "attempt 4 of 3".
            return (
                f"Briefing generation hit an error ({self.failed_attempts} of "
                f"{MAX_FAILED_ATTEMPTS} allowed); MyCoach will try again shortly."
                f"{detail}"
            )
        if self.now.time() >= GENERATE_CUTOFF:
            return (
                f"Garmin never returned today's recovery data — {self.attempts} "
                f"attempts up to the {GENERATE_CUTOFF:%H:%M} cutoff. Check that your "
                f"watch has synced; tomorrow's briefing runs as normal."
            )
        return (
            f"Waiting on Garmin: {self.attempts} attempt(s) since "
            f"{self.opens_at:%H:%M} found no sleep, HRV, or Body Battery data for "
            f"today. Retrying every {POLL_INTERVAL_MINUTES} minutes until "
            f"{GENERATE_CUTOFF:%H:%M}."
        )

    @property
    def is_broken(self) -> bool:
        """Is the outstanding problem a broken pipeline rather than absent data?

        Drives the wording of both the email and the dashboard banner. Telling
        someone to check their Bluetooth when the real fault was a validation
        error inside our own pipeline is worse than telling them nothing.
        """
        return self.last_status == "failed"


async def load_briefing_window(
    session: AsyncSession,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> BriefingWindow:
    """Read today's ``job_runs`` and build the window state from them.

    ``now`` defaults to the current time in the scheduler's timezone. The same
    timezone fixes which local day is "today" and therefore which runs count,
    so a poll and the dashboard rendered a second apart cannot disagree.
    """
    settings = settings or get_settings()
    tz = ZoneInfo(settings.scheduler_timezone or settings.timezone)
    now = now or datetime.now(tz)
    day = now.date()
    opens_at = time(settings.scheduler_briefing_hour, settings.scheduler_briefing_minute)

    runs = await _runs_on_local_day(session, day, tz)
    briefing_exists = await _briefing_exists(session, day)

    briefing_runs = [r for r in runs if r.job_name == BRIEFING_JOB]
    alert_runs = [r for r in runs if r.job_name == BRIEFING_ALERT_JOB]
    last = briefing_runs[-1] if briefing_runs else None

    return BriefingWindow(
        now=now,
        day=day,
        opens_at=opens_at,
        succeeded=briefing_exists or any(r.status == "success" for r in briefing_runs),
        skipped_attempts=sum(1 for r in briefing_runs if r.status == "skipped"),
        failed_attempts=sum(1 for r in briefing_runs if r.status == "failed"),
        last_status=last.status if last else None,
        last_detail=(last.error or last.skip_reason) if last else None,
        alerted=any(r.status != "failed" for r in alert_runs),
        alert_failures=sum(1 for r in alert_runs if r.status == "failed"),
    )


async def _briefing_exists(session: AsyncSession, day: date) -> bool:
    """Is there already a daily briefing for ``day``?

    Scoped to the user the scheduler runs as. Single-user MVP, matching every
    other scheduler-side query.
    """
    found = await session.scalar(
        select(CoachingInsight.id).where(
            CoachingInsight.user_id == USER_ID,
            CoachingInsight.insight_date == day,
            CoachingInsight.insight_type == BRIEFING_JOB,
        )
    )
    return found is not None


async def _runs_on_local_day(session: AsyncSession, day: date, tz: ZoneInfo) -> list[JobRun]:
    """Briefing and alert runs that started on ``day`` in ``tz``, oldest first.

    ``JobRun.started_at`` is a naive UTC timestamp, so the local day's bounds
    are converted to UTC before comparison rather than the column being
    converted per row — the latter cannot use an index and, on SQLite, has no
    timezone-aware function to do it with anyway.
    """
    local_start = datetime.combine(day, time.min, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_end = local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    result = await session.execute(
        select(JobRun)
        .where(
            JobRun.job_name.in_((BRIEFING_JOB, BRIEFING_ALERT_JOB)),
            JobRun.started_at >= utc_start,
            JobRun.started_at < utc_end,
        )
        .order_by(JobRun.started_at, JobRun.id)
    )
    return list(result.scalars().all())
