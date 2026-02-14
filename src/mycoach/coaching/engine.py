"""Coaching engine — orchestrates LLM calls for generating coaching insights."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import (
    get_health_trends,
    get_recent_activities,
    get_today_health,
)
from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.coaching.prompt_builder import (
    build_daily_briefing_prompt,
    get_system_prompt,
)
from mycoach.coaching.response_parser import DailyBriefingResponse, parse_response
from mycoach.models.coaching import CoachingInsight
from mycoach.models.prompt_log import PromptLog

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"


class CoachingEngine:
    """Orchestrates coaching insight generation."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def generate_daily_briefing(
        self,
        session: AsyncSession,
        user_id: int,
        today: date | None = None,
    ) -> CoachingInsight:
        """Generate a daily coaching briefing.

        Gathers context from DB, calls the LLM, validates the response,
        stores the result as a CoachingInsight, and logs the prompt.

        Returns the saved CoachingInsight.
        """
        today = today or date.today()

        # Check for existing briefing today
        existing = await session.execute(
            select(CoachingInsight).where(
                CoachingInsight.user_id == user_id,
                CoachingInsight.insight_date == today,
                CoachingInsight.insight_type == "daily_briefing",
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Daily briefing already exists for {today}")

        # Gather context
        health_today = await get_today_health(session, user_id, today)
        health_trends = await get_health_trends(session, user_id, days=3, today=today)
        recent_activities = await get_recent_activities(session, user_id, days=3, today=today)

        # Build prompts
        system_prompt = get_system_prompt(PROMPT_VERSION)
        user_message = build_daily_briefing_prompt(
            health_today=health_today,
            health_trends=health_trends,
            recent_activities=recent_activities,
            version=PROMPT_VERSION,
        )

        # Call LLM
        llm_response: LLMResponse | None = None
        error_msg: str | None = None
        parsed: DailyBriefingResponse | None = None

        try:
            llm_response = self._llm.call(
                system=system_prompt,
                user_message=user_message,
                model=self._llm.daily_model,
            )
            parsed = parse_response(llm_response.content, DailyBriefingResponse)
        except Exception as e:
            error_msg = str(e)
            logger.error("Daily briefing generation failed: %s", error_msg)

            # Retry once on parse failure if we got an LLM response
            if llm_response is not None:
                try:
                    llm_response = self._llm.call(
                        system=system_prompt,
                        user_message=user_message + "\n\nIMPORTANT: Respond ONLY with valid JSON.",
                        model=self._llm.daily_model,
                    )
                    parsed = parse_response(llm_response.content, DailyBriefingResponse)
                    error_msg = None
                except Exception as retry_err:
                    error_msg = f"Retry also failed: {retry_err}"
                    logger.error("Daily briefing retry failed: %s", retry_err)

        # Log the prompt call
        prompt_log = PromptLog(
            prompt_type="daily_briefing",
            prompt_version=PROMPT_VERSION,
            model=llm_response.model if llm_response else self._llm.daily_model,
            input_tokens=llm_response.input_tokens if llm_response else None,
            output_tokens=llm_response.output_tokens if llm_response else None,
            latency_ms=llm_response.latency_ms if llm_response else None,
            estimated_cost_usd=llm_response.estimated_cost_usd if llm_response else None,
            prompt_text=user_message,
            response_text=llm_response.content if llm_response else None,
            success=parsed is not None,
            error=error_msg,
        )
        session.add(prompt_log)

        if parsed is None:
            await session.commit()
            raise RuntimeError(f"Failed to generate daily briefing: {error_msg}")

        # Store the coaching insight
        content_json = parsed.model_dump_json()
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=today,
            insight_type="daily_briefing",
            content=content_json,
            prompt_version=PROMPT_VERSION,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)

        return insight
