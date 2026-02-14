"""Plans API routes — generate and retrieve weekly training plans."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.engine import CoachingEngine
from mycoach.database import get_db
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.schemas.plan import PlannedSessionRead, WeeklyPlanRead

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
