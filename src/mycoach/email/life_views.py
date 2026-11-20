"""Code-rendered views of Open this week and the Life context banner (#233).

Each builder turns a domain value into what a template prints: wording, chips and
links are decided here, never by the LLM and never re-typed in a template. The
briefing uses both; the post-workout email (#235) reuses Open this week.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from mycoach.coaching.context import OpenThisWeek, PlanAdherence
from mycoach.coaching.life_context import (
    DayEffect,
    Episode,
    LifeContext,
    RampBack,
    RecoverySignals,
    effect_on,
    is_jet_lag,
    travel_on,
)
from mycoach.email.tones import LIFE_CONTEXT_STYLE, SESSION_STATUS_CHIP, Chip, intent_chip
from mycoach.models.life_context import EpisodeKind, Equipment
from mycoach.models.plan import INTENT_LABEL

OVERDUE_CHIP = Chip("Planned day passed", "watch")
TODAY_CHIP = Chip("Today", "neutral")


@dataclass(frozen=True)
class OpenRow:
    day: str
    """The planned weekday, abbreviated: "Mon"."""
    title: str
    detail: str
    """ "Gym · strength · 75 min": whichever of the three the session has."""
    chip: Chip

    @property
    def compact(self) -> Chip:
        """The row as one chip, for the post-workout's compact list (#235): "Squat Day ·
        planned day passed" in the watch tone when overdue, else the bare title."""
        if self.chip == OVERDUE_CHIP:
            return Chip(f"{self.title} · {OVERDUE_CHIP.label.lower()}", OVERDUE_CHIP.style)
        return Chip(self.title, "neutral")


@dataclass(frozen=True)
class OpenWeekView:
    rows: list[OpenRow]
    days_remaining: int

    @property
    def summary(self) -> str:
        days = self.days_remaining
        return f"{len(self.rows)} left · {days} day{'s' if days != 1 else ''} remaining"

    @property
    def short_summary(self) -> str:
        """ "4 left · 5 days", the compact list's count."""
        days = self.days_remaining
        return f"{len(self.rows)} left · {days} day{'s' if days != 1 else ''}"

    @property
    def overloaded(self) -> bool:
        """More sessions open than days left to do them in."""
        return len(self.rows) > self.days_remaining


def open_week_view(open_week: OpenThisWeek | None) -> OpenWeekView | None:
    """Open this week as rows, or None (no plan this week: the block is left out)."""
    if open_week is None:
        return None
    rows = []
    for s in open_week["sessions"]:
        day = s["day"][:3]
        intent = s["intent"]
        detail = [s["sport"].capitalize()]
        if intent:
            detail.append(INTENT_LABEL.get(intent, intent))
        if s["duration_minutes"]:
            detail.append(f"{s['duration_minutes']} min")
        chip = {"overdue": OVERDUE_CHIP, "today": TODAY_CHIP}.get(s["status"], Chip(day, "neutral"))
        rows.append(
            OpenRow(
                day=day,
                title=s["title"],
                detail=" · ".join(detail),
                chip=chip,
            )
        )
    return OpenWeekView(rows=rows, days_remaining=open_week["days_remaining"])


# --- Post-workout week status (#235) ---------------------------------------------------


@dataclass(frozen=True)
class WeekStatus:
    """What one activity did for its week, as the post-workout email states it:
    "[Done] Completes Tue · Aerobic base swim + technique [aerobic base]"."""

    chip: Chip
    verb: str
    """ "Completes" or "Substitutes"; an Extra activity's whole line."""
    target: str | None = None
    """ "Tue · Aerobic base swim + technique": the planned session it counted for."""
    intent: Chip | None = None


EXTRA_STATUS = WeekStatus(SESSION_STATUS_CHIP["extra"], "Training load only, not counted")

_STATUS_VERB: dict[str, str] = {
    "done": "Completes",
    "substituted": "Substitutes",
    "adapted_travel": "Substitutes",
}


def week_status(
    week: PlanAdherence | None, activity: Mapping[str, Any] | None
) -> WeekStatus | None:
    """The activity's place in its week's Completion, or None (no plan that week, or
    the Completion doesn't name the activity)."""
    if week is None or activity is None:
        return None
    external_id = activity.get("external_id")
    for s in week["sessions"]:
        if s.get("done_by") == external_id and s["status"] in _STATUS_VERB:
            return WeekStatus(
                chip=SESSION_STATUS_CHIP[s["status"]],
                verb=_STATUS_VERB[s["status"]],
                target=f"{s['day'][:3]} · {s['title']}",
                intent=intent_chip(s["intent"]),
            )
    if any(a["external_id"] == external_id for a in week["extras"]):
        return EXTRA_STATUS
    return None


# --- Life context banner --------------------------------------------------------------

EQUIPMENT_LABEL: dict[str, str] = {
    Equipment.NONE: "No equipment",
    Equipment.HOTEL_GYM: "Hotel gym",
    Equipment.FULL_GYM: "Full gym",
}
"""How the banner names a trip's equipment: "Hotel gym"."""


