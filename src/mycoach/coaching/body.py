"""The body summary: what weight and waist readings mean, for the email and the prompt (#198).

Pure computation over ``mycoach.health.list_body_measurements`` dicts. Code produces every
number; the LLM only interprets them (spec #191). The rules come from
docs/research/health-metrics.md §4.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mycoach.localtime import local_day, local_today

EMA_ALPHA = 0.1
"""Per day (Hacker's Diet). A gap of ``n`` days weighs the new reading ``1 - 0.9**n``."""
TREND_MIN_READINGS = 4
TREND_WINDOW_DAYS = 14
"""A trend needs ``TREND_MIN_READINGS`` days with a weigh-in in the 14 days ending at the
latest one; otherwise one noisy day would read as a trend."""
RATE_WINDOW_DAYS = 28
WEIGHT_STALE_DAYS = 14
WEIGH_IN_DUE_DAYS = 7
WAIST_DUE_DAYS = 30


@dataclass(frozen=True)
class WeightSummary:
    latest_kg: float
    measured_on: date
    days_ago: int
    when: str
    trend_kg: float | None = None
    """Time-aware EMA at the latest weigh-in; None below the reading guard."""
    rate_kg_per_week: float | None = None
    """Least-squares slope of the daily weigh-ins over the last 28 days (a slope of the
    readings, not of the EMA, which lags by design); None whenever ``trend_kg`` is."""
    stale: bool = False
    """The latest weigh-in is 14+ days old: the trend describes the past."""


WAIST_CHANGE_DAYS = 28
WAIST_BASELINE_WINDOW = (14, 42)
"""The baseline for the 4-week change is the waist reading closest to 28 days before the
latest, taken 14 to 42 days before it (a monthly check never lands on day 28 exactly)."""
WAIST_MEANINGFUL_CM = 2.0
"""Self-measurement error is 1-2 cm: a smaller change is noise."""
WAIST_TO_HEIGHT_TARGET = 0.5
NOTES_WINDOW_DAYS = 14


@dataclass(frozen=True)
class WaistSummary:
    latest_cm: float
    measured_on: date
    days_ago: int
    when: str
    ratio: float | None = None
    """Waist-to-height; None without a height."""
    change_cm: float | None = None
    """Latest minus the ~4-weeks-earlier reading; None without one."""

    @property
    def change_meaningful(self) -> bool:
        return self.change_cm is not None and abs(self.change_cm) >= WAIST_MEANINGFUL_CM


@dataclass(frozen=True)
class BodySummary:
    weight: WeightSummary | None
    waist: WaistSummary | None = None
    weigh_in_due: bool = False
    """No weigh-in for 7+ days (or none ever)."""
    waist_check_due: bool = False
    """No waist reading for 30+ days (or none ever)."""
    recent_notes: list[tuple[str, str]] = field(default_factory=list)
    """``(day label, note)`` from the last 14 days, newest first: the context for a water
    jump (salt, carbs, alcohol, travel, a hard session)."""

    @property
    def empty(self) -> bool:
        return self.weight is None and self.waist is None


def when_text(measured_on: date, today: date) -> str:
    """"measured today", "measured yesterday", "measured Thu (2 days ago)", "measured 5 Nov
    (9 days ago)" (a weekday name is ambiguous past six days)."""
    days = (today - measured_on).days
    if days <= 0:
        return "measured today"
    if days == 1:
        return "measured yesterday"
    label = measured_on.strftime("%a") if days < 7 else f"{measured_on.day} {measured_on:%b}"
    return f"measured {label} ({days} days ago)"


@dataclass(frozen=True)
class _Reading:
    day: date
    value: float


def _daily(measurements: list[dict[str, Any]], field: str, tz: ZoneInfo) -> list[_Reading]:
    """One reading per local day (the mean of that day's), oldest first."""
    by_day: dict[date, list[float]] = {}
    for m in measurements:
        if m.get(field) is not None:
            by_day.setdefault(local_day(m["measured_at"], tz), []).append(float(m[field]))
    return [_Reading(day, sum(v) / len(v)) for day, v in sorted(by_day.items())]


def _ema(readings: list[_Reading]) -> float:
    """Time-aware EMA, seeded with the first reading: each day of a gap decays the old."""
    trend = readings[0].value
    for prev, cur in zip(readings, readings[1:], strict=False):
        alpha = 1 - (1 - EMA_ALPHA) ** (cur.day - prev.day).days
        trend += alpha * (cur.value - trend)
    return trend


def _slope_per_day(readings: list[_Reading]) -> float:
    origin = readings[0].day
    xs = [float((r.day - origin).days) for r in readings]
    ys = [r.value for r in readings]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def _since(readings: list[_Reading], end: date, days: int) -> list[_Reading]:
    """The readings in the ``days`` local days ending at ``end`` (inclusive)."""
    return [r for r in readings if (end - r.day).days < days]


def _weight(readings: list[_Reading], today: date) -> WeightSummary | None:
    if not readings:
        return None
    latest = readings[-1]
    days_ago = (today - latest.day).days
    trend = rate = None
    if len(_since(readings, latest.day, TREND_WINDOW_DAYS)) >= TREND_MIN_READINGS:
        trend = _ema(readings)
        rate = _slope_per_day(_since(readings, latest.day, RATE_WINDOW_DAYS)) * 7
    return WeightSummary(
        latest_kg=latest.value,
        measured_on=latest.day,
        days_ago=days_ago,
        when=when_text(latest.day, today),
        trend_kg=trend,
        rate_kg_per_week=rate,
        stale=days_ago >= WEIGHT_STALE_DAYS,
    )


def _waist(
    readings: list[_Reading], height_cm: float | None, today: date
) -> WaistSummary | None:
    if not readings:
        return None
    latest = readings[-1]
    low, high = WAIST_BASELINE_WINDOW
    candidates = [r for r in readings if low <= (latest.day - r.day).days <= high]
    baseline = min(
        candidates,
        key=lambda r: abs((latest.day - r.day).days - WAIST_CHANGE_DAYS),
        default=None,
    )
    return WaistSummary(
        latest_cm=latest.value,
        measured_on=latest.day,
        days_ago=(today - latest.day).days,
        when=when_text(latest.day, today),
        ratio=latest.value / height_cm if height_cm else None,
        change_cm=latest.value - baseline.value if baseline is not None else None,
    )


def _recent_notes(
    measurements: list[dict[str, Any]], today: date, tz: ZoneInfo
) -> list[tuple[str, str]]:
    dated = [
        (m["measured_at"], local_day(m["measured_at"], tz), m["note"])
        for m in measurements
        if m.get("note")
    ]
    dated = [d for d in dated if (today - d[1]).days < NOTES_WINDOW_DAYS]
    dated.sort(key=lambda d: d[0], reverse=True)
    return [(f"{day:%a} {day.day} {day:%b}", note) for _, day, note in dated]


def summarize_body(
    measurements: list[dict[str, Any]],
    *,
    height_cm: float | None,
    now: datetime,
    tz: ZoneInfo,
) -> BodySummary:
    """Summarise the athlete's readings as of ``now`` (aware), in local days of ``tz``.

    ``measurements`` should reach back past the 28-day rate window; older readings only
    seed the EMA.
    """
    today = local_today(tz, now)
    weight = _weight(_daily(measurements, "weight_kg", tz), today)
    waist = _waist(_daily(measurements, "waist_cm", tz), height_cm, today)
    return BodySummary(
        weight=weight,
        waist=waist,
        weigh_in_due=weight is None or weight.days_ago >= WEIGH_IN_DUE_DAYS,
        waist_check_due=waist is None or waist.days_ago >= WAIST_DUE_DAYS,
        recent_notes=_recent_notes(measurements, today, tz),
    )
