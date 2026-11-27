"""The rule that decides whether a day's health data can support a briefing.

Lives in its own module rather than inside the scheduler job that first needed
it, because three callers have to agree on the answer: the retry loop, the
manual generate endpoint, and the dashboard button that offers the manual
endpoint. When the rule lived in the job, the manual path bypassed it — for six
days the dashboard invited the user to press a button that would have produced a
briefing from a snapshot carrying nothing but a display name.
"""

from typing import Any

# The fields that make a briefing about recovery rather than about nothing —
# sleep, HRV, and Body Battery. A snapshot can carry *some* content (resting HR,
# steps) while holding none of these, which is not "no data" but is still
# nothing to coach from.
RECOVERY_FIELDS = (
    "sleep_duration_minutes",
    "sleep_score",
    "hrv_status",
    "hrv_7day_avg",
    "hrv_status_text",
    "body_battery_morning",
)


def missing_recovery_data(health_today: dict[str, Any]) -> str | None:
    """Why today's health data cannot support a briefing, or None if it can.

    Returns a sentence naming what is absent, so every caller reports the same
    reason: the run's ``skip_reason``, the 422 body, and the dashboard banner.
    """
    if any(health_today.get(field) is not None for field in RECOVERY_FIELDS):
        return None
    if not health_today:
        return "no Garmin health snapshot for today — nothing to base a briefing on"
    return (
        "today's Garmin health snapshot carries no sleep, HRV, or Body Battery "
        "data — nothing to base a briefing on"
    )
