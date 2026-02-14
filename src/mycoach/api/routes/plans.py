"""Plans API routes — generate and retrieve weekly training plans."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.engine import CoachingEngine
from mycoach.database import get_db
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.schemas.plan import (
    PlanAdherenceRead,
    PlannedSessionRead,
    SessionAdherenceRead,
    WeeklyPlanRead,
)

router = APIRouter(prefix="/api/plans", tags=["plans"])

USER_ID = 1  # Single-user MVP


def _current_week_monday() -> date:
    """Return the Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


@router.post("/generate", response_model=WeeklyPlanRead, status_code=201)
async def generate_plan(
    week_start: date | None = None,
    session: AsyncSession = Depends(get_db),
) -> WeeklyPlan:
    """Generate a weekly training plan.

    Uses availability slots + health trends + recent activities to call
    the LLM and produce a structured plan with individual sessions.

    Query param `week_start` must be a Monday. Defaults to next Monday.
    Returns 409 if an active plan already exists for that week.
    """
    if week_start is None:
        # Default to next Monday
        today = date.today()
        days_ahead = 7 - today.weekday()
        week_start = today + timedelta(days=days_ahead)

    engine = CoachingEngine()
    try:
        plan = await engine.generate_weekly_plan(session, USER_ID, week_start)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None

    # Load sessions for response
    return await _load_plan_with_sessions(session, plan.id)


@router.get("/current", response_model=WeeklyPlanRead)
async def get_current_plan(
    session: AsyncSession = Depends(get_db),
) -> WeeklyPlan:
    """Get the active plan for the current week."""
    monday = _current_week_monday()
    stmt = select(WeeklyPlan).where(
        WeeklyPlan.user_id == USER_ID,
        WeeklyPlan.week_start == monday,
        WeeklyPlan.status == "active",
    )
    result = await session.execute(stmt)
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan for the current week.")
    return await _load_plan_with_sessions(session, plan.id)


@router.get("/{plan_id}", response_model=WeeklyPlanRead)
async def get_plan(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
) -> WeeklyPlan:
    """Get a specific plan by ID with its sessions."""
    stmt = select(WeeklyPlan).where(
        WeeklyPlan.id == plan_id,
        WeeklyPlan.user_id == USER_ID,
    )
    result = await session.execute(stmt)
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return await _load_plan_with_sessions(session, plan.id)


@router.get("/{plan_id}/sessions", response_model=list[PlannedSessionRead])
async def get_plan_sessions(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
) -> list[PlannedSession]:
    """List sessions for a plan."""
    # Verify plan exists and belongs to user
    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.id == plan_id,
        WeeklyPlan.user_id == USER_ID,
    )
    plan_result = await session.execute(plan_stmt)
    if plan_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    stmt = (
        select(PlannedSession)
        .where(PlannedSession.plan_id == plan_id)
        .order_by(PlannedSession.day_of_week)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.patch("/{plan_id}/sessions/{session_id}", response_model=PlannedSessionRead)
async def mark_session_completed(
    plan_id: int,
    session_id: int,
    activity_id: int | None = None,
    session: AsyncSession = Depends(get_db),
) -> PlannedSession:
    """Mark a planned session as completed, optionally linking to an activity."""
    stmt = select(PlannedSession).where(
        PlannedSession.id == session_id,
        PlannedSession.plan_id == plan_id,
    )
    result = await session.execute(stmt)
    planned = result.scalar_one_or_none()
    if planned is None:
        raise HTTPException(status_code=404, detail="Planned session not found.")

    # Verify plan belongs to user
    plan_stmt = select(WeeklyPlan).where(WeeklyPlan.id == plan_id, WeeklyPlan.user_id == USER_ID)
    plan_result = await session.execute(plan_stmt)
    if plan_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    planned.completed = True
    if activity_id is not None:
        planned.activity_id = activity_id
    await session.commit()
    await session.refresh(planned)
    return planned


@router.get("/{plan_id}/adherence", response_model=PlanAdherenceRead)
async def get_plan_adherence(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
) -> PlanAdherenceRead:
    """Get adherence stats for a plan (completed / total sessions)."""
    # Verify plan exists and belongs to user
    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.id == plan_id,
        WeeklyPlan.user_id == USER_ID,
    )
    plan_result = await session.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    sess_stmt = (
        select(PlannedSession)
        .where(PlannedSession.plan_id == plan_id)
        .order_by(PlannedSession.day_of_week)
    )
    sess_result = await session.execute(sess_stmt)
    sessions = list(sess_result.scalars().all())

    total = len(sessions)
    completed = sum(1 for s in sessions if s.completed)
    adherence_pct = round((completed / total) * 100, 1) if total > 0 else 0.0

    return PlanAdherenceRead(
        plan_id=plan.id,
        week_start=plan.week_start,
        total_sessions=total,
        completed_sessions=completed,
        adherence_pct=adherence_pct,
        sessions=[
            SessionAdherenceRead(
                session_id=s.id,
                day_of_week=s.day_of_week,
                sport=s.sport,
                title=s.title,
                completed=s.completed,
                activity_id=s.activity_id,
            )
            for s in sessions
        ],
    )


async def _load_plan_with_sessions(session: AsyncSession, plan_id: int) -> WeeklyPlan:
    """Load a plan and attach its sessions as a list attribute for serialization."""
    stmt = select(WeeklyPlan).where(WeeklyPlan.id == plan_id)
    result = await session.execute(stmt)
    plan = result.scalar_one()

    sess_stmt = (
        select(PlannedSession)
        .where(PlannedSession.plan_id == plan_id)
        .order_by(PlannedSession.day_of_week)
    )
    sess_result = await session.execute(sess_stmt)
    plan.sessions = list(sess_result.scalars().all())  # type: ignore[attr-defined]
    return plan
