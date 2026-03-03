"""Coaching engine — orchestrates LLM calls for generating coaching insights."""

import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import (
    find_matching_planned_session,
    get_active_routine,
    get_activities_for_week,
    get_activity_with_details,
    get_availability_for_week,
    get_gym_details_for_week,
    get_gym_performance_history,
    get_health_trends,
    get_health_trends_averaged,
    get_last_week_all_activities,
    get_last_week_cardio_performance,
    get_mesocycle_context,
    get_plan_adherence_for_week,
    get_recent_activities,
    get_recent_plan_summaries,
    get_similar_activities,
    get_sport_profiles,
    get_today_health,
    get_today_planned_sessions,
    link_activity_to_planned_session,
)
from mycoach.coaching.llm_client import LLMClient, LLMResponse, get_llm_client
from mycoach.coaching.prompt_builder import (
    build_cardio_plan_prompt,
    build_daily_briefing_prompt,
    build_gym_adjustment_prompt,
    build_post_workout_prompt,
    build_weekly_recap_prompt,
    get_system_prompt,
)
from mycoach.coaching.response_parser import (
    CardioPlanResponse,
    DailyBriefingResponse,
    GymAdjustmentResponse,
    PostWorkoutResponse,
    WeeklyRecapResponse,
    parse_response,
)
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.prompt_log import PromptLog

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v2"


