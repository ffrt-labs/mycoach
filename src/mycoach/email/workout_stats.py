"""The post-workout email's code-rendered numbers (#239): stat grid, zone bar and trend.

Every number here comes from the activity, its planned session, its performed sets and
the similar-activity history: the coach never writes an observed number (spec #230).
A value the data doesn't have leaves its tile out; it is never shown as "—".
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mycoach.coaching.context import PlanAdherence
from mycoach.coaching.substitution import zone_minutes
from mycoach.email.coach_notes import NO_VALUE
from mycoach.models.plan import SessionIntent

MINUS = "−"


@dataclass(frozen=True)
class Tile:
    """One stat tile: "Pace 2:24 /100m · −7 s vs last", or a recap recovery tile
    "Sleep score / 64–81 / Low on Thu 5 Nov"."""

    label: str
    value: str
    unit: str = ""
    sub: str | None = None
    tone: str | None = None
    """``good``, ``watch`` or ``alert`` colours ``sub``; None leaves it muted."""


Activity = Mapping[str, Any]


# --- Formatting -----------------------------------------------------------------------


def _clock(seconds: float) -> str:
    """ "36:06", or "1:02:05" past the hour."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _num(value: float, places: int) -> str:
    return f"{value:.{places}f}"


def _signed(delta: float, places: int) -> str:
    """ "−7", "+1.2", "±0": a typographic minus, like the artboard."""
    text = _num(abs(delta), places)
    if float(text) == 0:
        return f"±{text}"
    return f"{MINUS if delta < 0 else '+'}{text}"


def _number(activity: Activity | None, key: str) -> float | None:
    value = (activity or {}).get(key)
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


# --- Derived metrics ------------------------------------------------------------------


def _moving_seconds(activity: Activity | None) -> float | None:
    moving = _number(activity, "moving_duration_seconds")
    if moving is not None:
        return moving
    minutes = _number(activity, "duration_minutes")
    return minutes * 60 if minutes is not None else None


def _pace(activity: Activity | None, per_meters: float) -> float | None:
    """Seconds per ``per_meters``, over the moving time."""
    seconds = _moving_seconds(activity)
    distance = _number(activity, "distance_meters")
    if seconds is None or distance is None:
        return None
    return seconds / (distance / per_meters)


def _swim_pace(activity: Activity | None) -> float | None:
    return _pace(activity, 100)


def _run_pace(activity: Activity | None) -> float | None:
    return _pace(activity, 1000)


_PLANNED_METERS_KEYS = ("distance_m", "distance_meters", "total_distance_m")
_PLANNED_KM_KEYS = ("distance_km", "target_distance_km")


def _details(planned: Mapping[str, Any] | None) -> dict[str, Any]:
    details = (planned or {}).get("details")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return {}
    return details if isinstance(details, dict) else {}


def _planned_meters(planned: Mapping[str, Any] | None) -> float | None:
    """The planned session's distance, from the details keys the planner writes."""
    details = _details(planned)
    for key in _PLANNED_METERS_KEYS:
        if (meters := _number(details, key)) is not None:
            return meters
    for key in _PLANNED_KM_KEYS:
        if (km := _number(details, key)) is not None:
            return km * 1000
    return None


# Garmin's training-effect bands: 2.0–2.9 is "Maintaining".
_TE_BANDS: tuple[tuple[float, str], ...] = (
    (5.0, "Overreaching"),
    (4.0, "Highly improving"),
    (3.0, "Improving"),
    (2.0, "Maintaining"),
    (1.0, "Minor"),
    (0.0, "No benefit"),
)


def _te_label(te: float) -> str:
    return next(label for floor, label in _TE_BANDS if te >= floor)


# --- Tiles ----------------------------------------------------------------------------


def _vs_last(
    today: float | None, last: float | None, places: int, unit: str = ""
) -> tuple[str | None, str | None]:
    """ "−7 s vs last" and its tone, lower being better; (None, None) without both."""
    if today is None or last is None:
        return None, None
    delta = today - last
    rounded = round(delta, places)
    tone = None if rounded == 0 else ("good" if rounded < 0 else "watch")
    return f"{_signed(delta, places)}{unit} vs last", tone


def _compared(
    label: str,
    today: float | None,
    last: float | None,
    show: Callable[[float], str],
    unit: str = "",
    places: int = 0,
    delta_unit: str = "",
) -> Tile | None:
    """A tile compared with the last activity, lower being better."""
    if today is None:
        return None
    return Tile(label, show(today), unit, *_vs_last(today, last, places, delta_unit))


