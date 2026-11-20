"""Links from an email into the dashboard."""

from mycoach.config import Settings


def insight_url(settings: Settings, insight_id: int) -> str:
    """The insight page of one exact Insight revision: its "Full analysis →" (#240).

    Link the revision the email rendered, never "the latest": revisions are immutable,
    so the link never changes under an email already sent. The page itself points to a
    newer revision when one exists.
    """
    return f"{settings.app_base_url}/insights/{insight_id}"