class CoachingEngine:
    """Orchestrates coaching insight generation."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    async def generate_daily_briefing(
        self,
        session: AsyncSession,
        user_id: int,
        today: date | None = None,
        force: bool = False,
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
            if force:
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.insight_date == today,
                        CoachingInsight.insight_type == "daily_briefing",
                    )
                )
            else:
                raise ValueError(f"Daily briefing already exists for {today}")

        # Gather context
        health_today = await get_today_health(session, user_id, today)
        health_trends = await get_health_trends(session, user_id, days=3, today=today)
        recent_activities = await get_recent_activities(session, user_id, days=3, today=today)
        planned_sessions = await get_today_planned_sessions(session, user_id, today)
        sport_profiles = await get_sport_profiles(session, user_id)

        # Build prompts
        system_prompt = get_system_prompt(PROMPT_VERSION)
        user_message = build_daily_briefing_prompt(
            health_today=health_today,
            health_trends=health_trends,
            recent_activities=recent_activities,
            planned_workout=planned_sessions if planned_sessions else None,
            sport_profiles=sport_profiles,
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

    def _call_llm_with_retry(
        self,
        *,
        system: str,
        user_message: str,
        model: str,
        max_tokens: int = 4096,
        response_model: type,
    ) -> tuple[LLMResponse | None, Any | None, str | None]:
        """Call LLM with one retry on parse failure. Returns (response, parsed, error)."""
        llm_response: LLMResponse | None = None
        error_msg: str | None = None
        parsed: Any = None

        try:
            llm_response = self._llm.call(
                system=system,
                user_message=user_message,
                model=model,
                max_tokens=max_tokens,
            )
            parsed = parse_response(llm_response.content, response_model)
        except Exception as e:
            error_msg = str(e)
            logger.error("LLM call failed (%s): %s", response_model.__name__, error_msg)

            if llm_response is not None:
                # If truncated, retry with doubled token budget
                retry_tokens = max_tokens
                if llm_response.stop_reason == "max_tokens":
                    retry_tokens = max_tokens * 2
                    logger.warning(
                        "Response truncated (stop_reason=max_tokens, used %d tokens). "
                        "Retrying with max_tokens=%d",
                        llm_response.output_tokens,
                        retry_tokens,
                    )

                try:
                    llm_response = self._llm.call(
                        system=system,
                        user_message=user_message + "\n\nIMPORTANT: Respond ONLY with valid JSON.",
                        model=model,
                        max_tokens=retry_tokens,
                    )
                    parsed = parse_response(llm_response.content, response_model)
                    error_msg = None
                except Exception as retry_err:
                    error_msg = f"Retry also failed: {retry_err}"

        return llm_response, parsed, error_msg

    async def generate_weekly_plan(
        self,
        session: AsyncSession,
        user_id: int,
        week_start: date,
        force: bool = False,
    ) -> WeeklyPlan:
        """Generate a weekly training plan using two-track system.

        Track 1 (Gym): Uses the user's fixed routine, LLM adjusts weights/RPE only.
        Track 2 (Cardio): LLM designs progressive swimming/running sessions from goals.

        Falls back to all-cardio if no routine is defined.
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
        existing_plan = existing.scalar_one_or_none()
        if existing_plan is not None:
            if force:
                existing_plan.status = "replaced"
            else:
                raise ValueError(f"Active plan already exists for week of {week_start}")

        # 1. Gather shared context
        availability = await get_availability_for_week(session, user_id, week_start)
        if not availability:
            raise ValueError(f"No availability slots set for week of {week_start}")

        health_trends_avg = await get_health_trends_averaged(
            session, user_id, days=7, today=week_start
        )
        mesocycle_ctx = await get_mesocycle_context(session, user_id)
        system_prompt = get_system_prompt(PROMPT_VERSION)

        # 2. Fetch routine + sport profiles + last week's full training log
        routine = await get_active_routine(session, user_id)
        sport_profiles = await get_sport_profiles(session, user_id)
        last_week_activities = await get_last_week_all_activities(
            session, user_id, week_start
        )

        # 3. Split slots by user-assigned sport
        gym_slots = [s for s in availability if s.get("sport") == "gym"]
        cardio_slots = [s for s in availability if s.get("sport") != "gym"]

        # Map routine days to gym slots in order
        routine_days = routine["days"] if routine else []
        sorted_routine_days = sorted(routine_days, key=lambda d: d.get("order_index", 0))
        gym_day_map: dict[int, dict] = {}
        for i, slot in enumerate(gym_slots):
            if i < len(sorted_routine_days):
                gym_day_map[slot["day_of_week"]] = sorted_routine_days[i]

        # Create the plan record
        plan = WeeklyPlan(
            user_id=user_id,
            week_start=week_start,
            prompt_version=PROMPT_VERSION,
            status="active",
        )
        session.add(plan)
        await session.flush()

        all_raw_outputs: list[str] = []
        summaries: list[str] = []

        # 4. GYM TRACK — one LLM call per routine day (not per gym slot)
        for routine_day in sorted_routine_days:
            user_message = build_gym_adjustment_prompt(
                routine_day_name=routine_day["name"],
                routine_exercises=routine_day["exercises"],
                health_trends=health_trends_avg,
                last_week_activities=last_week_activities,
                mesocycle_context=mesocycle_ctx,
                sport_profiles=sport_profiles,
                version=PROMPT_VERSION,
            )

            llm_response, parsed, error_msg = self._call_llm_with_retry(
                system=system_prompt,
                user_message=user_message,
                model=self._llm.daily_model,
                max_tokens=8192,
                response_model=GymAdjustmentResponse,
            )

            # Log prompt
            prompt_log = PromptLog(
                prompt_type="gym_adjustment",
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

            if llm_response and llm_response.content:
                all_raw_outputs.append(llm_response.content)

            # Find matching gym slot for this routine day (for PlannedSession day_of_week)
            matched_slot = next(
                (s for s in gym_slots if gym_day_map.get(s["day_of_week"]) is routine_day),
                None,
            )

            if parsed is not None:
                # Build details combining routine exercises + LLM adjustments
                exercises_detail = []
                for ex in parsed.exercises:
                    routine_ex = next(
                        (
                            r
                            for r in routine_day["exercises"]
                            if r["exercise_name"] == ex.exercise_name
                        ),
                        None,
                    )
                    exercises_detail.append(
                        {
                            "name": ex.exercise_name,
                            "target_weight_kg": ex.target_weight_kg,
                            "sets": routine_ex["sets"] if routine_ex else None,
                            "reps": routine_ex["rep_range"] if routine_ex else None,
                            "rpe": ex.target_rpe,
                            "rest_seconds": ex.rest_seconds,
                            "adjustment_rationale": ex.adjustment_rationale,
                            "notes": ex.notes,
                            "superset_group": (
                                routine_ex.get("superset_group") if routine_ex else None
                            ),
                        }
                    )

                details = {"exercises": exercises_detail}
                if matched_slot is not None:
                    planned = PlannedSession(
                        plan_id=plan.id,
                        day_of_week=matched_slot["day_of_week"],
                        sport="gym",
                        title=routine_day["name"],
                        duration_minutes=parsed.estimated_duration_minutes,
                        details=json.dumps(details),
                        notes=parsed.session_notes,
                        track="gym",
                    )
                    session.add(planned)
            else:
                logger.warning(
                    "Gym adjustment failed for %s, creating session from routine only",
                    routine_day["name"],
                )
                if matched_slot is not None:
                    exercises_detail = [
                        {
                            "name": ex["exercise_name"],
                            "sets": ex["sets"],
                            "reps": ex["rep_range"],
                            "notes": ex.get("notes"),
                            "superset_group": ex.get("superset_group"),
                        }
                        for ex in routine_day["exercises"]
                    ]
                    planned = PlannedSession(
                        plan_id=plan.id,
                        day_of_week=matched_slot["day_of_week"],
                        sport="gym",
                        title=routine_day["name"],
                        duration_minutes=None,
                        details=json.dumps({"exercises": exercises_detail}),
                        notes="LLM adjustment failed — using base routine.",
                        track="gym",
                    )
                    session.add(planned)

        # 5. CARDIO TRACK — single LLM call (weekly model, creative task)
        if cardio_slots:
            last_cardio = await get_last_week_cardio_performance(session, user_id, week_start)
            health_trends_list = await get_health_trends(
                session, user_id, days=7, today=week_start
            )

            user_message = build_cardio_plan_prompt(
                cardio_slots=cardio_slots,
                last_week_cardio=last_cardio,
                health_trends=health_trends_list,
                mesocycle_context=mesocycle_ctx,
                sport_profiles=sport_profiles,
                version=PROMPT_VERSION,
            )

            llm_response, parsed, error_msg = self._call_llm_with_retry(
                system=system_prompt,
                user_message=user_message,
                model=self._llm.weekly_model,
                max_tokens=8192,
                response_model=CardioPlanResponse,
            )

            # Log prompt
            prompt_log = PromptLog(
                prompt_type="cardio_plan",
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

            if llm_response and llm_response.content:
                all_raw_outputs.append(llm_response.content)

            if parsed is not None:
                summaries.append(parsed.weekly_summary)
                for s in parsed.sessions:
                    planned = PlannedSession(
                        plan_id=plan.id,
                        day_of_week=s.day_of_week,
                        sport=s.sport,
                        title=s.title,
                        duration_minutes=s.duration_minutes,
                        details=json.dumps(s.details) if s.details else None,
                        notes=s.notes,
                        track="cardio",
                    )
                    session.add(planned)
            else:
                logger.error("Cardio plan generation failed: %s", error_msg)
                # Create placeholder sessions
                for slot in cardio_slots:
                    planned = PlannedSession(
                        plan_id=plan.id,
                        day_of_week=slot["day_of_week"],
                        sport="running",
                        title="Cardio Session (plan generation failed)",
                        duration_minutes=None,
                        notes="LLM plan generation failed — please reschedule.",
                        track="cardio",
                    )
                    session.add(planned)

        # 6. Finalize plan
        plan.summary = " | ".join(summaries) if summaries else "Weekly training plan"
        plan.raw_llm_output = "\n---\n".join(all_raw_outputs) if all_raw_outputs else None

        await session.commit()
        await session.refresh(plan)

        return plan

    async def generate_post_workout_analysis(
        self,
        session: AsyncSession,
        user_id: int,
        activity_id: int,
        force: bool = False,
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
            if force:
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.activity_id == activity_id,
                        CoachingInsight.insight_type == "post_workout",
                    )
                )
            else:
                raise ValueError(
                    f"Post-workout analysis already exists for activity {activity_id}"
                )

        # Gather context
        activity_dict, gym_details = await get_activity_with_details(session, activity_id, user_id)
        planned_session = await find_matching_planned_session(session, activity_dict, user_id)
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
                        user_message=user_message + "\n\nIMPORTANT: Respond ONLY with valid JSON.",
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
            await link_activity_to_planned_session(session, activity_id, planned_session["id"])

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

    async def generate_weekly_recap(
        self,
        session: AsyncSession,
        user_id: int,
        week_start: date,
        force: bool = False,
    ) -> CoachingInsight:
        """Generate a weekly training recap for a completed week.

        Gathers plan adherence, activities, health trends, and mesocycle context,
        calls the LLM, validates the response, stores as CoachingInsight, and logs the prompt.

        Returns the saved CoachingInsight.
        """
        if week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday")

        # Check for existing recap for this week
        existing = await session.execute(
            select(CoachingInsight).where(
                CoachingInsight.user_id == user_id,
                CoachingInsight.insight_date == week_start,
                CoachingInsight.insight_type == "weekly_recap",
            )
        )
        if existing.scalar_one_or_none() is not None:
            if force:
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.insight_date == week_start,
                        CoachingInsight.insight_type == "weekly_recap",
                    )
                )
            else:
                raise ValueError(f"Weekly recap already exists for week of {week_start}")

        # Gather context
        plan_adherence = await get_plan_adherence_for_week(session, user_id, week_start)
        weekly_activities = await get_activities_for_week(session, user_id, week_start)
        health_trends = await get_health_trends(session, user_id, days=7, today=week_start)
        mesocycle_ctx = await get_mesocycle_context(session, user_id)
        plan_history = await get_recent_plan_summaries(
            session, user_id, weeks=4, before_date=week_start
        )
        routine = await get_active_routine(session, user_id)
        availability = await get_availability_for_week(session, user_id, week_start)
        sport_profiles = await get_sport_profiles(session, user_id)
        weekly_gym_details = await get_gym_details_for_week(session, user_id, week_start)
        gym_history = await get_gym_performance_history(session, user_id, week_start, weeks=3)

        # Build prompts
        system_prompt = get_system_prompt(PROMPT_VERSION)
        user_message = build_weekly_recap_prompt(
            week_start=week_start,
            plan_adherence=plan_adherence,
            weekly_activities=weekly_activities,
            health_trends=health_trends,
            mesocycle_context=mesocycle_ctx,
            plan_history=plan_history,
            routine=routine,
            availability=availability,
            weekly_gym_details=weekly_gym_details,
            gym_history=gym_history,
            sport_profiles=sport_profiles,
            version=PROMPT_VERSION,
        )

        # Call LLM (daily model for cost efficiency)
        llm_response, parsed, error_msg = self._call_llm_with_retry(
            system=system_prompt,
            user_message=user_message,
            model=self._llm.daily_model,
            max_tokens=8192,
            response_model=WeeklyRecapResponse,
        )

        # Log the prompt call
        prompt_log = PromptLog(
            prompt_type="weekly_recap",
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
            raise RuntimeError(f"Failed to generate weekly recap: {error_msg}")

        # Store the coaching insight
        content_json = parsed.model_dump_json()
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=week_start,
            insight_type="weekly_recap",
            content=content_json,
            prompt_version=PROMPT_VERSION,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)

        return insight