def _training_effect_tile(activity: Activity) -> Tile | None:
    """Garmin's aerobic training effect and load; only from Garmin data."""
    te = _number(activity, "training_effect_aerobic")
    if te is None or "garmin" not in (activity.get("sources") or []):
        return None
    load = _number(activity, "epoc")
    sub = _te_label(te) + (f" · load {round(load)}" if load is not None else "")
    return Tile("Training effect", _num(te, 1), sub=sub)


def _moving_time_tile(activity: Activity, label: str, planned_minutes: Any) -> Tile | None:
    seconds = _moving_seconds(activity)
    if seconds is None:
        return None
    sub = f"of {planned_minutes} min planned" if planned_minutes else None
    return Tile(label, _clock(seconds), sub=sub)


def _swim_tiles(
    activity: Activity, planned: Mapping[str, Any] | None, last: Activity | None
) -> list[Tile | None]:
    distance = _number(activity, "distance_meters")
    planned_m = _planned_meters(planned)
    distance_tile = None
    if distance is not None:
        sub = f"of {round(planned_m)} planned" if planned_m else None
        tone = "watch" if planned_m and distance < planned_m else None
        distance_tile = Tile("Distance", str(round(distance)), "m", sub, tone)

    pace = _swim_pace(activity)
    swolf = _number(activity, "avg_swolf")
    strokes = _number(activity, "avg_strokes_per_length")
    return [
        distance_tile,
        _moving_time_tile(activity, "Moving time", (planned or {}).get("duration_minutes")),
        _compared("Pace", pace, _swim_pace(last), _clock, "/100m", 0, " s"),
        _compared("SWOLF", swolf, _number(last, "avg_swolf"), _whole),
        _compared(
            "Strokes / length",
            strokes,
            _number(last, "avg_strokes_per_length"),
            lambda v: _num(v, 1),
            places=1,
        ),
        _training_effect_tile(activity),
    ]


def _plain(
    label: str, value: float | None, show: Callable[[float], str], unit: str = ""
) -> Tile | None:
    return Tile(label, show(value), unit) if value is not None else None


def _whole(value: float) -> str:
    return str(round(value))


def _run_tiles(activity: Activity) -> list[Tile | None]:
    return [
        _plain("Distance", _number(activity, "distance_meters"), lambda m: _num(m / 1000, 2), "km"),
        _moving_time_tile(activity, "Time", None),
        _plain("Pace", _run_pace(activity), _clock, "/km"),
        _plain("Avg HR", _number(activity, "avg_hr"), _whole, "bpm"),
        _plain("Cadence", _number(activity, "avg_cadence"), _whole, "spm"),
        _training_effect_tile(activity),
    ]


Set = Mapping[str, Any]


def _working(sets: Sequence[Set]) -> list[Set]:
    """The performed sets that count: everything but warm-ups."""
    return [s for s in sets if s.get("set_type") != "warmup"]


def _prescribed(sets: Sequence[Set]) -> bool:
    """Whether any set fulfilled a Prescription (the logger recorded what was planned)."""
    return any(
        s.get("prescribed_weight_kg") is not None or s.get("prescribed_reps") is not None
        for s in sets
    )


def _planned_sets(planned: Mapping[str, Any] | None) -> int | None:
    exercises = _details(planned).get("exercises")
    if not isinstance(exercises, list):
        return None
    counts = [e.get("sets") for e in exercises if isinstance(e, dict)]
    total = sum(c for c in counts if isinstance(c, int) and c > 0)
    return total or None


def _sets_tile(sets: Sequence[Set], planned: Mapping[str, Any] | None) -> Tile | None:
    """ "4/6": the planned count only when the sets fulfilled a Prescription."""
    working = _working(sets)
    if not working:
        return None
    of = _planned_sets(planned) if _prescribed(sets) else None
    return Tile("Sets", str(len(working)), f"/{of}" if of else "")


def _top_set_tile(sets: Sequence[Set]) -> Tile | None:
    """The heaviest working set (most reps breaking a tie): "100 kg × 5", its exercise."""
    loaded = [s for s in _working(sets) if _number(s, "weight_kg") and _number(s, "reps")]
    if not loaded:
        return None
    top = max(loaded, key=lambda s: (s["weight_kg"], s["reps"]))
    return Tile(
        "Top set",
        _num(top["weight_kg"], 0 if float(top["weight_kg"]).is_integer() else 1),
        f"kg × {top['reps']}",
        str(top.get("exercise_title") or "") or None,
    )