@dataclass(frozen=True)
class Banner:
    """The Life context banner at the top of the briefing: one per email at most."""

    style: str
    """A ``CHIP_STYLES`` name, from ``LIFE_CONTEXT_STYLE``."""
    kicker: str
    title: str
    body: str | None = None
    link_label: str | None = None
    segments: list[bool] | None = None
    """The ramp-back bar: one per ramp-back day, True up to today."""


def _day(d: date) -> str:
    """ "Wed 18 Nov"."""
    return f"{d:%a} {d.day} {d:%b}"


def _tz_text(shift: float | None) -> str | None:
    """ "6 h ahead of home", worded like the dashboard's ``tz_text``."""
    if not shift:
        return None
    return f"{abs(shift):g} h {'behind' if shift < 0 else 'ahead of'} home"


def _extend_text(signals: RecoverySignals) -> str:
    parts = []
    if signals.hrv_below_baseline:
        parts.append("HRV still below your range")
    if signals.resting_hr_elevated:
        parts.append("resting HR still above your range")
    text = " and ".join(parts)
    return f"{text[0].upper()}{text[1:]}: consider one more easy day."


def _illness_banner(life: LifeContext, effect: DayEffect) -> Banner:
    illness = max(
        (
            e
            for e in life.episodes
            if e.kind == EpisodeKind.ILLNESS and e.covers(life.today, life.today)
        ),
        key=lambda e: (e.started_on, e.id),
    )
    day = (life.today - illness.started_on).days + 1
    if effect == DayEffect.SICK:
        return Banner(
            style=LIFE_CONTEXT_STYLE[effect],
            kicker="Ill · sick",
            title=f"Ill since {_day(illness.started_on)} · day {day}",
            body="Rest today. Nothing to make up — sick days are excused.",
            link_label="Feeling better →",
        )
    return Banner(
        style=LIFE_CONTEXT_STYLE[effect],
        kicker="Ill · mild",
        title=f"Mild cold · day {day}",
        body="Light and short at most, or rest.",
        link_label="Update →",
    )


def _ramp_back_banner(ramp: RampBack) -> Banner:
    return Banner(
        style=LIFE_CONTEXT_STYLE["ramp_back"],
        kicker="Ramp-back · last day" if ramp.day == ramp.length else "Ramp-back",
        title=(
            f"Returning from illness · day {ramp.day}/{ramp.length}"
            f" · ~{ramp.load_pct}% of normal load"
        ),
        body=(
            _extend_text(ramp.extend_because)
            if ramp.extend_because is not None
            else "Keep today at or under that load."
        ),
        segments=[i < ramp.day for i in range(ramp.length)],
    )


def _travel_banner(trip: Episode) -> Banner:
    parts = [
        EQUIPMENT_LABEL.get(trip.equipment) if trip.equipment else None,
        _tz_text(trip.tz_shift_hours),
        f"back {_day(trip.ended_on)}" if trip.ended_on else None,
    ]
    jet_lag = is_jet_lag(trip.tz_shift_hours)
    return Banner(
        style=LIFE_CONTEXT_STYLE[DayEffect.TRAVEL],
        kicker="Travelling",
        title=" · ".join(p for p in parts if p) or "Travelling",
        body="Sessions adapted to the equipment"
        + ("; jet-lag principle applies today." if jet_lag else "."),
        link_label="Edit trip →",
    )


def life_chip(life: LifeContext | None) -> Chip | None:
    """The chip beside the post-workout verdict on a ramp-back or travel day (#235):
    "Ramp-back day 2/5", "Travelling · hotel gym". None on any other day.

    ``life.today`` is the activity's day. A ramp-back outranks a trip, as in the banner.
    """
    if life is None:
        return None
    if life.ramp_back is not None:
        ramp = life.ramp_back
        return Chip(f"Ramp-back day {ramp.day}/{ramp.length}", LIFE_CONTEXT_STYLE["ramp_back"])
    trip = travel_on(life.episodes, life.today, today=life.today)
    if trip is None:
        return None
    label = "Travelling"
    if trip.equipment:
        label += f" · {EQUIPMENT_LABEL[trip.equipment].lower()}"
    return Chip(label, LIFE_CONTEXT_STYLE[DayEffect.TRAVEL])


def life_banner(life: LifeContext | None) -> Banner | None:
    """Today's Life context banner, or None (an ordinary day, or no Life context).

    Overlapping episodes show only the most restrictive effect (``RESTRICTIVENESS``).
    A ramp-back only follows an illness that has ended, so it never meets one; it
    outranks an ordinary travel day, since it limits the load the trip only adapts.
    """
    if life is None or life.is_empty:
        return None
    effect = effect_on(life.episodes, life.today, today=life.today)
    if effect in (DayEffect.SICK, DayEffect.MILD):
        assert effect is not None
        return _illness_banner(life, effect)
    if effect == DayEffect.TRAVEL_NO_TRAINING:
        return Banner(
            style=LIFE_CONTEXT_STYLE[effect],
            kicker="Travelling · no training",
            title="No training possible today",
            body="Rest. Today can excuse one not-done session; nothing to make up.",
            link_label="Back home →",
        )
    if life.ramp_back is not None:
        return _ramp_back_banner(life.ramp_back)
    trip = travel_on(life.episodes, life.today, today=life.today)
    return _travel_banner(trip) if trip is not None else None
