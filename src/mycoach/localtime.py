"""The athlete's local time, one place for it (#191).

The container runs on UTC and the database stores naive UTC; the athlete lives in
``settings.timezone``. Every conversion between the two goes through here, so the health
side (the Garmin weigh-in copy), coaching (steps, body) and the web layer agree on what
"today" and "the day of this reading" mean. Neutral ground: imports neither side.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

TimeZone = str | ZoneInfo

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
"""Weekday names by ``date.weekday()`` (and ``PlannedSession.day_of_week``)."""


def _zone(tz: TimeZone) -> ZoneInfo:
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)


def local_now(tz: TimeZone, now: datetime | None = None) -> datetime:
    """The current (or ``now``'s, aware) time in ``tz``, aware."""
    return (now or datetime.now(UTC)).astimezone(_zone(tz))


def local_today(tz: TimeZone, now: datetime | None = None) -> date:
    """Today's date in ``tz`` (the container itself runs on UTC)."""
    return local_now(tz, now).date()


def local_day(utc: datetime | str, tz: TimeZone) -> date:
    """The local calendar day of a naive-UTC time (or its ``str()``)."""
    if isinstance(utc, str):
        utc = datetime.fromisoformat(utc)
    return utc.replace(tzinfo=UTC).astimezone(_zone(tz)).date()


def to_naive_utc(when: datetime | None, tz: TimeZone) -> datetime:
    """Naive UTC for storage; None is now. A naive ``when`` is local wall time in ``tz``
    (what a ``datetime-local`` input sends)."""
    if when is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_zone(tz))
    return when.astimezone(UTC).replace(tzinfo=None)
