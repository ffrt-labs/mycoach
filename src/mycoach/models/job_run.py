from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class JobRun(Base):
    """Durable record of a single scheduled-job execution.

    Append-only, write-only exhaust: nothing in the coaching, source, or
    scheduler logic ever reads it. Idempotency comes from checking for existing
    insights, never from querying run history.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime]
    duration_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(20))  # success, skipped, failed
    error: Mapped[str | None] = mapped_column(Text, default=None)