def _volume(sets: Sequence[Set]) -> float | None:
    """Kilograms moved over the working sets (weight × reps)."""
    total = sum((_number(s, "weight_kg") or 0) * (_number(s, "reps") or 0) for s in _working(sets))
    return total or None


def _avg_rpe(sets: Sequence[Set]) -> float | None:
    rpes = [r for s in _working(sets) if (r := _number(s, "rpe")) is not None]
    return sum(rpes) / len(rpes) if rpes else None


def _gym_tiles(
    activity: Activity, planned: Mapping[str, Any] | None, sets: Sequence[Set]
) -> list[Tile | None]:
    return [
        _sets_tile(sets, planned),
        _top_set_tile(sets),
        _plain("Volume", _volume(sets), _whole, "kg"),
        _plain("Avg RPE", _avg_rpe(sets), lambda r: _num(r, 1)),
        _plain("Duration", _number(activity, "duration_minutes"), _whole, "min"),
        _training_effect_tile(activity),
    ]


def stat_tiles(
    activity: Activity | None,
    *,
    planned: Mapping[str, Any] | None = None,
    sets: Sequence[Mapping[str, Any]] | None = None,
    history: Sequence[Activity] | None = None,
) -> list[Tile]:
    """The 3 × 2 stat grid of the activity's sport, its empty tiles left out.

    ``history`` is the similar activities, newest first: its first one is "last".
    """
    if not activity:
        return []
    last = history[0] if history else None
    builders: dict[str, Callable[[], list[Tile | None]]] = {
        "swimming": lambda: _swim_tiles(activity, planned, last),
        "running": lambda: _run_tiles(activity),
        "gym": lambda: _gym_tiles(activity, planned, sets or []),
    }
    build = builders.get(str(activity.get("sport")))
    return [t for t in build() if t] if build else []


def cell_width(n: int) -> str:
    """Each of ``n`` equal cells' share of a row, in percent: "33.33"."""
    return f"{round(100 / n, 2):g}"


def gutters(n: int, gap: int = 8) -> list[str]:
    """The side padding of each of ``n`` cells in a row of tiles (#248).

    The gaps come out of the cells, so the row is flush with the content edge, and
    every gap is ``gap`` px. In whole pixels, which every client renders alike
    (Outlook rounds fractions its own way): for thirds, 0/5, 3/3, 5/0, so the tiles'
    widths differ by 1 px at most.
    """
    left = [round(gap * i / n) for i in range(n)]
    right = [gap - left[i + 1] for i in range(n - 1)] + [0]
    return [
        f"padding-left:{lft}px;padding-right:{rgt}px;" for lft, rgt in zip(left, right, strict=True)
    ]


@dataclass(frozen=True)
class StatGrid:
    """Tiles laid out in rows for ``components/_stat_grid.html``."""

    rows: list[list[Tile | None]]
    """Each row ``per_row`` cells; a short last row is padded with empty cells (None)."""
    cell_width: str
    """Each cell's share of the row, in percent: "33.33"."""
    gutters: list[str]
    """Each column's side padding (``gutters``), the same in every row."""


def stat_grid(tiles: Sequence[Tile], per_row: int = 3) -> StatGrid | None:
    """The tiles ``per_row`` to a row, or None without tiles."""
    if not tiles:
        return None
    rows: list[list[Tile | None]] = [
        list(tiles[i : i + per_row]) for i in range(0, len(tiles), per_row)
    ]
    rows[-1] += [None] * (per_row - len(rows[-1]))
    return StatGrid(rows, cell_width(per_row), gutters(per_row))


# --- Heart rate zones -----------------------------------------------------------------


@dataclass(frozen=True)
class ZoneBand:
    """One band of the zone bar and its legend: "Z2 · 17.3 min · 56%"."""

    label: str
    minutes: str
    pct: int
    """Its share of the time in zones; 0 leaves it out of the bar, not the legend."""
    css: str
    """Its colour class, ``zone-<name>`` from ``tones.ZONE_STYLES``."""


@dataclass(frozen=True)
class ZoneBar:
    bands: list[ZoneBand]
    target: str | None
    """ "Target: Z2", from the session's intent; None when the intent names no zone."""


# The bar's bands (Z4 and Z5 share one) and their colour classes (tones.ZONE_STYLES).
_BANDS: tuple[tuple[str, tuple[int, ...], str], ...] = (
    ("Z1", (1,), "zone-z1"),
    ("Z2", (2,), "zone-z2"),
    ("Z3", (3,), "zone-z3"),
    ("Z4–5", (4, 5), "zone-z45"),
)

