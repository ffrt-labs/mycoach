from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import COACHING_SCHEMA, Base


class JobRun(Base):
    """Durable record of a single scheduled-job execution.

    Append-only, and — since the daily-briefing retry window — no longer
    write-only. ``mycoach.scheduler.briefing_window`` derives that window's
    entire state from these rows: whether today's briefing has succeeded, how
    many attempts have been made since the window opened, whether each ended in
    a skip or a real failure, and whether the user has already been emailed.

    That is deliberate rather than convenient. The scheduler has no jobstore and
    rebuilds its jobs on every boot, so anything held in memory is lost to a
    container restart; a table already written on every run is the only place
    the state can live without inventing one. Idempotency for *insights* still
    comes from checking for the insight itself, never from run history.
    """

    __tablename__ = "job_runs"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime]
    duration_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(20))  # success, skipped, failed
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Populated only when status is "skipped" — a skip is not an error, so it
    # gets its own column rather than overloading `error`.
    skip_reason: Mapped[str | None] = mapped_column(Text, default=None)
    # Whether an email actually went out, kept deliberately separate from status
    # rather than folded into it. A run whose insight generated fine but whose
    # recipient has that email type switched off is a genuine success that sent
    # nothing; a run whose send was attempted and rejected is a failure. Keeping
    # delivery its own field represents both without overloading the status
    # vocabulary, and lets monitoring alert on "succeeding but not delivering".
    email_delivered: Mapped[bool] = mapped_column(default=False)
