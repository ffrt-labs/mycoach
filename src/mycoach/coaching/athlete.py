"""Mapping a coaching user to the health side's Athlete ID (map #85, ticket #186).

The health schema issues the Athlete ID and coaching stores it as ``users.health_athlete_id``
(never joined across the seam). Every call into the health module goes through this mapping,
replacing the ticket #184 convention that the athlete id equals the user id.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.user import User


async def athlete_id_for(session: AsyncSession, user_id: int) -> int:
    """The Athlete ID of ``user_id``.

    A user with no stored mapping (not yet created, or created before #184) falls back to
    its own id: the first deployment issued the one athlete the same id as the one user.
    """
    mapped = (
        await session.execute(select(User.health_athlete_id).where(User.id == user_id))
    ).scalar_one_or_none()
    return mapped if mapped is not None else user_id
