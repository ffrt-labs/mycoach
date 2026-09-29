"""One-off cleanup of the row #123 found (map #132, ticket #131).

Releases planned session 41 (Squat Day, week of 2026-09-21) from activity 58 (a
19-second empty "Morning Session" logger import that wrongly claimed it), via
``unlink_planned_session`` (#130), then deletes activity 58 and the stray
``coaching_insights`` row(s) generated from it (a ``post_workout`` note written
before the claim guard existed — its own content calls activity 58 "a logging
error", so it carries no history worth keeping once the activity is gone).
Decision #6 in #123 deferred this until after the Postgres cutover (#107) and
the unlink feature both landed, so row-count verification during the migration
stayed faithful.

A data fix, not a product feature: no API route, no UI button. Run once, inside
the app container, with the app stopped (its checks and the delete are not
locked against a concurrent write from a live app, e.g. a set landing on
activity 58 between the check and the delete):

    python -m mycoach.tools.cleanup_activity_58 [--dry-run]

Refuses to run unless planned session 41 is currently linked to activity 58 and
that activity has no performed sets (the same guard ``link_activity_to_planned_session``
checks) — a safety check against running this against the wrong data or a row
that was already fixed by hand.
"""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import async_session
from mycoach.models.activity import Activity
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession
from mycoach.plan_linking import has_performed_sets, unlink_planned_session

DEFAULT_USER_ID = 1
PLANNED_SESSION_ID = 41
ACTIVITY_ID = 58


async def cleanup(session: AsyncSession) -> bool:
    """Release planned session 41 from activity 58 and delete activity 58.

    Leaves the session uncommitted either way — the caller decides whether to
    commit or roll back. Returns whether the checks passed and the change was
    made (flushed, not committed).
    """
    planned = (
        await session.execute(select(PlannedSession).where(PlannedSession.id == PLANNED_SESSION_ID))
    ).scalar_one_or_none()
    if planned is None:
        print(f"ABORT: planned session {PLANNED_SESSION_ID} does not exist.")
        return False
    if planned.activity_id != ACTIVITY_ID:
        print(
            f"ABORT: planned session {PLANNED_SESSION_ID} is linked to activity "
            f"{planned.activity_id!r}, not {ACTIVITY_ID}. Already fixed, or wrong data."
        )
        return False
    if await has_performed_sets(session, ACTIVITY_ID):
        print(f"ABORT: activity {ACTIVITY_ID} has performed sets — not the empty row #123 found.")
        return False

    updated = await unlink_planned_session(session, DEFAULT_USER_ID, PLANNED_SESSION_ID)
    if updated is None:
        print(f"ABORT: session {PLANNED_SESSION_ID} does not belong to user {DEFAULT_USER_ID}.")
        return False

    activity = await session.get(Activity, ACTIVITY_ID)
    if activity is None:
        print(f"ABORT: activity {ACTIVITY_ID} does not exist.")
        return False

    insight_stmt = select(CoachingInsight).where(CoachingInsight.activity_id == ACTIVITY_ID)
    insights = (await session.execute(insight_stmt)).scalars().all()
    for insight in insights:
        await session.delete(insight)
    # Flush now, before deleting the activity: there's no ORM `relationship()`
    # between CoachingInsight and Activity, so the unit of work has no
    # dependency info to order the two DELETEs itself, and Postgres checks the
    # FK per statement — deleting the activity first fails even though both
    # deletes are in the same transaction.
    await session.flush()

    await session.delete(activity)
    await session.flush()

    print(
        f"Released planned session {PLANNED_SESSION_ID} (completed=False, activity_id=None), "
        f"deleted {len(insights)} coaching insight(s), and deleted activity {ACTIVITY_ID}."
    )
    return True


async def run(dry_run: bool) -> bool:
    async with async_session() as session:
        ok = await cleanup(session)
        if not ok:
            await session.rollback()
        elif dry_run:
            print("--dry-run: rolling back.")
            await session.rollback()
        else:
            await session.commit()
        return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the checks and print the outcome without committing.",
    )
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.dry_run)) else 1)


if __name__ == "__main__":
    main()
