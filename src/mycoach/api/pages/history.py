"""Activity history page — past workouts with details."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.coaching import CoachingInsight

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

SPORT_COLORS = {
    "gym": "blue",
    "swimming": "cyan",
    "padel": "green",
    "cardio": "red",
}


@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    sport: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the activity history page with paginated activities."""
    per_page = 20

    # Base query
    base_where = [Activity.user_id == USER_ID]
    if sport:
        base_where.append(Activity.sport == sport)

    # Count total
    count_result = await session.execute(
        select(func.count(Activity.id)).where(*base_where)
    )
    total = count_result.scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    # Fetch activities
    stmt = (
        select(Activity)
        .where(*base_where)
        .order_by(Activity.start_time.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    activities = list(result.scalars().all())

    # Fetch gym details for gym activities
    gym_activity_ids = [a.id for a in activities if a.sport == "gym"]
    gym_details: dict[int, list[GymWorkoutDetail]] = {}
    if gym_activity_ids:
        details_result = await session.execute(
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id.in_(gym_activity_ids))
            .order_by(GymWorkoutDetail.activity_id, GymWorkoutDetail.set_index)
        )
        for detail in details_result.scalars().all():
            gym_details.setdefault(detail.activity_id, []).append(detail)

    # Check which activities have post-workout analyses
    activity_ids = [a.id for a in activities]
    analyzed_ids: set[int] = set()
    if activity_ids:
        analysis_result = await session.execute(
            select(CoachingInsight.activity_id).where(
                CoachingInsight.insight_type == "post_workout",
                CoachingInsight.activity_id.in_(activity_ids),
            )
        )
        analyzed_ids = {row[0] for row in analysis_result.all() if row[0] is not None}

    # Build activity cards data
    activity_cards: list[dict[str, object]] = []
    for a in activities:
        # Group gym details by exercise
        exercises: list[dict] = []
        if a.id in gym_details:
            exercise_map: dict[str, list[GymWorkoutDetail]] = {}
            for d in gym_details[a.id]:
                exercise_map.setdefault(d.exercise_title, []).append(d)
            for ex_title, sets in exercise_map.items():
                exercises.append({
                    "title": ex_title,
                    "sets": sets,
                })

        color = SPORT_COLORS.get(a.sport, "gray")
        activity_cards.append({
            "activity": a,
            "exercises": exercises,
            "has_analysis": a.id in analyzed_ids,
            "color": color,
            "date_str": a.start_time.strftime("%b %d, %Y"),
            "time_str": a.start_time.strftime("%H:%M"),
        })

    # Group by date for display
    grouped: dict[str, list[dict[str, object]]] = {}
    for card in activity_cards:
        date_key = str(card["date_str"])
        grouped.setdefault(date_key, []).append(card)

    # Available sports for filter
    sports_result = await session.execute(
        select(Activity.sport)
        .where(Activity.user_id == USER_ID)
        .distinct()
        .order_by(Activity.sport)
    )
    available_sports = [row[0] for row in sports_result.all()]

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "active_page": "history",
            "grouped_activities": grouped,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "per_page": per_page,
            "sport_filter": sport,
            "available_sports": available_sports,
        },
    )
