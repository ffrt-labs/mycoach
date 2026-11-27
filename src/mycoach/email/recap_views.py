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
from datetime import date, timedelta
from typing import Any

from mycoach.coaching.email_schemas import LiftNote, SessionNote
from mycoach.coaching.life_context import (
    STATUS_LABEL,
    Episode,
    excused_reasons,
    excused_summary,
    format_days,
    ramp_back_length,
)
from mycoach.config import Settings
from mycoach.email.life_views import EQUIPMENT_LABEL
from mycoach.email.links import prefill_url
from mycoach.email.tones import SESSION_STATUS_CHIP, Chip, tone_chip
from mycoach.email.workout_stats import Tile
from mycoach.localtime import WEEKDAY_NAMES
from mycoach.models.life_context import EpisodeKind, IllnessSeverity
from mycoach.models.plan import INTENT_LABEL
from mycoach.sport_display import sport_display

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
        tiles.append(Tile("Sleep score", _range(values), sub=sub))
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
class BarSegment:
    """A stretch of the load bar between two of its edges (track, band, value).

    ``layer`` is what lies behind it ("track" or "band"); ``filled`` is whether the
    value's fill runs through it, in front; ``fill_end`` marks where the fill stops.
    """

    width_pct: int
    layer: str
    filled: bool
    fill_end: bool = False


@dataclass(frozen=True)
class LoadBar:
    """A bar drawn only against a target range: one track, the target band behind
    the value, the value's fill in front. Its segments' widths add up to 100."""

    segments: tuple[BarSegment, ...]
    in_range: bool


@dataclass(frozen=True)
class LoadRow:
    key: str
    label: str
    value: str
    target: str | None = None
    bar: LoadBar | None = None


_SCALE_CAP = 3
"""The load bar's scale ends at most this many times the target's top."""


def _pct(x: float, scale: float) -> int:
    return min(100, max(0, round(100 * x / scale)))


def _bar(value: float, low: float, high: float) -> LoadBar:
    """Cut the track at the band's edges and the value's end, in whole percents.

    Email clients cannot stack the band and the fill, so the bar is drawn as the
    stretches between those edges: each is band or track behind, filled or not in
    front. The band is at least 1% wide, so a zero-width target still shows. The
    scale ends past the larger of value and target, but never past 3× the target's
    top: a far larger value fills the bar rather than squeeze the band to a sliver.
    """
    scale = max(value, high) * 1.15
    if high > 0:
        scale = min(scale, high * _SCALE_CAP)
    scale = scale or 1.0
    fill = _pct(value, scale)
    start = min(_pct(low, scale), 99)
    end = max(_pct(high, scale), start + 1)
    edges = sorted({0, start, end, fill, 100})
    segments = tuple(
        BarSegment(
            width_pct=b - a,
            layer="band" if start <= a < end else "track",
            filled=b <= fill,
            fill_end=b == fill,
        )
        for a, b in zip(edges, edges[1:], strict=False)
    )
    return LoadBar(segments, in_range=low <= value <= high)


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
                f"{base_url}/jobs/weekly_plan",
                "See the job log →",
                "watch",
            )
        )
    return notices


def completion_line(adherence: Mapping[str, Any] | None) -> str | None:
    """The week's completion as the recap email shows it: "4/5 done · 1 substituted".

    Substitutions are already in the done count (#216); excused sessions (#220) are
    out of the denominator: "2/2 done · 3 excused (ill 3 days)". Sessions adapted on
    a trip (#221) are done too: "2/3 done · 1 adapted (travel)". None without a plan.
    """
    if not adherence or not (adherence.get("total_sessions") or adherence.get("excused_sessions")):
        return None
    line = f"{adherence['completed_sessions']}/{adherence['total_sessions']} done"
    for key, status in (
        ("substituted_sessions", "substituted"),
        ("adapted_sessions", "adapted_travel"),
    ):
        if adherence.get(key):
            line += f" · {adherence[key]} {STATUS_LABEL[status]}"
    excused = excused_summary(
        adherence.get("excused_sessions", 0), adherence.get("excused_days") or {}
    )
    if excused:
        line += f" · {excused}"
    return line


@dataclass(frozen=True)
class CompletionHero:
    """The recap hero (#232): "4/5 done" large, "· 1 substituted" beside it."""

    h1: str
    rest: str | None = None
    on_planned_day: str | None = None


