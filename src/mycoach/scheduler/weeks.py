"""The scheduler's user and the weeks its plan and recap jobs are for.

Shared by the jobs and by ``mycoach.tools.trace_coverage``, which rebuilds a
run's key from the day it ran, so the two cannot drift apart.
"""

from datetime import date, timedelta

SCHEDULER_USER_ID = 1
"""The single user the scheduled jobs coach (single-user MVP)."""


def plan_week_start(today: date) -> date:
    """The week a plan generated on ``today`` is for: the next Monday, a week
    ahead when ``today`` is a Monday."""
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def recap_week_start(today: date) -> date:
    """The week a recap generated on ``today`` covers: the Monday of the week
    before ``today``'s."""
    return today - timedelta(days=today.weekday() + 7)
