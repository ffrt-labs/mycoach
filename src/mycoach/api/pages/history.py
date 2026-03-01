"""Activity history page — past workouts with details."""

import contextlib
import json
from datetime import date, timedelta

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
    recap_week: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the activity history page with paginated activities."""
    per_page = 20

    # Base query
    base_where = [Activity.user_id == USER_ID]
    if sport:
        base_where.append(Activity.sport == sport)

    # Count total
    count_result = await session.execute(select(func.count(Activity.id)).where(*base_where))
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

    # Check which activities have post-workout analyses and fetch content
    activity_ids = [a.id for a in activities]
    analyzed_ids: set[int] = set()
    analysis_data: dict[int, dict] = {}
    if activity_ids:
        analysis_result = await session.execute(
            select(CoachingInsight).where(
                CoachingInsight.insight_type == "post_workout",
                CoachingInsight.activity_id.in_(activity_ids),
            )
        )
        for insight in analysis_result.scalars().all():
            if insight.activity_id is not None:
                analyzed_ids.add(insight.activity_id)
                if insight.content:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        analysis_data[insight.activity_id] = json.loads(insight.content)

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
                exercises.append(
                    {
                        "title": ex_title,
                        "sets": sets,
                    }
                )

        color = SPORT_COLORS.get(a.sport, "gray")
        activity_cards.append(
            {
                "activity": a,
                "exercises": exercises,
                "has_analysis": a.id in analyzed_ids,
                "analysis_data": analysis_data.get(a.id),
                "color": color,
                "date_str": a.start_time.strftime("%b %d, %Y"),
                "time_str": a.start_time.strftime("%H:%M"),
            }
        )

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

    # Week picker: determine which week to show
    today = date.today()
    default_last_monday = today - timedelta(days=today.weekday() + 7)

    # Parse recap_week param if provided; must be a valid Monday
    last_monday = default_last_monday
    if recap_week:
        try:
            parsed_week = date.fromisoformat(recap_week)
            if parsed_week.weekday() == 0:
                last_monday = parsed_week
        except ValueError:
            pass

    # Generate last 8 Mondays for week picker (newest first)
    available_weeks = []
    for i in range(8):
        monday = default_last_monday - timedelta(weeks=i)
        available_weeks.append(
            {
                "iso": monday.isoformat(),
                "label": f"Week of {monday.strftime('%b')} {monday.day}, {monday.year}",
            }
        )

    selected_week_label = (
        f"Week of {last_monday.strftime('%b')} {last_monday.day}, {last_monday.year}"
    )

    # Fetch weekly recap for the selected week
    recap_result = await session.execute(
        select(CoachingInsight).where(
            CoachingInsight.user_id == USER_ID,
            CoachingInsight.insight_date == last_monday,
            CoachingInsight.insight_type == "weekly_recap",
        )
    )
    weekly_recap = recap_result.scalar_one_or_none()
    recap_data = None
    if weekly_recap and weekly_recap.content:
        try:
            recap_data = json.loads(weekly_recap.content)
        except (json.JSONDecodeError, TypeError):
            recap_data = None

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
            "weekly_recap": weekly_recap,
            "recap_data": recap_data,
            "last_monday_iso": last_monday.isoformat(),
            "available_weeks": available_weeks,
            "selected_week_label": selected_week_label,
        },
    )
