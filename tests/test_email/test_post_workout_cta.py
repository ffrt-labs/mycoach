"""The post-workout email's "Full analysis →" CTA opens the revision it rendered (#240)."""

import re

from tests.test_email.test_post_workout_notes import _render


def _cta(html: str) -> tuple[str, str]:
    """The CTA button's (href, label)."""
    match = re.search(r'<a href="([^"]+)" class="cta"[^>]*>([^<]+)</a>', html)
    assert match is not None, "no CTA in the email"
    return match.group(1), match.group(2).strip()


def test_post_workout_cta_opens_the_rendered_revisions_full_analysis() -> None:
    html = _render(insight_id=42)

    assert _cta(html) == ("https://coach.example.com/insights/42", "Full analysis →")


def test_post_workout_without_coach_notes_has_no_full_analysis_link() -> None:
    """A Coach notes fallback stored no analysis, so the CTA stays on the dashboard."""
    html = _render(None, insight_id=42)

    href, label = _cta(html)
    assert "/insights/" not in href
    assert label != "Full analysis →"
