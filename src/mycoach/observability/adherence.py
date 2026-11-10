"""Adherence: how a performed session compares to the prescription it fulfils.

Pure formulas, one value per Langfuse score name. A score with nothing to
measure (no prescribed sets, no planned duration, no RPE on one side) is left
out rather than sent as zero, so it never drags an average.

Gym sets match their prescription by Exercise identity (``exercise_id``).
Warmup sets are not performed sets. A performed exercise the plan never
prescribed matches nothing and moves no score; a prescribed exercise never
performed counts as undone sets.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Any

from mycoach.coaching.prescriptions import Prescription
from mycoach.models.plan import PlannedSession, Track, WeeklyPlan
from mycoach.observability.scores import Score, score_timestamp
from mycoach.observability.trace_ids import trace_id, weekly_plan_seed

SET_COMPLETION = "adherence_set_completion"
WEIGHT_DELTA_PCT = "adherence_weight_delta_pct"
REPS_DELTA = "adherence_reps_delta"
RPE_DELTA = "adherence_rpe_delta"
DURATION_RATIO = "adherence_duration_ratio"

_REP_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


@dataclass(frozen=True)
class _Prescribed:
    sets: int
    reps: float | None
    """The target reps: a range's midpoint (``"8-10"`` → 9.0)."""
    weight_kg: float | None
    rpe: float | None


def _target_reps(value: Any) -> float | None:
    """``"8-10"`` → 9.0 (the range's midpoint), ``"8"`` or 8 → 8.0; anything else → None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    match = _REP_RANGE.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    low = int(match.group(1))
    return (low + int(match.group(2) or low)) / 2


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _prescriptions(details: Mapping[str, Any] | None) -> dict[str, _Prescribed]:
    """The plan's prescribed exercises, keyed by exercise id.

    An exercise listed twice adds its sets; its first entry's targets win.
    """
    out: dict[str, _Prescribed] = {}
    for ex in (details or {}).get("exercises") or []:
        exercise_id = ex.get("exercise_id")
        if not exercise_id:
            continue
        sets = ex.get("sets")
        sets = sets if isinstance(sets, int) and not isinstance(sets, bool) and sets > 0 else 0
        earlier = out.get(exercise_id)
        if earlier is not None:
            out[exercise_id] = _Prescribed(
                earlier.sets + sets, earlier.reps, earlier.weight_kg, earlier.rpe
            )
            continue
        out[exercise_id] = _Prescribed(
            sets=sets,
            reps=_target_reps(ex.get("reps")),
            weight_kg=_number(ex.get("target_weight_kg")),
            rpe=_number(ex.get("rpe")),
        )
    return out


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return fmean(values) if values else None


def gym_adherence(
    details: Mapping[str, Any] | None,
    sets: Sequence[Mapping[str, Any]],
    fulfillments: Mapping[tuple[str, int], Prescription] | None = None,
) -> dict[str, float]:
    """The gym adherence scores of ``sets`` against the plan's ``details``.

    A set's own prescription (what the logger showed on that set: the coaching
    fulfillment under the set's ``(exercise_title, set_index)`` in ``fulfillments``)
    wins over the plan's per-exercise targets. The reps delta is the mean of performed minus prescribed reps, a prescribed range
    counting as its midpoint (``"8-10"`` is 9). The weight delta is a
    percentage of the prescribed weight.
    """
    prescriptions = _prescriptions(details)
    total = sum(p.sets for p in prescriptions.values())
    if total == 0:
        return {}

    matched = [s for s in sets if s["set_type"] != "warmup" and s["exercise_id"] in prescriptions]
    done: dict[str, int] = {}
    weight_deltas: list[float] = []
    reps_deltas: list[float] = []
    rpe_deltas: list[float] = []
    for s in matched:
        assert s["exercise_id"] is not None
        p = prescriptions[s["exercise_id"]]
        done[s["exercise_id"]] = done.get(s["exercise_id"], 0) + 1

        own = (fulfillments or {}).get((s["exercise_title"], s["set_index"]), Prescription(None, None))
        target_weight = own.weight_kg if own.weight_kg is not None else p.weight_kg
        if s["weight_kg"] is not None and target_weight:
            weight_deltas.append((s["weight_kg"] - target_weight) / target_weight * 100)

        target_reps = float(own.reps) if own.reps is not None else p.reps
        if s["reps"] is not None and target_reps is not None:
            reps_deltas.append(s["reps"] - target_reps)

        if s["rpe"] is not None and p.rpe is not None:
            rpe_deltas.append(s["rpe"] - p.rpe)

    completed = sum(min(n, prescriptions[ex].sets) for ex, n in done.items())
    values = {SET_COMPLETION: completed / total}
    for name, deltas in (
        (WEIGHT_DELTA_PCT, weight_deltas),
        (REPS_DELTA, reps_deltas),
        (RPE_DELTA, rpe_deltas),
    ):
        mean = _mean(deltas)
        if mean is not None:
            values[name] = mean
    return values


def cardio_adherence(
    *, planned_minutes: int | None, actual_minutes: int | float | None
) -> dict[str, float]:
    """The cardio adherence score: actual over planned duration."""
    if not planned_minutes or actual_minutes is None:
        return {}
    return {DURATION_RATIO: actual_minutes / planned_minutes}


def adherence_scores(
    *,
    plan: WeeklyPlan,
    planned: PlannedSession,
    activity: Mapping[str, Any],
    sets: Sequence[Mapping[str, Any]],
    gym_slot: int | None,
    now: datetime,
    fulfillments: Mapping[tuple[str, int], Prescription] | None = None,
) -> list[Score]:
    """The adherence scores of ``planned`` fulfilled by ``activity``, on the weekly-plan trace.

    Each score's ID names the planned session, so fulfilling it again (a
    re-link, a repeated import) overwrites. The timestamp is the plan's
    week, a stable day for that overwrite. A gym score also names the slot's
    gym-adjustment generation (``gym_slot``, the slot's index in the plan
    run) as ``gym_adjustment:<slot index>``, the qualifier the code-check
    scores of that generation use.
    """
    if planned.track == Track.GYM:
        details = json.loads(planned.details) if planned.details else None
        values = gym_adherence(details, sets, fulfillments)
    else:
        values = cardio_adherence(
            planned_minutes=planned.duration_minutes, actual_minutes=activity.get("duration_minutes")
        )
    metadata: dict[str, Any] = {
        "planned_session_id": planned.id,
        "activity_external_id": activity.get("external_id"),
        "track": planned.track,
        "day_of_week": planned.day_of_week,
    }
    if planned.track == Track.GYM and gym_slot is not None:
        metadata["generation"] = f"gym_adjustment:{gym_slot}"
    trace = trace_id(weekly_plan_seed(plan.id))
    at = score_timestamp(plan.week_start, now)
    return [
        Score.numeric(
            trace,
            name,
            value,
            timestamp=at,
            qualifier=f"planned_session:{planned.id}",
            metadata=metadata,
        )
        for name, value in values.items()
    ]
