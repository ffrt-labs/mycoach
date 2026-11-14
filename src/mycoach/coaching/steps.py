"""Daily steps as the briefing shows them: a 7-day strip ending yesterday (#196).

The numbers are computed here, from the health module's step counts, and
rendered by code in the email; the LLM only interprets them (``steps_summary``).
Evidence and rules: docs/research/health-metrics.md section 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for

NEUTRAL_BAND = 0.10
"""A change within +/-10% of the previous week is noise (wrist step counts are that accurate)."""

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

Trend = Literal["up", "down", "neutral"]


@dataclass(frozen=True)
class StepsDay:
    day: date
    steps: int | None
    fill_pct: int
    """How much of the bar is filled: steps as a share of the target, capped at 100."""
    at_goal: bool
    is_yesterday: bool

    @property
    def weekday(self) -> str:
        return _WEEKDAYS[self.day.weekday()]


@dataclass(frozen=True)
class StepsSummary:
    target: int
    days: list[StepsDay]
    """The 7 days ending yesterday, oldest first."""
    yesterday: int | None
    avg_7d: int | None
    """Mean over the last 7 days that have a count (missing days are left out)."""
    prev_avg_7d: int | None
    days_at_goal: int
    change: int | None
    """``avg_7d - prev_avg_7d`` in steps/day."""
    trend: Trend | None
    """``neutral`` within +/-10% of the previous average."""

    @property
    def has_data(self) -> bool:
        return any(d.steps is not None for d in self.days)


def _mean(counts: list[int | None]) -> int | None:
    known = [c for c in counts if c is not None]
    return round(sum(known) / len(known)) if known else None


def summarize_steps(daily: list[dict[str, Any]], target: int) -> StepsSummary:
    """Summarise ``health.get_daily_steps`` output (14 days, oldest first, ending yesterday)."""
    counts = [d["steps"] for d in daily]
    last, previous = counts[-7:], counts[:-7]
    days = [
        StepsDay(
            day=date.fromisoformat(d["date"]),
            steps=d["steps"],
            fill_pct=min(100, round(d["steps"] * 100 / target)) if d["steps"] else 0,
            at_goal=d["steps"] is not None and d["steps"] >= target,
            is_yesterday=i == len(daily[-7:]) - 1,
        )
        for i, d in enumerate(daily[-7:])
    ]
    avg, prev = _mean(last), _mean(previous)
    change: int | None = None
    trend: Trend | None = None
    if avg is not None and prev:
        change = avg - prev
        if abs(change) <= NEUTRAL_BAND * prev:
            trend = "neutral"
        elif change > 0:
            trend = "up"
        else:
            trend = "down"
    return StepsSummary(
        target=target,
        days=days,
        yesterday=counts[-1],
        avg_7d=avg,
        prev_avg_7d=prev,
        days_at_goal=sum(1 for d in days if d.at_goal),
        change=change,
        trend=trend,
    )


def local_today(timezone: str, now: datetime | None = None) -> date:
    """Today's date in ``timezone`` (the container itself runs on UTC)."""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()


async def load_steps_summary(
    session: AsyncSession, user_id: int, today: date, target: int
) -> StepsSummary:
    """The summary for the 14 days before ``today`` (yesterday back)."""
    athlete_id = await athlete_id_for(session, user_id)
    daily = await health.get_daily_steps(athlete_id, until=today - timedelta(days=1), days=14)
    return summarize_steps(daily, target)
