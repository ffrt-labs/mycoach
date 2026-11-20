"""Life context API: the dashboard's illness check-in (#220) and travel mode (#221).

Episodes are coaching-owned (``coaching.life_context_episodes``): the athlete says
them, no device measures them. Days are local days in ``settings.timezone``.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching import life_context
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.localtime import local_today
from mycoach.models.life_context import Equipment, IllnessSeverity, LifeContextEpisode
from mycoach.schemas.life_context import EpisodeRead, EpisodeUpdate, IllnessCreate, TravelCreate

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
    """An illness from today, or told after the fact (#236).

    A backdated illness excuses that week's sessions at once on the plan page,
    dashboard and Logger: Completion is computed on read. Known limit: the week's
    Langfuse completion score was sent at recap time and is not updated, and the
    recap already sent is not regenerated.
    """
    today = _today()
    if body.started_on is not None and body.started_on > today:
        raise HTTPException(status_code=422, detail="An illness cannot start after today")
    if body.ended_on is not None and body.ended_on >= today:
        raise HTTPException(
            status_code=422, detail="An illness that is over ended yesterday at the latest"
        )
    # An omitted start is today, so an end given without one is always before it.
    if body.ended_on is not None and body.ended_on < (body.started_on or today):
        raise HTTPException(status_code=422, detail="An illness cannot end before it starts")
    try:
        episode = await life_context.open_illness(
            session,
            DEFAULT_USER_ID,
            severity=body.severity,
            note=_clean(body.note),
            today=today,
            started_on=body.started_on,
            ended_on=body.ended_on,
        )
    except (life_context.EpisodeAlreadyOpenError, life_context.IllnessOverlapError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return episode


@router.post("/travel", response_model=EpisodeRead, status_code=201)
async def travelling(
    body: TravelCreate, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    """A trip, now or entered in advance; the weekly plan reads it when written."""
    started_on = body.started_on or _today()
    if body.ended_on is not None and body.ended_on < started_on:
        raise HTTPException(status_code=422, detail="A trip cannot end before it starts")
    episode = await life_context.open_travel(
        session,
        DEFAULT_USER_ID,
        started_on=started_on,
        ended_on=body.ended_on,
        equipment=body.equipment,
        tz_shift_hours=body.tz_shift_hours,
        no_training=body.no_training,
        note=_clean(body.note),
    )
    await session.commit()
    return episode


_TRAVEL_FIELDS = ("equipment", "tz_shift_hours", "no_training", "ended_on")


@router.patch("/episodes/{episode_id}", response_model=EpisodeRead)
async def update_episode(
    episode_id: int, body: EpisodeUpdate, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    episode = await _episode(session, episode_id)
    if body.severity is not None:
        if episode.kind != life_context.EpisodeKind.ILLNESS:
            raise HTTPException(status_code=422, detail="Only an illness has a severity")
        episode.severity = IllnessSeverity(body.severity)
    travel_fields = [f for f in _TRAVEL_FIELDS if f in body.model_fields_set]
    if travel_fields and episode.kind != life_context.EpisodeKind.TRAVEL:
        raise HTTPException(status_code=422, detail="Only a trip has equipment and dates to edit")
    if body.equipment is not None:
        episode.equipment = Equipment(body.equipment)
    if "tz_shift_hours" in body.model_fields_set:
        episode.tz_shift_hours = body.tz_shift_hours
    if body.no_training is not None:
        episode.no_training = body.no_training
    if "ended_on" in body.model_fields_set:
        if body.ended_on is not None and body.ended_on < episode.started_on:
            raise HTTPException(status_code=422, detail="A trip cannot end before it starts")
        episode.ended_on = body.ended_on
    if "note" in body.model_fields_set:
        episode.note = _clean(body.note)
    await session.commit()
    return episode


@router.post("/episodes/{episode_id}/close", response_model=EpisodeRead)
async def close_episode(
    episode_id: int, session: AsyncSession = Depends(get_db)
) -> LifeContextEpisode:
    """ "Feeling better", or "Back home" on a trip."""
    episode = await _episode(session, episode_id)
    if episode.ended_on is None:
        life_context.close(episode, today=_today())
        await session.commit()
    return episode


@router.delete("/episodes/{episode_id}", status_code=204)
async def cancel_trip(episode_id: int, session: AsyncSession = Depends(get_db)) -> Response:
    """Cancel a trip entered in advance (409 once it has started)."""
    episode = await _episode(session, episode_id)
    try:
        await life_context.cancel_trip(session, episode, today=_today())
    except life_context.EpisodeUnderWayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return Response(status_code=204)
