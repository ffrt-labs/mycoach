"""Life context API (#220): the dashboard's illness check-in, severity edit and close.

Episodes are coaching-owned (``coaching.life_context_episodes``): the athlete says
them, no device measures them. Days are local days in ``settings.timezone``.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching import life_context
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.localtime import local_today
from mycoach.models.life_context import IllnessSeverity, LifeContextEpisode
from mycoach.schemas.life_context import EpisodeRead, EpisodeUpdate, IllnessCreate

router = APIRouter(prefix="/api/life-context", tags=["life-context"])


def _today() -> date:
    return local_today(get_settings().timezone)


def _clean(note: str | None) -> str | None:
    return note.strip() or None if note else None


async def _episode(session: AsyncSession, episode_id: int) -> LifeContextEpisode:
    episode = await life_context.get_episode(session, DEFAULT_USER_ID, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


@router.post("/illness", response_model=EpisodeRead, status_code=201)
async def feeling_unwell(
    body: IllnessCreate, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    try:
        episode = await life_context.open_illness(
            session,
            DEFAULT_USER_ID,
            severity=body.severity,
            note=_clean(body.note),
            today=_today(),
        )
    except life_context.EpisodeAlreadyOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return episode


@router.patch("/episodes/{episode_id}", response_model=EpisodeRead)
async def update_episode(
    episode_id: int, body: EpisodeUpdate, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    episode = await _episode(session, episode_id)
    if body.severity is not None:
        if episode.kind != life_context.EpisodeKind.ILLNESS:
            raise HTTPException(status_code=422, detail="Only an illness has a severity")
        episode.severity = IllnessSeverity(body.severity)
    if "note" in body.model_fields_set:
        episode.note = _clean(body.note)
    await session.commit()
    return episode


@router.post("/episodes/{episode_id}/close", response_model=EpisodeRead)
async def close_episode(
    episode_id: int, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    """ "Feeling better"."""
    episode = await _episode(session, episode_id)
    if episode.ended_on is None:
        life_context.close(episode, today=_today())
        await session.commit()
    return episode