def completion_hero(adherence: Mapping[str, Any] | None) -> CompletionHero:
    """Split the week's Completion line for the recap hero.

    The H1 is the line's first segment. A week whose every session was excused
    shows "4 excused" and the reason, never "0/0 done"; no plan, "No plan this
    week". On planned day counts only sessions whose day is known, and is
    informational, never a score.
    """
    line = completion_line(adherence)
    if adherence is None or line is None:
        return CompletionHero("No plan this week")
    if not adherence.get("total_sessions"):
        reasons = excused_reasons(adherence.get("excused_days") or {})
        h1, rest = f"{adherence['excused_sessions']} excused", reasons or None
    else:
        h1, _, rest_line = line.partition(" · ")
        rest = rest_line or None
    known = [
        s["on_planned_day"]
        for s in adherence.get("sessions") or []
        if s.get("on_planned_day") is not None
    ]
    return CompletionHero(
        h1,
        None if rest is None else f"· {rest}",
        f"On planned day {sum(known)}/{len(known)} · informational" if known else None,
    )


_EXCUSED_STATUSES = frozenset({"excused_ill", "excused_travel"})


@dataclass(frozen=True)
class SessionRow:
    """One planned session in the recap's Sessions list (#232), all from the Completion."""

    day: str  # "MON"
    title: str
    meta: str  # "Gym · strength"
    chip: Chip | None
    detail: str | None  # "Done Wednesday", or what filled it
    struck: bool  # excused: out of the count, no action asked
    note: str | None = None  # the coach's session note, joined by session id (#242)


def _weekday_index(day: str) -> int:
    return WEEKDAY_NAMES.index(day) if day in WEEKDAY_NAMES else len(WEEKDAY_NAMES)


def _sport_meta(sport: str, intent: str | None) -> str:
    """ "Gym · strength": the sport, then the Session intent's label when it has one."""
    label = INTENT_LABEL.get(intent) if intent else None
    return sport_display(sport).name + (f" · {label}" if label else "")


def _substitute_line(sub: Mapping[str, Any]) -> str:
    """ "← Padel Sun · 101 min · avg HR 118 · aerobic TE 2.7"; what Garmin didn't record
    is left out."""
    head = f"← {sub['title']}"
    if sub.get("day"):
        head += f" {str(sub['day'])[:3]}"
    parts = [head]
    if sub.get("duration_minutes") is not None:
        parts.append(f"{sub['duration_minutes']} min")
    if sub.get("avg_hr") is not None:
        parts.append(f"avg HR {sub['avg_hr']}")
    if sub.get("training_effect_aerobic") is not None:
        parts.append(f"aerobic TE {sub['training_effect_aerobic']:.1f}")
    return " · ".join(parts)


def session_rows(
    adherence: Mapping[str, Any] | None, notes: Mapping[int, str] | None = None
) -> list[SessionRow]:
    """The week's planned sessions in planned-day order, each with its status chip.

    A session done on another day says which ("Done Wednesday"); a substituted or
    adapted one shows what filled it; an excused one is struck through. ``notes`` are
    the coach's session notes by session id (#242).
    """
    rows = []
    for s in sorted(
        (adherence or {}).get("sessions") or [], key=lambda s: _weekday_index(s["day"])
    ):
        status = s["status"]
        detail = None
        if s.get("substitute"):
            detail = _substitute_line(s["substitute"])
        elif status == "done" and s.get("on_planned_day") is False and s.get("actual_day"):
            detail = f"Done {s['actual_day']}"
        rows.append(
            SessionRow(
                day=str(s["day"])[:3].upper(),
                title=s["title"],
                meta=_sport_meta(s["sport"], s.get("intent")),
                chip=SESSION_STATUS_CHIP.get(status),
                detail=detail,
                struck=status in _EXCUSED_STATUSES,
                note=(notes or {}).get(s.get("id")),
            )
        )
    return rows


@dataclass(frozen=True)
class ExtraRow:
    """An Extra activity in the recap: training load only, never counted."""

    day: str  # "SAT"
    text: str  # "Walk · 45 min · recovery"


def extra_rows(adherence: Mapping[str, Any] | None) -> list[ExtraRow]:
    """The week's Extra activities in the order they were done."""
    rows = []
    for a in sorted((adherence or {}).get("extras") or [], key=lambda a: _weekday_index(a["day"])):
        parts = [str(a["title"])]
        if a.get("duration_minutes") is not None:
            parts.append(f"{a['duration_minutes']} min")
        if a.get("intent") and a["intent"] in INTENT_LABEL:
            parts.append(INTENT_LABEL[a["intent"]])
        rows.append(ExtraRow(str(a["day"])[:3].upper(), " · ".join(parts)))
    return rows