INTENT_ZONE_TARGET: dict[str, str] = {
    SessionIntent.AEROBIC_BASE: "Z2",
    SessionIntent.RECOVERY: "Z1",
    SessionIntent.THRESHOLD: "Z3–4",
    SessionIntent.INTERVALS: "Z4–5",
}
"""The zones a Session intent aims for. Strength and skill aim for none."""


def session_intent(
    activity: Activity | None,
    planned: Mapping[str, Any] | None,
    week: PlanAdherence | None,
) -> str | None:
    """The intent of the session the activity did: its planned session's, else the one
    its week's Completion says it did."""
    if planned and planned.get("intent"):
        return str(planned["intent"])
    if week is None or not activity:
        return None
    done = (s for s in week["sessions"] if s.get("done_by") == activity.get("external_id"))
    return next((s["intent"] for s in done if s.get("intent")), None)


def _minutes(value: float) -> str:
    return "0 min" if round(value, 1) == 0 else f"{value:.1f} min"


def zone_bar(activity: Activity | None, intent: str | None) -> ZoneBar | None:
    """Minutes per zone as a bar and legend, or None without zone data."""
    minutes = zone_minutes((activity or {}).get("hr_zones"))
    total = sum(minutes.values())
    if total <= 0:
        return None
    bands = []
    for label, zones, css in _BANDS:
        band = sum(minutes.get(z, 0.0) for z in zones)
        bands.append(ZoneBand(label, _minutes(band), round(band / total * 100), css))
    target = INTENT_ZONE_TARGET.get(intent or "")
    return ZoneBar(bands, f"Target: {target}" if target else None)


# --- Trend ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendRow:
    metric: str
    today: str
    last: str
    best: str
    improved: bool
    """Today beats the last one: today's value is marked good."""


@dataclass(frozen=True)
class TrendTable:
    label: str
    """ "Trend · last 6 swims"."""
    last_label: str
    """The last activity's column: its day, "05 Nov"."""
    rows: list[TrendRow]


@dataclass(frozen=True)
class _Metric:
    label: str
    read: Callable[[Activity | None], float | None]
    show: Callable[[float], str]
    lower_is_better: bool


_TREND_METRICS: dict[str, tuple[str, tuple[_Metric, ...]]] = {
    "swimming": (
        "swims",
        (
            _Metric("Pace /100m", _swim_pace, _clock, lower_is_better=True),
            _Metric("SWOLF", lambda a: _number(a, "avg_swolf"), _whole, lower_is_better=True),
            _Metric(
                "Strokes / length",
                lambda a: _number(a, "avg_strokes_per_length"),
                lambda v: _num(v, 1),
                lower_is_better=True,
            ),
            _Metric(
                "Distance", lambda a: _number(a, "distance_meters"), _whole, lower_is_better=False
            ),
        ),
    ),
    "running": (
        "runs",
        (
            _Metric("Pace /km", _run_pace, _clock, lower_is_better=True),
            _Metric(
                "Distance",
                lambda a: _number(a, "distance_meters"),
                lambda m: _num(m / 1000, 2),
                lower_is_better=False,
            ),
        ),
    ),
}
"""What each sport trends. Gym has none: the history carries no performed sets."""


def _day_label(activity: Activity) -> str | None:
    start = activity.get("start_time")
    if not start:
        return None
    try:
        return datetime.fromisoformat(str(start)).strftime("%d %b")
    except ValueError:
        return None


def trend_table(activity: Activity | None, history: Sequence[Activity] | None) -> TrendTable | None:
    """Today against the last activity and the best of the lot, per metric.

    ``history`` is the similar activities, newest first. None without history, or
    when the sport has no trend metrics or today has none of their values.
    """
    if not activity or not history:
        return None
    sport = _TREND_METRICS.get(str(activity.get("sport")))
    if sport is None:
        return None
    noun, metrics = sport
    last = history[0]
    rows = []
    for m in metrics:
        today = m.read(activity)
        if today is None:
            continue
        before = m.read(last)
        values = [v for v in (today, *(m.read(a) for a in history)) if v is not None]
        best = min(values) if m.lower_is_better else max(values)
        improved = before is not None and (today < before if m.lower_is_better else today > before)
        rows.append(
            TrendRow(
                m.label,
                m.show(today),
                m.show(before) if before is not None else NO_VALUE,
                m.show(best),
                improved,
            )
        )
    if not rows:
        return None
    return TrendTable(f"Trend · last {len(history) + 1} {noun}", _day_label(last) or "Last", rows)
