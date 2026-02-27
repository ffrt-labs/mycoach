"""Prompt log viewer — browse LLM calls, prompts, and responses."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.prompt_log import PromptLog

router = APIRouter(tags=["pages"])

PROMPT_TYPES = [
    "daily_briefing",
    "weekly_plan",
    "gym_adjustment",
    "cardio_plan",
    "post_workout",
    "sleep",
    "weekly_recap",
]


@router.get("/prompt-logs", response_class=HTMLResponse)
async def prompt_logs_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    prompt_type: str | None = Query(default=None),
    success: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render paginated list of prompt logs."""
    per_page = 20

    # Build filters
    filters = []
    if prompt_type:
        filters.append(PromptLog.prompt_type == prompt_type)
    if success == "true":
        filters.append(PromptLog.success.is_(True))
    elif success == "false":
        filters.append(PromptLog.success.is_(False))

    # Count total
    count_result = await session.execute(select(func.count(PromptLog.id)).where(*filters))
    total = count_result.scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    # Fetch logs
    stmt = (
        select(PromptLog)
        .where(*filters)
        .order_by(PromptLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    logs = list(result.scalars().all())

    log_dicts = [
        {
            "id": log.id,
            "prompt_type": log.prompt_type,
            "prompt_version": log.prompt_version,
            "model": log.model,
            "input_tokens": log.input_tokens,
            "output_tokens": log.output_tokens,
            "latency_ms": log.latency_ms,
            "estimated_cost_usd": log.estimated_cost_usd,
            "success": log.success,
            "error": log.error,
            "created_at": log.created_at,
        }
        for log in logs
    ]

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "prompt_logs.html",
        {
            "active_page": "prompt_logs",
            "logs": log_dicts,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "prompt_type_filter": prompt_type,
            "success_filter": success,
            "prompt_types": PROMPT_TYPES,
        },
    )


@router.get("/prompt-logs/{log_id}", response_class=HTMLResponse)
async def prompt_log_detail(
    request: Request,
    log_id: int,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render detail view for a single prompt log."""
    result = await session.execute(select(PromptLog).where(PromptLog.id == log_id))
    log = result.scalar_one_or_none()

    if log is None:
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "prompt_log_detail.html",
            {"active_page": "prompt_logs", "log": None},
            status_code=404,
        )

    log_dict = {
        "id": log.id,
        "prompt_type": log.prompt_type,
        "prompt_version": log.prompt_version,
        "model": log.model,
        "input_tokens": log.input_tokens,
        "output_tokens": log.output_tokens,
        "latency_ms": log.latency_ms,
        "estimated_cost_usd": log.estimated_cost_usd,
        "success": log.success,
        "error": log.error,
        "created_at": log.created_at,
        "prompt_text": log.prompt_text,
        "response_text": log.response_text,
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "prompt_log_detail.html",
        {"active_page": "prompt_logs", "log": log_dict},
    )
