"""Substitution (#216): an unplanned activity that delivered a leftover session's intent.

Every threshold the classifier and the volume gate use is a named constant here.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mycoach.models.plan import SessionIntent

# intervals: at least this share of time in Z5, or an anaerobic training effect of this.
INTERVALS_MIN_Z5_SHARE = 0.10
INTERVALS_MIN_ANAEROBIC_TE = 2.5
# threshold: more than this share of time in Z3+Z4 (a majority).
THRESHOLD_MIN_Z3_Z4_SHARE = 0.50
# recovery: at least this share in Z1, with an aerobic training effect below this.
RECOVERY_MIN_Z1_SHARE = 0.60
RECOVERY_MAX_AEROBIC_TE = 2.0
# aerobic_base: this share of time in Z2, or in Z1+Z2 when Z2 alone falls short.
AEROBIC_BASE_MIN_ZONE_SHARE = 0.60
# ... with an anaerobic training effect below this.
AEROBIC_BASE_MAX_ANAEROBIC_TE = 2.0


def _zone_minutes(hr_zones: str | None) -> dict[int, float]:
    """Minutes per zone (1..5) from the stored ``hr_zones`` JSON. Empty when unusable."""
    if not hr_zones:
        return {}
    try:
        zones = json.loads(hr_zones)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(zones, list):
        return {}
    out: dict[int, float] = {}
    for z in zones:
        if not isinstance(z, dict):
            continue
        number = z.get("zoneNumber") or z.get("zone")
        minutes = z.get("minutes")
        if minutes is None and z.get("secsInZone") is not None:
            minutes = z["secsInZone"] / 60
        if isinstance(number, int) and 1 <= number <= 5 and isinstance(minutes, (int, float)):
            out[number] = out.get(number, 0.0) + float(minutes)
    return out


def classify_activity_intent(activity: Mapping[str, Any]) -> SessionIntent | None:
    """The training intent an activity delivered, or None when it can't be told.

    ``activity`` carries the health facts: ``sport``, ``has_performed_sets``,
    ``hr_zones`` (Garmin's JSON list of ``{"zoneNumber", "secsInZone"}``),
    ``training_effect_aerobic`` and ``training_effect_anaerobic``. A gym session
    with performed sets is strength. Everything else is read from time in HR
    zones, hardest rule first; with no zone data it is unclassifiable (None),
    and so is a session no rule describes. Skill is never classified: it can
    only be done, not substituted.
    """
    if activity.get("sport") == "gym" and activity.get("has_performed_sets"):
        return SessionIntent.STRENGTH
    minutes = _zone_minutes(activity.get("hr_zones"))
    total = sum(minutes.values())
    if total <= 0:
        return None
    share = {z: minutes.get(z, 0.0) / total for z in range(1, 6)}
    aerobic_te = activity.get("training_effect_aerobic") or 0.0
    anaerobic_te = activity.get("training_effect_anaerobic") or 0.0
    if share[5] >= INTERVALS_MIN_Z5_SHARE or anaerobic_te >= INTERVALS_MIN_ANAEROBIC_TE:
        return SessionIntent.INTERVALS
    if share[3] + share[4] > THRESHOLD_MIN_Z3_Z4_SHARE:
        return SessionIntent.THRESHOLD
    if share[1] >= RECOVERY_MIN_Z1_SHARE and aerobic_te < RECOVERY_MAX_AEROBIC_TE:
        return SessionIntent.RECOVERY
    if (
        share[2] >= AEROBIC_BASE_MIN_ZONE_SHARE
        or share[1] + share[2] >= AEROBIC_BASE_MIN_ZONE_SHARE
    ) and anaerobic_te < AEROBIC_BASE_MAX_ANAEROBIC_TE:
        return SessionIntent.AEROBIC_BASE
    return None


# Volume gate: a substitute lasts at least this share of the planned duration.
MIN_VOLUME_RATIO = 0.70

IntentMatch = Callable[[Mapping[str, Any], SessionIntent], bool]


def delivers_intent(activity: Mapping[str, Any], intent: SessionIntent) -> bool:
    """The default match: the activity's classified intent is the session's intent."""
    return classify_activity_intent(activity) is intent


@dataclass(frozen=True)
class SubstitutionRules:
    """What a substitute must meet for one planned session.

    The default applies to every session. A relaxation (e.g. a travel week, #221)
    is a different instance for the days it covers, handed to
    ``get_plan_adherence_for_week`` through ``rules_for_day``.

    ``min_minutes`` replaces the volume ratio for the intents it names with an
    absolute floor (travel: 20 min of resistance work fills a strength slot).
    """

    min_volume_ratio: float = MIN_VOLUME_RATIO
    matches_intent: IntentMatch = delivers_intent
    min_minutes: Mapping[SessionIntent, int] = field(default_factory=dict)


DEFAULT_RULES = SubstitutionRules()

# Travel (#221): this much bodyweight or hotel resistance work fills a strength slot.
TRAVEL_STRENGTH_MIN_MINUTES = 20


def is_resistance_work(activity: Mapping[str, Any]) -> bool:
    """A gym-sport activity (a Logger entry, a Garmin strength activity) of any kind.

    With performed sets it already classifies as strength; without them it is
    the hotel-room circuit the travel relaxation accepts.
    """
    return activity.get("sport") == "gym"


def delivers_intent_travelling(activity: Mapping[str, Any], intent: SessionIntent) -> bool:
    """The travel match: the default, or resistance work without sets for strength."""
    if delivers_intent(activity, intent):
        return True
    return intent is SessionIntent.STRENGTH and is_resistance_work(activity)


TRAVEL_RULES = SubstitutionRules(
    matches_intent=delivers_intent_travelling,
    min_minutes={SessionIntent.STRENGTH: TRAVEL_STRENGTH_MIN_MINUTES},
)
"""The relaxed rules for a session planned on a travel day (Adapted (travel))."""

# Which rules apply to the session planned on a given day.
RulesForDay = Callable[[date], SubstitutionRules]


def can_substitute(
    activity: Mapping[str, Any],
    intent: str | None,
    planned_minutes: int | None,
    rules: SubstitutionRules = DEFAULT_RULES,
) -> bool:
    """Whether ``activity`` can fill a leftover session of this intent and duration.

    A session with no intent (or one this version doesn't know) is never
    substitutable. With no planned duration there is no volume to gate on.
    """
    try:
        slot_intent = SessionIntent(intent) if intent is not None else None
    except ValueError:
        slot_intent = None
    if slot_intent is None:
        return False
    done = activity.get("duration_minutes")
    floor = rules.min_minutes.get(slot_intent)
    if floor is not None:
        if done is None or done < floor:
            return False
    elif planned_minutes and (done is None or done < rules.min_volume_ratio * planned_minutes):
        return False
    return rules.matches_intent(activity, slot_intent)
