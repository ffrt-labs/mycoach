"""Insight page: the full analysis (``analysis_md``) of one exact Insight revision (#240).

Revisions are immutable, so ``/insights/{id}`` always shows what the email that links
to it was rendered from. A newer revision of the same insight is linked, not shown.
"""

import contextlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

# CommonMark with raw HTML off: the coach's Markdown is rendered, never trusted as HTML.
_MARKDOWN = MarkdownIt("commonmark", {"html": False})


def analysis_html(content: str | None) -> Markup | None:
    """The insight's ``analysis_md`` as HTML, or None when it has none.

    v5 Coach notes carry ``analysis_md``; a v4 insight or a Coach notes fallback
    (content ``null``) does not.
    """
    data = None
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        data = json.loads(content or "")
    analysis = data.get("analysis_md") if isinstance(data, dict) else None
    if not isinstance(analysis, str) or not analysis.strip():
        return None
    return Markup(_MARKDOWN.render(analysis))  # noqa: S704 — html disabled above


async def _newest_revision(
    session: AsyncSession, insight: CoachingInsight
) -> CoachingInsight | None:
    """The latest revision of the same insight, if it is newer than ``insight``.

    Revisions exist for activity insights (CONTEXT.md: Insight revision), so an
    insight without an activity never has a newer one.
    """
    if insight.activity_external_id is None:
        return None
    result = await session.execute(
        select(CoachingInsight)
        .where(
            CoachingInsight.user_id == insight.user_id,
            CoachingInsight.insight_type == insight.insight_type,
            CoachingInsight.activity_external_id == insight.activity_external_id,
            CoachingInsight.revision > insight.revision,
        )
        .order_by(CoachingInsight.revision.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/insights/{insight_id}", response_class=HTMLResponse)
async def insight_page(
    request: Request, insight_id: int, session: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Render one Insight revision's full analysis; 404 for an unknown id."""
    insight = await session.get(CoachingInsight, insight_id)
    if insight is None or insight.user_id != USER_ID:
        raise HTTPException(status_code=404, detail="Insight not found")
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "insight.html",
        {
            "active_page": "history",
            "insight": insight,
            "analysis": analysis_html(insight.content),
            "newer": await _newest_revision(session, insight),
        },
    )
