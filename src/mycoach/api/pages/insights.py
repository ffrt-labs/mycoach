"""Insight page: the full analysis (``analysis_md``) of one exact Insight revision (#240).

Revisions are immutable, so ``/insights/{id}`` always shows what the email that links
to it was rendered from. A newer revision of the same insight is linked, not shown.
"""

from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.email_schemas import DailyBriefingEmail, PostWorkoutEmail, WeeklyRecapEmail
from mycoach.database import get_db
from mycoach.email.coach_notes import NotesKind, StoredNotes, stored_notes
from mycoach.models.coaching import CoachingInsight

router = APIRouter(tags=["pages"])

USER_ID = 1  # Single-user MVP

# CommonMark with raw HTML off: the coach's Markdown is rendered, never trusted as HTML.
_MARKDOWN = MarkdownIt("commonmark", {"html": False})


def markdown_html(markdown: str | None) -> Markup | None:
    """The coach's Markdown as HTML, or None when there is none."""
    if not isinstance(markdown, str) or not markdown.strip():
        return None
    return Markup(_MARKDOWN.render(markdown))  # noqa: S704 — html disabled above


_NOTES_MODEL: Mapping[str, type[PostWorkoutEmail | WeeklyRecapEmail | DailyBriefingEmail]] = {
    "post_workout": PostWorkoutEmail,
    "weekly_recap": WeeklyRecapEmail,
    "daily_briefing": DailyBriefingEmail,
}
"""Each insight type's v5 Coach notes; every one carries ``analysis_md``."""


def insight_notes(
    insight: CoachingInsight,
) -> StoredNotes[PostWorkoutEmail | WeeklyRecapEmail | DailyBriefingEmail]:
    """The insight's stored content through the shared v5/legacy/fallback check (#249).

    An insight type without Coach notes has none to show: the fallback.
    """
    model = _NOTES_MODEL.get(insight.insight_type)
    if model is None:
        return StoredNotes(NotesKind.FALLBACK)
    return stored_notes(insight.content, model)


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
    stored = insight_notes(insight)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "insight.html",
        {
            "active_page": "history",
            "insight": insight,
            "insight_notes": stored,
            "analysis": markdown_html(stored.notes.analysis_md) if stored.notes else None,
            "newer": await _newest_revision(session, insight),
        },
    )
