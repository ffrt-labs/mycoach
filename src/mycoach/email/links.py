"""Links from an email into the dashboard."""

from datetime import date, timedelta

from mycoach.config import Settings


def insight_url(settings: Settings, insight_id: int) -> str:
    """The insight page of one exact Insight revision: its "Full analysis →" (#240).

    Link the revision the email rendered, never "the latest": revisions are immutable,
    so the link never changes under an email already sent. The page itself points to a
    newer revision when one exists.
    """
    return f"{settings.app_base_url}/insights/{insight_id}"


def dashboard_url(settings: Settings) -> str:
    """Build the dashboard link used for every email's CTA button.

    The dashboard is the app's root page (``api/pages/dashboard.py`` serves ``/``).
    """
    return f"{settings.app_base_url.rstrip('/')}/"


def life_context_url(settings: Settings) -> str:
    """The dashboard's Life context section, where illness and travel are logged."""
    return f"{settings.app_base_url}/#life-context"


def default_schedule_url(settings: Settings) -> str:
    """Build the link straight to the standing-schedule tab of the availability page."""
    return f"{settings.app_base_url}/availability?week=default"


def prefill_url(settings: Settings, kind: str, start: date, end: date) -> str:
    """The dashboard's Life context form, prefilled (#236). Opening it writes nothing."""
    return (
        f"{settings.app_base_url}/?log={kind}&from={start.isoformat()}"
        f"&to={end.isoformat()}#life-context"
    )


def log_episode_url(settings: Settings, week_start: str) -> str:
    """The dashboard's Life context form, prefilled for the recapped week (#236)."""
    start = date.fromisoformat(week_start)
    return prefill_url(settings, "illness", start, start + timedelta(days=6))
