"""Life context episodes (CONTEXT.md, #220): what they excuse and how training ramps back.

An episode is something the athlete tells MyCoach about (an illness, a trip: #221)
that every coaching run reads around. This module holds the rules as pure functions over
``Episode`` values; the ORM row is ``models.life_context.LifeContextEpisode``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.life_context import (
    EpisodeKind,
    Equipment,
    IllnessSeverity,
    LifeContextEpisode,
)

__all__ = [
    "DayEffect",
    "Episode",
    "EpisodeKind",
    "Equipment",
    "IllnessSeverity",
    "RampBack",
    "RecoverySignals",
    "ramp_back_length",
    "effect_on",
    "is_jet_lag",
    "excusing_days",
    "ramp_back_on",
    "travel_on",
]

RAMP_BACK_MIN_DAYS = 3
RAMP_BACK_MAX_DAYS = 7
RAMP_BACK_FIRST_DAY_PCT = 50
RAMP_BACK_LAST_DAY_PCT = 90

JET_LAG_MIN_SHIFT_HOURS = 3
"""A trip this many hours or more off the home clock brings the jet-lag principles in."""


def is_jet_lag(tz_shift_hours: float | None) -> bool:
    return tz_shift_hours is not None and abs(tz_shift_hours) >= JET_LAG_MIN_SHIFT_HOURS


@dataclass(frozen=True)
class Episode:
    """One life context episode. ``ended_on`` is its last affected day, None while open.

    Kind-specific fields default to None and only their kind sets them
    (illness: ``severity``; travel: ``equipment``, ``tz_shift_hours``, ``no_training``).
    A trip may be entered in advance: ``started_on`` in the future.
    """

    id: int
    kind: EpisodeKind
    started_on: date
    ended_on: date | None
    note: str | None = None
    severity: IllnessSeverity | None = None
    equipment: Equipment | None = None
    tz_shift_hours: float | None = None
    no_training: bool | None = None

    @classmethod
    def from_row(cls, row: LifeContextEpisode) -> "Episode":
        return cls(
            id=row.id,
            kind=EpisodeKind(row.kind),
            started_on=row.started_on,
            ended_on=row.ended_on,
            note=row.note,
            severity=None if row.severity is None else IllnessSeverity(row.severity),
            equipment=None if row.equipment is None else Equipment(row.equipment),
            tz_shift_hours=row.tz_shift_hours,
            no_training=row.no_training,
        )

    @property
    def is_open(self) -> bool:
        return self.ended_on is None

    def last_day(self, today: date) -> date:
        """The last day it covers so far: ``ended_on``, or today while open."""
        return self.ended_on if self.ended_on is not None else today

    def covers(self, day: date, today: date) -> bool:
        return self.started_on <= day <= self.last_day(today) and day <= today

    def length_days(self, today: date) -> int:
        return (self.last_day(today) - self.started_on).days + 1


@dataclass(frozen=True)
class RecoverySignals:
    """Today's recovery against the athlete's own baseline, for extending a ramp-back."""

    hrv_below_baseline: bool
    resting_hr_elevated: bool

    @property
    def poor(self) -> bool:
        return self.hrv_below_baseline or self.resting_hr_elevated


RESTING_HR_ELEVATED_BPM = 5
"""Resting HR this far above its recent average counts as elevated (the briefing's HRV rule)."""


def recovery_signals(
    today: Mapping[str, Any], recent: Iterable[Mapping[str, Any]]
) -> RecoverySignals:
    """Read today's snapshot against the athlete's own baseline.

    HRV: the 7-day average below Garmin's baseline band. Resting HR: at least
    ``RESTING_HR_ELEVATED_BPM`` above the recent days' average. Missing data is no signal.
    """
    hrv, low = today.get("hrv_7day_avg"), today.get("hrv_baseline_low")
    rhr = today.get("resting_hr")
    recent_rhr = [r["resting_hr"] for r in recent if r.get("resting_hr") is not None]
    return RecoverySignals(
        hrv_below_baseline=hrv is not None and low is not None and hrv < low,
        resting_hr_elevated=(
            rhr is not None
            and bool(recent_rhr)
            and rhr >= sum(recent_rhr) / len(recent_rhr) + RESTING_HR_ELEVATED_BPM
        ),
    )


@dataclass(frozen=True)
class RampBack:
    """Where today sits in the ramp-back after an illness: "day 2/5, ~60% of normal load".

    ``extend_because`` is set only on the last day, to the recovery signals that have
    not recovered: the briefing may then suggest extending, never shortening. The
    length itself is never changed by recovery data.
    """

    episode_id: int
    day: int
    length: int
    load_pct: int
    ends_on: date
    extend_because: RecoverySignals | None = None

    @property
    def extend_suggested(self) -> bool:
        return self.extend_because is not None


class DayEffect(StrEnum):
    """What an episode does to one day, most restrictive first (``RESTRICTIVENESS``)."""

    SICK = "sick"  # rest or very light only; excuses one session
    TRAVEL_NO_TRAINING = "travel_no_training"  # "no training possible"; excuses one session
    MILD = "mild"  # lighter or shorter; excuses nothing
    TRAVEL = "travel"  # adapted to the trip's equipment; excuses nothing


RESTRICTIVENESS: tuple[DayEffect, ...] = (
    DayEffect.SICK,
    DayEffect.TRAVEL_NO_TRAINING,
    DayEffect.MILD,
    DayEffect.TRAVEL,
)
"""Per day the most restrictive effect wins: earlier here beats later.

Sick beats travel; mild beats travel (a light session, within what the trip allows).
Whether a day is a travel day at all, for relaxed substitution and "Not done
(travelling)", is ``travel_on``: that holds under any illness too.
"""

ExcusedStatus = Literal["excused_ill", "excused_travel"]
"""The ``SessionAdherence.status`` of a session an episode excused."""

EXCUSED_STATUS: dict[DayEffect, ExcusedStatus] = {
    DayEffect.SICK: "excused_ill",
    DayEffect.TRAVEL_NO_TRAINING: "excused_travel",
}
"""The ``SessionAdherence.status`` a day with this effect gives the session it excuses."""

EXCUSED_LABEL: dict[str, str] = {
    "excused_ill": "ill",
    "excused_travel": "no training while travelling",
}
"""How the recap and the email name an excused status: "3 excused (ill 3 days)"."""

STATUS_LABEL: dict[str, str] = {
    "done": "done",
    "substituted": "substituted",
    "adapted_travel": "adapted (travel)",
    "not_done": "not done",
    "not_done_travelling": "not done (travelling)",
    **{status: f"excused ({reason})" for status, reason in EXCUSED_LABEL.items()},
}
"""How the recap and the email name a session status: in a count ("1 adapted (travel)")
and, capitalised, on its chip ("Done")."""

ACTIVITY_LABEL: dict[str, str] = {
    "extra": "extra",
}
"""How the email names an activity that is not a session status: an Extra activity is
listed apart from the plan's sessions and never counted."""


def excused_reasons(excused_days: Mapping[str, int]) -> str:
    """ "ill 3 days, no training while travelling 1 day" ("" when there are none)."""
    return ", ".join(
        f"{EXCUSED_LABEL.get(status, status)} {n} day{'s' if n != 1 else ''}"
        for status, n in excused_days.items()
    )


def excused_summary(excused_sessions: int, excused_days: Mapping[str, int]) -> str | None:
    """ "3 excused (ill 3 days)", or None when nothing was excused."""
    if not excused_sessions:
        return None
    reasons = excused_reasons(excused_days)
    return f"{excused_sessions} excused" + (f" ({reasons})" if reasons else "")


def _effect_of(episode: Episode) -> DayEffect | None:
    if episode.kind == EpisodeKind.ILLNESS:
        return DayEffect.MILD if episode.severity == IllnessSeverity.MILD else DayEffect.SICK
    if episode.kind == EpisodeKind.TRAVEL:
        return DayEffect.TRAVEL_NO_TRAINING if episode.no_training else DayEffect.TRAVEL
    return None


def effect_on(episodes: Iterable[Episode], day: date, *, today: date) -> DayEffect | None:
    """The most restrictive effect any episode has on ``day`` (None: an ordinary day).

    An open episode covers days up to ``today``, never the future.
    """
    effects = {
        effect for e in episodes if e.covers(day, today) and (effect := _effect_of(e)) is not None
    }
    return next((e for e in RESTRICTIVENESS if e in effects), None)


def travel_on(episodes: Iterable[Episode], day: date, *, today: date) -> Episode | None:
    """The trip covering ``day``, or None (the latest-started one if trips overlap).

    Up to ``today`` only, like every effect: a future trip changes no completion yet.
    """
    trips = [e for e in episodes if e.kind == EpisodeKind.TRAVEL and e.covers(day, today)]
    return max(trips, key=lambda e: (e.started_on, e.id), default=None)


def excusing_days(
    episodes: Iterable[Episode], week_start: date, *, today: date
) -> dict[date, ExcusedStatus]:
    """The week's days that excuse a session, in day order, each with the status it gives.

    A day counts once however many episodes cover it, so a session is never
    excused twice for the same day.
    """
    episodes = list(episodes)
    days: dict[date, ExcusedStatus] = {}
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        effect = effect_on(episodes, day, today=today)
        if effect is not None and effect in EXCUSED_STATUS:
            days[day] = EXCUSED_STATUS[effect]
    return days


def ramp_back_length(severity: IllnessSeverity | str, ill_days: int) -> int:
    """Days of ramp-back after an illness: 3 when mild, else its length clamped to 3-7."""
    if IllnessSeverity(severity) == IllnessSeverity.MILD:
        return RAMP_BACK_MIN_DAYS
    return max(RAMP_BACK_MIN_DAYS, min(RAMP_BACK_MAX_DAYS, ill_days))


def _load_pct(day: int, length: int) -> int:
    span = RAMP_BACK_LAST_DAY_PCT - RAMP_BACK_FIRST_DAY_PCT
    return round(RAMP_BACK_FIRST_DAY_PCT + span * (day - 1) / (length - 1))


def ramp_back_on(
    episodes: Iterable[Episode], today: date, *, recovery: RecoverySignals | None = None
) -> RampBack | None:
    """The ramp-back ``today`` is in, or None (still ill, or fully back).

    An open illness wins over any ramp-back: while ill there is nothing to ramp
    back to. Of several ended illnesses the latest one sets the ramp-back.
    """
    illnesses = [e for e in episodes if e.kind == EpisodeKind.ILLNESS]
    if any(e.covers(today, today) for e in illnesses):
        return None
    ended = [e for e in illnesses if e.ended_on is not None and e.ended_on < today]
    if not ended:
        return None
    latest = max(ended, key=lambda e: (e.ended_on, e.id))
    assert latest.ended_on is not None
    length = ramp_back_length(latest.severity or IllnessSeverity.SICK, latest.length_days(today))
    day = (today - latest.ended_on).days
    if day > length:
        return None
    return RampBack(
        episode_id=latest.id,
        day=day,
        length=length,
        load_pct=_load_pct(day, length),
        ends_on=latest.ended_on + timedelta(days=length),
        extend_because=recovery
        if day == length and recovery is not None and recovery.poor
        else None,
    )


# --- Commands and queries -----------------------------------------------------------


class EpisodeAlreadyOpenError(Exception):
    """An episode of this kind is already open: edit or close it instead."""


async def open_episodes(
    session: AsyncSession, user_id: int, kind: EpisodeKind | None = None
) -> list[LifeContextEpisode]:
    stmt = select(LifeContextEpisode).where(
        LifeContextEpisode.user_id == user_id, LifeContextEpisode.ended_on.is_(None)
    )
    if kind is not None:
        stmt = stmt.where(LifeContextEpisode.kind == kind)
    return list((await session.execute(stmt.order_by(LifeContextEpisode.id))).scalars().all())


class IllnessOverlapError(Exception):
    """Another illness already covers some of these days: illnesses don't overlap."""


def format_days(start: date, end: date) -> str:
    """ "9–15 Nov", "28 Oct–3 Nov", "12 Nov": a day range as the dashboard shows it."""
    if start == end:
        return f"{start.day} {start:%b}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%b}"
    return f"{start.day} {start:%b}–{end.day} {end:%b}"


async def open_illness(
    session: AsyncSession,
    user_id: int,
    *,
    severity: IllnessSeverity,
    note: str | None,
    today: date,
    started_on: date | None = None,
    ended_on: date | None = None,
) -> LifeContextEpisode:
    """ "Feeling unwell": an illness from ``started_on`` (default today).

    With no ``ended_on`` it stays open until "Feeling better" (one open illness at a
    time); a past ``started_on`` is "ill since Thursday". With ``ended_on`` it is an
    illness told after the fact (#236): ``ended_on`` = yesterday stores what "Feeling
    better" would have. An illness may not overlap another illness, so "Ill since"
    stays unambiguous; illnesses that only touch are allowed and not merged. Overlap
    with travel is allowed (sick wins).
    """
    start = today if started_on is None else started_on
    if ended_on is None and await open_episodes(session, user_id, EpisodeKind.ILLNESS):
        raise EpisodeAlreadyOpenError("An illness is already open")
    last = today if ended_on is None else ended_on
    for other in await episodes_between(session, user_id, start, last):
        if other.kind == EpisodeKind.ILLNESS:
            dates = format_days(other.started_on, other.last_day(today))
            raise IllnessOverlapError(f"An illness already covers {dates}")
    episode = LifeContextEpisode(
        user_id=user_id,
        kind=EpisodeKind.ILLNESS,
        started_on=start,
        ended_on=ended_on,
        severity=severity,
        note=note,
    )
    session.add(episode)
    await session.flush()
    return episode


async def get_episode(
    session: AsyncSession, user_id: int, episode_id: int
) -> LifeContextEpisode | None:
    episode = await session.get(LifeContextEpisode, episode_id)
    return episode if episode is not None and episode.user_id == user_id else None


async def open_travel(
    session: AsyncSession,
    user_id: int,
    *,
    started_on: date,
    ended_on: date | None,
    equipment: Equipment,
    tz_shift_hours: float | None,
    no_training: bool,
    note: str | None,
) -> LifeContextEpisode:
    """ "Travelling" (#221): a trip from ``started_on``, which may be in the future.

    ``ended_on`` is the expected last travel day, if known. Trips may overlap any
    other episode: per day the most restrictive effect wins.
    """
    episode = LifeContextEpisode(
        user_id=user_id,
        kind=EpisodeKind.TRAVEL,
        started_on=started_on,
        ended_on=ended_on,
        equipment=equipment,
        tz_shift_hours=tz_shift_hours,
        no_training=no_training,
        note=note,
    )
    session.add(episode)
    await session.flush()
    return episode


def close(episode: LifeContextEpisode, *, today: date) -> None:
    """ "Feeling better": the episode's last day is yesterday (today is ramp-back day 1).

    An episode opened today and closed today still covers today. "Back home" on a
    trip ends it today instead: the journey home is still a travel day.
    """
    if episode.kind == EpisodeKind.TRAVEL:
        episode.ended_on = max(episode.started_on, today)
        return
    episode.ended_on = max(episode.started_on, today - timedelta(days=1))


class EpisodeUnderWayError(Exception):
    """Only a trip that has not started can be cancelled; close one under way."""


async def cancel_trip(session: AsyncSession, episode: LifeContextEpisode, *, today: date) -> None:
    """Drop a trip entered in advance that will not happen."""
    if episode.kind != EpisodeKind.TRAVEL or episode.started_on <= today:
        raise EpisodeUnderWayError("Only a trip that has not started can be cancelled")
    await session.delete(episode)
    await session.flush()


async def episodes_between(
    session: AsyncSession, user_id: int, start: date, end: date
) -> list[Episode]:
    """Every episode touching ``start``..``end`` (inclusive), open ones included."""
    stmt = (
        select(LifeContextEpisode)
        .where(
            LifeContextEpisode.user_id == user_id,
            LifeContextEpisode.started_on <= end,
            or_(LifeContextEpisode.ended_on.is_(None), LifeContextEpisode.ended_on >= start),
        )
        .order_by(LifeContextEpisode.started_on, LifeContextEpisode.id)
    )
    return [Episode.from_row(r) for r in (await session.execute(stmt)).scalars().all()]


@dataclass(frozen=True)
class LifeContext:
    """What a coaching run reads about the athlete's life (#220).

    ``episodes`` touch the run's window (a day for the briefing, a week for the
    recap); ``ramp_back`` is where ``today`` sits after an illness, if anywhere.
    """

    today: date
    episodes: list[Episode]
    ramp_back: RampBack | None

    @property
    def is_empty(self) -> bool:
        return not self.episodes and self.ramp_back is None


async def life_context_for(
    session: AsyncSession,
    user_id: int,
    start: date,
    end: date,
    *,
    today: date,
    recovery: RecoverySignals | None = None,
) -> LifeContext:
    """The episodes touching ``start``..``end`` and ``today``'s ramp-back.

    The ramp-back looks back far enough to find the illness that set it
    (``RAMP_BACK_MAX_DAYS`` before today). A trip entered in advance (#221) is
    read too, so the weekly plan can plan around it; illness never starts later
    than today.
    """
    episodes = await episodes_between(session, user_id, start, end)
    recent = await episodes_between(
        session, user_id, today - timedelta(days=RAMP_BACK_MAX_DAYS + 1), today
    )
    return LifeContext(
        today=today,
        episodes=[e for e in episodes if e.started_on <= today or e.kind == EpisodeKind.TRAVEL],
        ramp_back=ramp_back_on(recent, today, recovery=recovery),
    )
