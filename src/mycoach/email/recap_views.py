"""Code-built views of the weekly recap email on the v5 contract (#242).

Every number here comes from the data: the recovery tiles from the week's health
snapshots, the top sets from its gym log, the load values from Garmin's load focus.
The coach's notes are only joined onto these rows, by the ids the prompt listed; a
note on an id the week does not have is dropped with a warning (spec: "IDs must exist").
"""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from mycoach.coaching.email_schemas import LiftNote, SessionNote
from mycoach.email.tones import Chip, tone_chip
from mycoach.localtime import WEEKDAY_NAMES

logger = logging.getLogger(__name__)


def session_notes(
    notes: Sequence[SessionNote], adherence: Mapping[str, Any] | None
) -> dict[int, str]:
    """The coach's session notes by planned session id, unknown ids dropped."""
    known = {s.get("id") for s in (adherence or {}).get("sessions") or []}
    joined: dict[int, str] = {}
    for note in notes:
        if note.session_id not in known:
            logger.warning("Recap: dropped a note on unknown session_id %s", note.session_id)
            continue
        joined[note.session_id] = note.text
    return joined


# --- Recovery ----------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    """A recovery tile: "Sleep score / 64–81 / Low on Thu 5 Nov"."""

    label: str
    value: str
    unit: str | None
    sub: str


def _day_label(iso_day: str) -> str:
    day = date.fromisoformat(iso_day)
    return f"{WEEKDAY_NAMES[day.weekday()][:3]} {day.day} {day:%b}"


def _range(values: Sequence[float]) -> str:
    low, high = min(values), max(values)
    return f"{low:g}" if low == high else f"{low:g}–{high:g}"


def _readings(n: int) -> str:
    return f"{n} reading{'s' if n != 1 else ''} this week"


def recovery_tiles(snapshots: Iterable[Mapping[str, Any]] | None) -> list[Tile]:
    """The week's sleep score and resting HR as ranges, each tile left out without data."""
    days = sorted(snapshots or [], key=lambda s: str(s.get("snapshot_date")))
    tiles = []
    sleep = [(s["snapshot_date"], s["sleep_score"]) for s in days if s.get("sleep_score")]
    if sleep:
        values = [v for _, v in sleep]
        low_day = min(sleep, key=lambda d: d[1])[0]
        sub = f"Low on {_day_label(low_day)}" if len(set(values)) > 1 else _readings(len(sleep))
        tiles.append(Tile("Sleep score", _range(values), None, sub))
    rhr = [s["resting_hr"] for s in days if s.get("resting_hr")]
    if rhr:
        tiles.append(Tile("Resting HR", _range(rhr), "bpm", _readings(len(rhr))))
    return tiles


# --- Strength ----------------------------------------------------------------------


@dataclass(frozen=True)
class LiftRow:
    """A catalogue exercise's top set this week, with the coach's trend note if any."""

    exercise_id: str
    name: str
    top_set: str
    trend: str | None = None
    chip: Chip | None = None


def _kg(weight: float) -> str:
    return f"{weight:g} kg"


def _top_set(s: Mapping[str, Any]) -> str:
    """ "100 kg × 5 @8"; a set without a load is its reps ("12 reps")."""
    if s.get("weight_kg") is not None and s.get("reps") is not None:
        text = f"{_kg(s['weight_kg'])} × {s['reps']}"
    elif s.get("reps") is not None:
        text = f"{s['reps']} reps"
    else:
        text = _kg(s["weight_kg"])
    if s.get("rpe") is not None:
        text += f" @{s['rpe']:g}"
    return text


def _heavier(s: Mapping[str, Any]) -> tuple[float, int]:
    return (s.get("weight_kg") or 0.0, s.get("reps") or 0)


