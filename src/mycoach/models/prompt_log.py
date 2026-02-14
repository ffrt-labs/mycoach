from datetime import datetime

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class PromptLog(Base):
    __tablename__ = "prompt_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_type: Mapped[str] = mapped_column(
        String(50)
    )  # daily_briefing, weekly_plan, post_workout, sleep
    prompt_version: Mapped[str] = mapped_column(String(10))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, default=None)
    prompt_text: Mapped[str | None] = mapped_column(Text, default=None)
    response_text: Mapped[str | None] = mapped_column(Text, default=None)
    success: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
