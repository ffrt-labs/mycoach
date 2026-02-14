"""Coaching engine — orchestrates LLM calls for generating coaching insights."""

import json
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import (
    find_matching_planned_session,
    get_activity_with_details,
    get_availability_for_week,
    get_health_trends,
    get_mesocycle_context,
    get_recent_activities,
    get_similar_activities,
    get_today_health,
    link_activity_to_planned_session,
)
from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.coaching.prompt_builder import (
    build_daily_briefing_prompt,
    build_post_workout_prompt,
    build_weekly_plan_prompt,
    get_system_prompt,
)
from mycoach.coaching.response_parser import (
    DailyBriefingResponse,
    PostWorkoutResponse,
    WeeklyPlanResponse,
    parse_response,
)
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
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

    async def generate_weekly_plan(
        self,
        session: AsyncSession,
        user_id: int,
        week_start: date,
    ) -> WeeklyPlan:
        """Generate a weekly training plan.

        Gathers availability + health + activity context, calls the LLM (weekly model),
        validates the response, stores WeeklyPlan + PlannedSessions, and logs the prompt.

        Returns the saved WeeklyPlan.
        """
        if week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday")

        # Check for existing active plan for this week
        existing = await session.execute(
            select(WeeklyPlan).where(
                WeeklyPlan.user_id == user_id,
                WeeklyPlan.week_start == week_start,
                WeeklyPlan.status == "active",
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Active plan already exists for week of {week_start}")

        # Gather context
        availability = await get_availability_for_week(session, user_id, week_start)
        if not availability:
            raise ValueError(f"No availability slots set for week of {week_start}")

        health_trends = await get_health_trends(
            session, user_id, days=7, today=week_start
        )
        recent_activities = await get_recent_activities(
            session, user_id, days=14, today=week_start
        )
        mesocycle_ctx = await get_mesocycle_context(session, user_id)

        # Build prompts
        system_prompt = get_system_prompt(PROMPT_VERSION)
        user_message = build_weekly_plan_prompt(
            availability=availability,
            health_trends=health_trends,
            recent_activities=recent_activities,
            mesocycle_context=mesocycle_ctx,
            version=PROMPT_VERSION,
        )

        # Call LLM (weekly model for better reasoning)
        llm_response: LLMResponse | None = None
        error_msg: str | None = None
        parsed: WeeklyPlanResponse | None = None

        try:
            llm_response = self._llm.call(
                system=system_prompt,
                user_message=user_message,
                model=self._llm.weekly_model,
                max_tokens=8192,
            )
            parsed = parse_response(llm_response.content, WeeklyPlanResponse)
        except Exception as e:
            error_msg = str(e)
            logger.error("Weekly plan generation failed: %s", error_msg)

            if llm_response is not None:
                try:
                    llm_response = self._llm.call(
                        system=system_prompt,
                        user_message=user_message + "\n\nIMPORTANT: Respond ONLY with valid JSON.",
                        model=self._llm.weekly_model,
                        max_tokens=8192,
                    )
                    parsed = parse_response(llm_response.content, WeeklyPlanResponse)
                    error_msg = None
                except Exception as retry_err:
                    error_msg = f"Retry also failed: {retry_err}"
                    logger.error("Weekly plan retry failed: %s", retry_err)

        # Log the prompt call
        prompt_log = PromptLog(
            prompt_type="weekly_plan",
            prompt_version=PROMPT_VERSION,
            model=llm_response.model if llm_response else self._llm.weekly_model,
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
            raise RuntimeError(f"Failed to generate weekly plan: {error_msg}")

        # Store the plan
        plan = WeeklyPlan(
            user_id=user_id,
            week_start=week_start,
            prompt_version=PROMPT_VERSION,
            status="active",
            summary=parsed.summary,
            raw_llm_output=llm_response.content if llm_response else None,
        )
        session.add(plan)
        await session.flush()  # get plan.id

        for s in parsed.sessions:
            planned = PlannedSession(
                plan_id=plan.id,
                day_of_week=s.day_of_week,
                sport=s.sport,
                title=s.title,
                duration_minutes=s.duration_minutes,
                details=json.dumps(s.details) if s.details else None,
                notes=s.notes,
            )
            session.add(planned)

        await session.commit()
        await session.refresh(plan)

        return plan

    async def generate_post_workout_analysis(
        self,
        session: AsyncSession,
        user_id: int,
        activity_id: int,
    ) -> CoachingInsight:
        """Generate a post-workout analysis for a completed activity.

        Gathers activity data, matches to planned session, finds similar activities
        for trend analysis, calls LLM, stores as CoachingInsight, and links to
        the planned session if found.

        Returns the saved CoachingInsight.
        """
        # Check for existing analysis for this activity
        existing = await session.execute(
            select(CoachingInsight).where(
                CoachingInsight.user_id == user_id,
                CoachingInsight.activity_id == activity_id,
                CoachingInsight.insight_type == "post_workout",
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(
                f"Post-workout analysis already exists for activity {activity_id}"
            )

        # Gather context
        activity_dict, gym_details = await get_activity_with_details(
            session, activity_id, user_id
        )
        planned_session = await find_matching_planned_session(
            session, activity_dict, user_id
        )
        similar = await get_similar_activities(
            session, user_id, activity_dict["sport"], activity_id
        )

        # Get health context for the activity date
        import contextlib
        from datetime import datetime as dt

        activity_date = date.today()
        start_time_str = activity_dict.get("start_time")
        if start_time_str:
            with contextlib.suppress(ValueError, TypeError):
                activity_date = dt.fromisoformat(start_time_str).date()
        health_ctx = await get_today_health(session, user_id, activity_date)

        # Build prompts
        system_prompt = get_system_prompt(PROMPT_VERSION)
        user_message = build_post_workout_prompt(
            activity=activity_dict,
            gym_details=gym_details,
            planned_session=planned_session,
            similar_activities=similar,
            health_context=health_ctx,
            version=PROMPT_VERSION,
        )

        # Call LLM
        llm_response: LLMResponse | None = None
        error_msg: str | None = None
        parsed: PostWorkoutResponse | None = None

        try:
            llm_response = self._llm.call(
                system=system_prompt,
                user_message=user_message,
                model=self._llm.daily_model,
            )
            parsed = parse_response(llm_response.content, PostWorkoutResponse)
        except Exception as e:
            error_msg = str(e)
            logger.error("Post-workout analysis failed: %s", error_msg)

            if llm_response is not None:
                try:
                    llm_response = self._llm.call(
                        system=system_prompt,
                        user_message=user_message
                        + "\n\nIMPORTANT: Respond ONLY with valid JSON.",
                        model=self._llm.daily_model,
                    )
                    parsed = parse_response(llm_response.content, PostWorkoutResponse)
                    error_msg = None
                except Exception as retry_err:
                    error_msg = f"Retry also failed: {retry_err}"
                    logger.error("Post-workout analysis retry failed: %s", retry_err)

        # Log the prompt call
        prompt_log = PromptLog(
            prompt_type="post_workout",
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
            raise RuntimeError(f"Failed to generate post-workout analysis: {error_msg}")

        # Link activity to planned session if found
        if planned_session:
            await link_activity_to_planned_session(
                session, activity_id, planned_session["id"]
            )

        # Store the coaching insight
        content_json = parsed.model_dump_json()
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=activity_date,
            insight_type="post_workout",
            content=content_json,
            prompt_version=PROMPT_VERSION,
            activity_id=activity_id,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)

        return insight