def lift_rows(
    sets: Iterable[Mapping[str, Any]] | None, notes: Sequence[LiftNote] = ()
) -> list[LiftRow]:
    """The top working set of each catalogue exercise, in the order first done.

    Warm-up sets never count; a custom exercise (no Exercise ID) is left out, as
    the prompt gave the coach nothing to note it by.
    """
    top: dict[str, Mapping[str, Any]] = {}
    for s in sets or []:
        exercise_id = s.get("exercise_id")
        if not exercise_id or s.get("set_type") == "warmup":
            continue
        if s.get("weight_kg") is None and s.get("reps") is None:
            continue
        if exercise_id not in top or _heavier(s) > _heavier(top[exercise_id]):
            top[exercise_id] = s
    by_id: dict[str, LiftNote] = {}
    for note in notes:
        if note.exercise_id not in top:
            logger.warning("Recap: dropped a lift note on unknown exercise_id %s", note.exercise_id)
            continue
        by_id[note.exercise_id] = note
    rows = []
    for exercise_id, s in top.items():
        lift_note = by_id.get(exercise_id)
        rows.append(
            LiftRow(
                exercise_id=exercise_id,
                name=str(s.get("exercise_title") or exercise_id),
                top_set=_top_set(s),
                trend=lift_note.trend if lift_note else None,
                chip=tone_chip(lift_note.tone, lift_note.trend) if lift_note else None,
            )
        )
    return rows


# --- Load focus --------------------------------------------------------------------

_LOAD_FOCUS = (
    ("aerobic_low", "Low aerobic"),
    ("aerobic_high", "High aerobic"),
    ("anaerobic", "Anaerobic"),
)


@dataclass(frozen=True)
class LoadBar:
    """A bar drawn only against a target range: fill and band as percentages."""

    fill_pct: int
    band_start_pct: int
    band_width_pct: int
    in_range: bool


@dataclass(frozen=True)
class LoadRow:
    key: str
    label: str
    value: str
    target: str | None = None
    bar: LoadBar | None = None


def _bar(value: float, low: float, high: float) -> LoadBar:
    scale = max(value, high) * 1.15 or 1.0
    return LoadBar(
        fill_pct=round(100 * value / scale),
        band_start_pct=round(100 * low / scale),
        band_width_pct=max(1, round(100 * (high - low) / scale)),
        in_range=low <= value <= high,
    )


def _load_focus(snapshots: Iterable[Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    """The week's latest Garmin load focus (it is a 4-week figure, so the latest wins)."""
    for s in sorted(snapshots or [], key=lambda s: str(s.get("snapshot_date")), reverse=True):
        raw = s.get("load_focus")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(data, Mapping):
            return data
    return None


def load_rows(snapshots: Iterable[Mapping[str, Any]] | None) -> list[LoadRow]:
    """Low aerobic, high aerobic and anaerobic load; a bar only with a target range."""
    focus = _load_focus(snapshots)
    if focus is None:
        return []
    rows = []
    for key, label in _LOAD_FOCUS:
        value = focus.get(key)
        if value is None:
            continue
        low, high = focus.get(f"{key}_target_min"), focus.get(f"{key}_target_max")
        target = bar = None
        if low is not None and high is not None:
            target, bar = f"target {low:.0f}–{high:.0f}", _bar(value, low, high)
        rows.append(LoadRow(key, label, f"{value:.0f}", target, bar))
    return rows


# --- Setup & system ----------------------------------------------------------------


@dataclass(frozen=True)
class SetupNotice:
    label: str
    text: str
    link_url: str
    link_text: str
    tone: str = "neutral"


def setup_notices(
    base_url: str, *, has_mesocycle: bool | None, plan_failed: bool
) -> list[SetupNotice]:
    """App setup and system problems, each with the link that fixes or explains it.

    ``has_mesocycle`` None (unread) says nothing rather than claim there is none.
    """
    notices = []
    if has_mesocycle is False:
        notices.append(
            SetupNotice(
                "No block set",
                "Progression is generic until a mesocycle exists.",
                f"{base_url}/mesocycles",
                "Set one up →",
            )
        )
    if plan_failed:
        notices.append(
            SetupNotice(
                "Plan failed",
                "Next week's plan failed to generate.",
                f"{base_url}/api/system/jobs/weekly_plan/runs",
                "See the job log →",
                "watch",
            )
        )
    return notices