def episode_line(episode: Episode) -> str:
    """One Life context episode touching the recap week.

    "Ill 11–15 Nov · sick · ramp-back to 20 Nov", "Ill since 6 Nov · sick",
    "Trip 9–12 Nov · hotel gym · no training possible".
    """
    head = "Ill" if episode.kind == EpisodeKind.ILLNESS else "Trip"
    if episode.ended_on is None:
        when = f"since {episode.started_on.day} {episode.started_on:%b}"
    else:
        when = format_days(episode.started_on, episode.ended_on)
    parts = [f"{head} {when}"]
    if episode.kind == EpisodeKind.ILLNESS:
        severity = episode.severity or IllnessSeverity.SICK
        parts.append(severity.value)
        if episode.ended_on is not None:
            length = ramp_back_length(severity, (episode.ended_on - episode.started_on).days + 1)
            ends = episode.ended_on + timedelta(days=length)
            parts.append(f"ramp-back to {ends.day} {ends:%b}")
    else:
        if episode.equipment is not None:
            parts.append(EQUIPMENT_LABEL[episode.equipment].lower())
        if episode.no_training:
            parts.append("no training possible")
    return " · ".join(parts)


@dataclass(frozen=True)
class QuickReply:
    """One cause quick-reply button: a GET that only opens a prefilled form."""

    label: str
    sub: str
    url: str


@dataclass(frozen=True)
class QuickReplies:
    """The recap's "What happened…?" block (#237)."""

    question: str
    replies: list[QuickReply]


def _between(first: date, last: date) -> str:
    """ "between 9 and 15 Nov", "between 30 Oct and 3 Nov", "on 12 Nov"."""
    if first == last:
        return f"on {first.day} {first:%b}"
    if (first.year, first.month) == (last.year, last.month):
        return f"between {first.day} and {last.day} {last:%b}"
    return f"between {first.day} {first:%b} and {last.day} {last:%b}"


# Health snapshots missing for this many days of the week point at the watch.
_HEALTH_GAP_DAYS = 2


def quick_replies(
    adherence: Mapping[str, Any] | None,
    episodes: Sequence[Episode] | None,
    week_start: date,
    today: date,
    settings: Settings,
    health_days_missing: int | None = None,
    garmin_auth_failed: bool = False,
) -> QuickReplies | None:
    """Ask what happened in a week that went quiet, or None when the block stays out.

    Shown when the week isn't complete, no Life context episode touches it, and
    either nothing was done or health data is missing for two days or more.
    Unread episodes or health data count as unknown, never as "none".

    The prefill range runs from the first to the last not-done session's planned day:
    the narrowest range that explains the gap, so accepting the default excuses
    nothing the user didn't mean. Illness can't end later than yesterday. The
    watch button needs the health gap, and reads "Reconnect Garmin" when the last
    sync failed to log in.
    """
    if adherence is None or episodes is None or episodes:
        return None
    if adherence.get("completed_sessions", 0) >= adherence.get("total_sessions", 0):
        return None
    health_gap = health_days_missing is not None and health_days_missing >= _HEALTH_GAP_DAYS
    if adherence.get("completed_sessions", 0) > 0 and not health_gap:
        return None
    not_done = sorted(
        week_start + timedelta(days=_weekday_index(s["day"]))
        for s in adherence.get("sessions") or []
        if s.get("status") == "not_done" and s.get("day") in WEEKDAY_NAMES
    )
    if not not_done:
        return None
    first, last = not_done[0], not_done[-1]
    replies = []
    ill_to = min(last, today - timedelta(days=1))
    if ill_to >= first:
        replies.append(
            QuickReply(
                "I was ill", "log past illness", prefill_url(settings, "illness", first, ill_to)
            )
        )
    replies.append(
        QuickReply("I was travelling", "log the trip", prefill_url(settings, "travel", first, last))
    )
    if health_gap:
        # The dashboard's Garmin sync button; the link itself never syncs or logs in.
        label = "Reconnect Garmin" if garmin_auth_failed else "Watch didn't sync"
        replies.append(QuickReply(label, "check sources", f"{settings.app_base_url}/"))
    return QuickReplies(f"What happened {_between(first, last)}?", replies)
