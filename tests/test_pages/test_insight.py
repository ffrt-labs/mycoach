"""Tests for the insight page: one exact Insight revision's full analysis (#240)."""

import json
from datetime import date

import pytest
from httpx import AsyncClient

from mycoach.models.coaching import CoachingInsight
from mycoach.models.user import User
from tests.conftest import test_session

pytestmark = pytest.mark.anyio


def _notes(analysis_md: str) -> str:
    """Post-workout Coach notes (v5) whose interesting slot here is ``analysis_md``."""
    return json.dumps(
        {
            "headline": {"tone": "good", "verdict": "Aerobic", "summary": "Held Z2."},
            "actions": [{"do": "Keep the pace", "why": "Aerobic base"}],
            "zone_note": None,
            "trend_note": "Steady",
            "next_session": [],
            "guardrails": [],
            "week_addition": None,
            "ready_in_hours": 24,
            "recovery": "Easy evening",
            "analysis_md": analysis_md,
        }
    )


async def _seed_insight(
    content: str, *, revision: int = 1, activity_external_id: str | None = "garmin:1"
) -> int:
    async with test_session() as session:
        if await session.get(User, 1) is None:
            session.add(User(id=1, email="test@example.com", name="Test User"))
            await session.flush()
        insight = CoachingInsight(
            user_id=1,
            insight_date=date(2026, 11, 18),
            insight_type="post_workout",
            content=content,
            activity_external_id=activity_external_id,
            revision=revision,
        )
        session.add(insight)
        await session.commit()
        return insight.id


async def test_insight_page_renders_the_revisions_analysis(client: AsyncClient) -> None:
    insight_id = await _seed_insight(_notes("## Pacing\n\nYou held **Z2** for 40 min."))

    resp = await client.get(f"/insights/{insight_id}")

    assert resp.status_code == 200
    assert "<h2>Pacing</h2>" in resp.text
    assert "You held <strong>Z2</strong> for 40 min." in resp.text


async def test_insight_page_links_the_newest_revision_when_one_exists(
    client: AsyncClient,
) -> None:
    first = await _seed_insight(_notes("First take."), revision=1)
    await _seed_insight(_notes("Second take."), revision=2)
    latest = await _seed_insight(_notes("Third take."), revision=3)
    await _seed_insight(_notes("Another activity."), revision=4, activity_external_id="garmin:2")

    resp = await client.get(f"/insights/{first}")

    assert "First take." in resp.text
    assert "Third take." not in resp.text
    assert f'<a href="/insights/{latest}"' in resp.text
    assert "A newer analysis exists →" in resp.text


async def test_insight_page_has_no_newer_line_on_the_latest_revision(
    client: AsyncClient,
) -> None:
    await _seed_insight(_notes("First take."), revision=1)
    latest = await _seed_insight(_notes("Second take."), revision=2)

    resp = await client.get(f"/insights/{latest}")

    assert "Second take." in resp.text
    assert "A newer analysis exists" not in resp.text


async def test_insight_page_is_404_for_an_unknown_id(client: AsyncClient) -> None:
    resp = await client.get("/insights/999")

    assert resp.status_code == 404


async def test_insight_page_escapes_html_in_the_analysis(client: AsyncClient) -> None:
    insight_id = await _seed_insight(_notes("<script>alert(1)</script> [x](javascript:alert(1))"))

    resp = await client.get(f"/insights/{insight_id}")

    assert "<script>alert(1)</script>" not in resp.text
    assert 'href="javascript:' not in resp.text


async def test_insight_page_says_so_when_the_revision_has_no_analysis(
    client: AsyncClient,
) -> None:
    """A Coach notes fallback stores ``null``; a v4 insight has no ``analysis_md``."""
    fallback = await _seed_insight("null")
    v4 = await _seed_insight(json.dumps({"performance_summary": "Good swim."}), revision=2)

    for insight_id in (fallback, v4):
        resp = await client.get(f"/insights/{insight_id}")
        assert resp.status_code == 200
        assert "No full analysis for this revision." in resp.text
