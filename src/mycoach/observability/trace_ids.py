"""Deterministic Langfuse trace IDs and week sessions for coaching runs.

Every trace ID is derived from MyCoach's own IDs and natural keys, so a later
signal (a Langfuse score, the trace-coverage script) can find its trace
without MyCoach ever storing a Langfuse ID. This module is the one place the
seeds are spelled out: rebuild IDs by calling these functions, never by
copying the format strings.
"""

from datetime import UTC, date, datetime, timedelta

from langfuse import Langfuse


def weekly_plan_seed(plan_id: int) -> str:
    return f"mycoach:weekly-plan:{plan_id}"


def daily_briefing_seed(user_id: int, day: date) -> str:
    return f"mycoach:daily-briefing:{user_id}:{day.isoformat()}"


def post_workout_seed(activity_id: int) -> str:
    """``activity_id`` is the canonical activity's id."""
    return f"mycoach:post-workout:{activity_id}"


def weekly_recap_seed(user_id: int, week_start: date) -> str:
    return f"mycoach:weekly-recap:{user_id}:{week_start.isoformat()}"


_REGEN_STAMP = "%Y%m%dT%H%M%SZ"


def regen_seed(seed: str, at: datetime) -> str:
    """The seed of a forced regeneration of the run ``seed`` names, made at ``at``.

    A naive ``at`` is taken to be UTC.
    """
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    return f"{seed}:regen:{at:{_REGEN_STAMP}}"


def is_regen_of(candidate: str, seed: str) -> bool:
    """Is ``candidate`` the seed of a forced regeneration of ``seed``?

    The regeneration time is not stored anywhere, so a regen seed cannot be
    rebuilt; this recognises one instead (the trace-coverage check uses it).
    """
    prefix = f"{seed}:regen:"
    if not candidate.startswith(prefix):
        return False
    try:
        datetime.strptime(candidate.removeprefix(prefix), _REGEN_STAMP)
    except ValueError:
        return False
    return True


def trace_id(seed: str) -> str:
    """The 32-hex Langfuse trace ID for ``seed``: ``sha256(seed)[:16]``.

    The SDK's own helper, which needs no client or keys. It is the same scheme
    Agregado uses, so IDs can be recomputed from any language.
    """
    return Langfuse.create_trace_id(seed=seed)


def week_session_id(user_id: int, day: date) -> str:
    """The Langfuse session for the training week ``day`` falls in.

    Pass the date the run is *about* (the recapped week, the activity's day),
    not the time it ran.
    """
    monday = day - timedelta(days=day.weekday())
    return f"mycoach:week:{user_id}:{monday.isoformat()}"
