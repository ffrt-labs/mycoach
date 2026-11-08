"""Coaching engine — orchestrates LLM calls for generating coaching insights."""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.context import (
    find_matching_planned_session,
    get_active_routine,
    get_activities_for_week,
    get_activity_with_details,
    get_availability_for_week,
    get_default_availability,
    get_gym_details_for_week,
    get_gym_performance_history,
    get_health_trends,
    get_health_trends_averaged,
    get_last_week_all_activities,
    get_last_week_cardio_performance,
    get_mesocycle_context,
    get_plan_adherence_for_week,
    get_recent_activities,
    get_similar_activities,
    get_sport_profiles,
    get_today_health,
    get_today_planned_sessions,
)
from mycoach.coaching.exceptions import (
    InsufficientHealthData,
    NoAvailabilityConfigured,
    PipelineSkip,
)
from mycoach.coaching.llm_client import LLMClient, LLMResponse, get_llm_client
from mycoach.coaching.prompt_builder import (
    Operation,
    cardio_plan_variables,
    daily_briefing_variables,
    gym_adjustment_variables,
    post_workout_variables,
    weekly_recap_variables,
)
from mycoach.coaching.prompt_store import PromptStore, ServedPrompt, get_prompt_store
from mycoach.coaching.recovery_data import missing_recovery_data
from mycoach.coaching.response_parser import (
    CardioPlanResponse,
    DailyBriefingResponse,
    GymAdjustmentExercise,
    GymAdjustmentResponse,
    PostWorkoutResponse,
    WeeklyRecapResponse,
    parse_response,
)
from mycoach.models.availability import WeeklyAvailability
from mycoach.models.coaching import CoachingInsight
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.prompt_log import PromptLog
from mycoach.observability.scores import Score, ScoreSink, score_sink_for, score_timestamp
from mycoach.observability.trace_ids import trace_id, weekly_plan_seed
from mycoach.observability.tracing import (
    GenerationStart,
    RunSpec,
    Tracer,
    daily_briefing_run,
    get_tracer,
    post_workout_run,
    weekly_plan_run,
    weekly_recap_run,
)
from mycoach.plan_linking import link_activity_to_planned_session

logger = logging.getLogger(__name__)

# The template version a weekly plan's legacy ``prompt_version`` records; the
# prompt actually served is recorded per track (``gym_prompt_version``, ...).
PROMPT_VERSION = "v2"

T = TypeVar("T", bound=BaseModel)


@dataclass
class Generation(Generic[T]):
    """Outcome of one coaching generation (or of a single attempt within it).

    ``parsed`` is ``None`` when the generation failed; ``error`` then says why
    and ``exception`` is what was raised (a typed provider error, or the parse
    error), kept so callers can tell failures apart.
    ``response`` is the last LLM response received, if any. ``retried`` and
    ``truncated`` say whether a second attempt was made and whether any
    attempt's response hit the token limit.
    """

    response: LLMResponse | None
    parsed: T | None
    error: str | None
    exception: BaseException | None = None
    retried: bool = False
    truncated: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _truncated(response: LLMResponse | None) -> bool:
    return response is not None and response.stop_reason == "max_tokens"


class CoachingEngine:
    """Orchestrates coaching insight generation."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tracer: Tracer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        scores: ScoreSink | None = None,
        prompts: PromptStore | None = None,
    ) -> None:
        self._llm = llm_client or get_llm_client()
        self._tracer = tracer or get_tracer()
        self._prompts = prompts or get_prompt_store()
        # Stamps the trace seed of a forced regeneration and the code-check scores.
        self._clock = clock
        self._scores = scores or score_sink_for(self._tracer)

    @property
    def _provider(self) -> str:
        """The provider name recorded on each generation."""
        name = getattr(type(self._llm), "provider_name", None)
        return name if isinstance(name, str) and name else type(self._llm).__name__

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
        replaced = False
        if existing.scalar_one_or_none() is not None:
            if force:
                replaced = True
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.insight_date == today,
                        CoachingInsight.insight_type == "daily_briefing",
                    )
                )
            else:
                raise PipelineSkip(f"Daily briefing already exists for {today}")

        # Gather context
        health_today = await get_today_health(session, user_id, today)

        # The empty-data guard lives here, not in the scheduler job, so cron,
        # retry loop and dashboard button cannot drift apart on what counts as
        # a day worth briefing about. ``force`` remains the explicit escape
        # hatch, as it is for the already-exists check above.
        if not force:
            missing = missing_recovery_data(health_today)
            if missing is not None:
                raise InsufficientHealthData(missing)

        health_trends = await get_health_trends(session, user_id, days=3, today=today)
        recent_activities = await get_recent_activities(session, user_id, days=3, today=today)
        planned_sessions = await get_today_planned_sessions(session, user_id, today)

        # Build prompts
        variables = daily_briefing_variables(
            health_today=health_today,
            health_trends=health_trends,
            recent_activities=recent_activities,
            planned_workout=planned_sessions if planned_sessions else None,
        )
        prompt = await self._serve(Operation.DAILY_BRIEFING, variables)

        now = self._clock()
        run = daily_briefing_run(user_id, today, regenerated_at=now if replaced else None)
        with self._tracer.run(run):
            generation = await self._generate(
                session,
                operation="daily_briefing",
                prompt=prompt,
                variables=variables,
                model=self._llm.daily_model,
                response_model=DailyBriefingResponse,
            )
            self._score_code_checks(
                run, generation, operation="daily_briefing", at=score_timestamp(run.about, now)
            )
            parsed = generation.parsed

            if parsed is None:
                await session.commit()
                raise RuntimeError(f"Failed to generate daily briefing: {generation.error}")

        # Store the coaching insight
        content_json = parsed.model_dump_json()
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=today,
            insight_type="daily_briefing",
            content=content_json,
            prompt_version=prompt.artifact_version,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)

        return insight

    async def _serve(self, operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
        """The operation's prompt, compiled. Off the event loop: a cache miss
        fetches from Langfuse synchronously."""
        return await asyncio.to_thread(self._prompts.serve, operation, variables)

    def _score_code_checks(
        self,
        run: RunSpec,
        generation: Generation[T],
        *,
        operation: str,
        at: datetime,
        qualifier: str | None = None,
        metadata: Mapping[str, object] | None = None,
        fallback_used: bool | None = None,
    ) -> None:
        """Emit the code-check scores of one generation on the run's trace.

        The checks observe values the run already computed and never steer
        it. With no answer at all (the provider failed) there is nothing to
        check, so only ``fallback_used`` can still be emitted. ``qualifier``
        tells apart several generations on one trace.
        """
        meta = {"operation": operation, **(metadata or {})}
        checks: list[tuple[str, bool]] = []
        if generation.response is not None:
            checks += [
                ("parse_ok", generation.parsed is not None),
                ("needed_retry", generation.retried),
                ("truncated", generation.truncated),
            ]
        if fallback_used is not None:
            checks.append(("fallback_used", fallback_used))
        for name, value in checks:
            self._emit(
                Score.boolean(
                    run.trace_id, name, value, timestamp=at, qualifier=qualifier, metadata=meta
                )
            )

    def _score_plan_completion(
        self, plan_adherence: dict[str, Any] | None, *, at: datetime
    ) -> None:
        """Emit ``plan_sessions_completed_ratio`` on the recapped week's plan trace.

        A skipped session never reaches prescription fulfillment, so only the
        recap, looking back at the whole week, can count it against the plan.
        No plan, or a plan with no sessions, has no ratio.
        """
        if not plan_adherence or not plan_adherence["total_sessions"]:
            return
        completed = plan_adherence["completed_sessions"]
        planned = plan_adherence["total_sessions"]
        self._emit(
            Score.numeric(
                trace_id(weekly_plan_seed(plan_adherence["plan_id"])),
                "plan_sessions_completed_ratio",
                completed / planned,
                timestamp=at,
                metadata={
                    "operation": "weekly_recap",
                    "completed_sessions": completed,
                    "planned_sessions": planned,
                },
            )
        )

    def _emit(self, score: Score) -> None:
        try:
            self._scores.emit(score)
        except Exception:
            logger.warning(
                "Langfuse: sending the %s score failed; the coaching run continues",
                score.name,
                exc_info=True,
            )

    async def _call_llm(
        self,
        *,
        system: str,
        user_message: str,
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Run the synchronous provider call on a worker thread.

        A provider call can take a minute or more (the Claude Code CLI), and
        the manual generate routes await the engine on the web server's event
        loop. Calling the provider inline would freeze every other request,
        the health check included, until it returned.
        """
        return await asyncio.to_thread(
            self._llm.call,
            system=system,
            user_message=user_message,
            model=model,
            max_tokens=max_tokens,
        )

    async def _generate(
        self,
        session: AsyncSession,
        *,
        operation: str,
        prompt: ServedPrompt,
        variables: Mapping[str, str],
        model: str,
        response_model: type[T],
        max_tokens: int = 4096,
    ) -> Generation[T]:
        """The one generation seam every coaching operation goes through.

        Calls the LLM, parses the response, retries once on a parse failure
        (doubling the token budget when the first response was truncated and
        telling the model what was wrong), and writes the single ``PromptLog``
        row for the operation. ``operation`` names what is being served
        (``daily_briefing``, ``gym_adjustment``, ...) and becomes the log's
        ``prompt_type``. Each attempt runs through ``_attempt``, which records
        it as its own generation in the current trace; ``variables`` (the
        prompt's variable map) is that generation's input, and ``prompt``
        (the compiled messages and where they came from) is linked to it.

        Never raises for an LLM or parse failure: the caller reads
        ``Generation.parsed`` (``None`` on failure) and ``Generation.error``.
        """
        user_message = prompt.user
        result = await self._attempt(
            operation=operation,
            attempt=1,
            prompt=prompt,
            user_message=user_message,
            variables=variables,
            model=model,
            max_tokens=max_tokens,
            response_model=response_model,
        )

        # A provider failure (no response at all) is not retried: only a
        # response that came back but did not parse earns a second attempt.
        if result.parsed is None and result.response is not None:
            first_error = result.error
            retry_tokens = max_tokens
            if result.response.stop_reason == "max_tokens":
                retry_tokens = max_tokens * 2
                logger.warning(
                    "Response truncated (stop_reason=max_tokens, used %d tokens). "
                    "Retrying with max_tokens=%d",
                    result.response.output_tokens,
                    retry_tokens,
                )
            retry = await self._attempt(
                operation=operation,
                attempt=2,
                prompt=prompt,
                user_message=(
                    user_message
                    + "\n\nIMPORTANT: Respond ONLY with valid JSON. "
                    + f"Your previous response had this error: {first_error}"
                ),
                variables=variables,
                model=model,
                max_tokens=retry_tokens,
                response_model=response_model,
                previous_error=first_error,
            )
            result = Generation(
                response=retry.response or result.response,
                parsed=retry.parsed,
                error=None if retry.parsed is not None else f"Retry also failed: {retry.error}",
                exception=retry.exception,
                retried=True,
                truncated=_truncated(result.response) or _truncated(retry.response),
            )
        elif result.response is not None:
            result.truncated = _truncated(result.response)

        response = result.response
        session.add(
            PromptLog(
                prompt_type=operation,
                prompt_version=prompt.artifact_version,
                model=response.model if response else model,
                input_tokens=response.input_tokens if response else None,
                output_tokens=response.output_tokens if response else None,
                latency_ms=response.latency_ms if response else None,
                estimated_cost_usd=response.estimated_cost_usd if response else None,
                prompt_text=user_message,
                response_text=response.content if response else None,
                success=result.parsed is not None,
                error=result.error,
            )
        )
        return result

    async def _attempt(
        self,
        *,
        operation: str,
        attempt: int,
        prompt: ServedPrompt,
        user_message: str,
        variables: Mapping[str, str],
        model: str,
        max_tokens: int,
        response_model: type[T],
        previous_error: str | None = None,
    ) -> Generation[T]:
        """One call-and-parse attempt, recorded as one generation.

        ``attempt`` is 1 for the first try, 2 for the retry; a retry carries
        the parse error of the attempt it follows as ``previous_error``. The
        generation is opened and ended here, on the event loop, so the
        provider's worker thread never touches the tracer.
        """
        traced = self._tracer.generation(
            GenerationStart(
                operation=operation,
                attempt=attempt,
                provider=self._provider,
                model=model,
                max_tokens=max_tokens,
                input=variables,
                previous_error=previous_error,
                metadata={
                    "prompt_source": prompt.source,
                    "prompt_version": prompt.artifact_version,
                },
                prompt=prompt.link,
            )
        )
        response: LLMResponse | None = None
        try:
            response = await self._call_llm(
                system=prompt.system,
                user_message=user_message,
                model=model,
                max_tokens=max_tokens,
            )
            parsed = parse_response(response.content, response_model)
        except Exception as e:
            logger.error("%s attempt %d failed: %s", operation, attempt, e)
            traced.failed(error=str(e), exception=e, response=response)
            return Generation(response=response, parsed=None, error=str(e), exception=e)
        traced.succeeded(response)
        return Generation(response=response, parsed=parsed, error=None)

    async def _materialize_availability(
        self, session: AsyncSession, user_id: int, week_start: date
    ) -> str:
        """Ensure ``week_start`` has availability rows, materializing from the
        standing default schedule if none were declared.

        This is the single seam every weekly-plan generation path passes
        through, so the scheduled run and manual regenerate can't disagree
        about what the week's availability is. Returns ``"declared"`` if the
        week already had rows, ``"default"`` if rows were copied in from
        ``DefaultAvailability``. Raises ``NoAvailabilityConfigured`` if the
        week has no rows and the default is also empty — nothing is written
        in that case.
        """
        existing = await session.execute(
            select(WeeklyAvailability.id).where(
                WeeklyAvailability.user_id == user_id,
                WeeklyAvailability.week_start == week_start,
            )
        )
        if existing.first() is not None:
            return "declared"

        default_slots = await get_default_availability(session, user_id)
        if not default_slots:
            raise NoAvailabilityConfigured(f"No availability configured for week of {week_start}")

        for slot in default_slots:
            session.add(
                WeeklyAvailability(
                    user_id=user_id,
                    week_start=week_start,
                    day_of_week=slot.day_of_week,
                    sport=slot.sport,
                )
            )
        await session.flush()
        return "default"

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
                await session.execute(
                    delete(PlannedSession).where(PlannedSession.plan_id == existing_plan.id)
                )
                await session.delete(existing_plan)
                await session.flush()
            else:
                raise PipelineSkip(f"Active plan already exists for week of {week_start}")

        # 1. Gather shared context
        availability_source = await self._materialize_availability(session, user_id, week_start)
        availability = await get_availability_for_week(session, user_id, week_start)
        logger.info(
            "Availability for week %s: %s",
            week_start,
            [(s["day_name"], s["sport"]) for s in availability],
        )

        health_trends_avg = await get_health_trends_averaged(
            session, user_id, days=7, today=week_start
        )
        mesocycle_ctx = await get_mesocycle_context(session, user_id)

        # 2. Fetch routine + sport profiles + last week's full training log
        routine = await get_active_routine(session, user_id)
        sport_profiles = await get_sport_profiles(session, user_id)
        last_week_activities = await get_last_week_all_activities(session, user_id, week_start)

        # 3. Split slots by user-assigned sport
        gym_slots = [s for s in availability if s.get("sport") == "gym"]
        cardio_slots = [s for s in availability if s.get("sport") != "gym"]

        routine_days = routine["days"] if routine else []
        sorted_routine_days = sorted(routine_days, key=lambda d: d.get("order_index", 0))

        # Create the plan record
        plan = WeeklyPlan(
            user_id=user_id,
            week_start=week_start,
            prompt_version=PROMPT_VERSION,
            status="active",
            availability_source=availability_source,
        )
        session.add(plan)
        await session.flush()

        all_raw_outputs: list[str] = []
        summaries: list[str] = []

        # One trace for the whole plan: a generation per gym slot plus the
        # cardio generation. plan.id exists now, after the flush above.
        run = weekly_plan_run(plan.id, user_id, week_start)
        scored_at = score_timestamp(run.about, self._clock())
        with self._tracer.run(run):
            # 4. GYM TRACK — one LLM call per gym slot (cycle routine days if needed)
            for i, slot in enumerate(gym_slots):
                if not sorted_routine_days:
                    logger.warning(
                        "No routine days defined, skipping gym slot day %d",
                        slot["day_of_week"],
                    )
                    continue
                routine_day = sorted_routine_days[i % len(sorted_routine_days)]

                variables = gym_adjustment_variables(
                    routine_day_name=routine_day["name"],
                    routine_exercises=routine_day["exercises"],
                    health_trends=health_trends_avg,
                    last_week_activities=last_week_activities,
                    mesocycle_context=mesocycle_ctx,
                    sport_profiles=sport_profiles,
                )
                prompt = await self._serve(Operation.GYM_ADJUSTMENT, variables)
                plan.gym_prompt_version = prompt.artifact_version

                gym = await self._generate(
                    session,
                    operation="gym_adjustment",
                    prompt=prompt,
                    variables=variables,
                    model=self._llm.daily_model,
                    max_tokens=8192,
                    response_model=GymAdjustmentResponse,
                )

                self._score_code_checks(
                    run,
                    gym,
                    operation="gym_adjustment",
                    at=scored_at,
                    qualifier=f"gym_adjustment:{i}",
                    metadata={"day_of_week": slot["day_of_week"]},
                    fallback_used=gym.parsed is None,
                )

                if gym.response and gym.response.content:
                    all_raw_outputs.append(gym.response.content)

                if gym.parsed is not None:
                    parsed = gym.parsed
                    # Build from the routine so a hallucinated response can never
                    # invent a prescribed exercise. Names remain display labels;
                    # the echoed catalogue id is the only matching key.
                    exercises_detail = []
                    known_ids = {
                        ex["exercise_id"]
                        for ex in routine_day["exercises"]
                        if ex.get("exercise_id") is not None
                    }
                    adjustments: dict[str, GymAdjustmentExercise] = {}
                    for adjustment in parsed.exercises:
                        if (
                            adjustment.exercise_id is None
                            or adjustment.exercise_id not in known_ids
                        ):
                            logger.warning(
                                "Ignoring gym adjustment for unknown exercise_id %r",
                                adjustment.exercise_id,
                            )
                            continue
                        adjustments.setdefault(adjustment.exercise_id, adjustment)

                    for routine_ex in routine_day["exercises"]:
                        ex = adjustments.get(routine_ex.get("exercise_id"))
                        exercises_detail.append(
                            {
                                "exercise_id": routine_ex.get("exercise_id"),
                                "name": routine_ex["exercise_name"],
                                "target_weight_kg": ex.target_weight_kg if ex else None,
                                "sets": routine_ex["sets"],
                                "reps": routine_ex["rep_range"],
                                "rpe": ex.target_rpe if ex else None,
                                "rest_seconds": ex.rest_seconds if ex else None,
                                "adjustment_rationale": ex.adjustment_rationale if ex else None,
                                "notes": ex.notes if ex else routine_ex.get("notes"),
                                "superset_group": routine_ex.get("superset_group"),
                            }
                        )

                    details = {"exercises": exercises_detail}
                    planned = PlannedSession(
                        plan_id=plan.id,
                        day_of_week=slot["day_of_week"],
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
                    exercises_detail = [
                        {
                            "exercise_id": ex.get("exercise_id"),
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
                        day_of_week=slot["day_of_week"],
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

                variables = cardio_plan_variables(
                    cardio_slots=cardio_slots,
                    last_week_cardio=last_cardio,
                    health_trends=health_trends_list,
                    mesocycle_context=mesocycle_ctx,
                    sport_profiles=sport_profiles,
                )
                prompt = await self._serve(Operation.CARDIO_PLAN, variables)
                plan.cardio_prompt_version = prompt.artifact_version

                cardio = await self._generate(
                    session,
                    operation="cardio_plan",
                    prompt=prompt,
                    variables=variables,
                    model=self._llm.weekly_model,
                    max_tokens=8192,
                    response_model=CardioPlanResponse,
                )

                self._score_code_checks(
                    run, cardio, operation="cardio_plan", at=scored_at, qualifier="cardio_plan"
                )

                if cardio.response and cardio.response.content:
                    all_raw_outputs.append(cardio.response.content)

                if cardio.parsed is not None:
                    cardio_plan = cardio.parsed
                    summaries.append(cardio_plan.weekly_summary)
                    # Match LLM sessions to slots by sport — don't trust LLM's day_of_week
                    # Group LLM sessions by sport for matching
                    llm_by_sport: dict[str, list] = {}
                    for s in cardio_plan.sessions:
                        llm_by_sport.setdefault(s.sport, []).append(s)

                    for slot in cardio_slots:
                        sport = slot["sport"]
                        candidates = llm_by_sport.get(sport, [])
                        if candidates:
                            s = candidates.pop(0)
                            if s.day_of_week != slot["day_of_week"]:
                                logger.warning(
                                    "LLM returned day_of_week=%d for %s, slot is day %d"
                                    " — using slot",
                                    s.day_of_week,
                                    sport,
                                    slot["day_of_week"],
                                )
                            planned = PlannedSession(
                                plan_id=plan.id,
                                day_of_week=slot["day_of_week"],
                                sport=sport,
                                title=s.title,
                                duration_minutes=s.duration_minutes,
                                details=json.dumps(s.details) if s.details else None,
                                notes=s.notes,
                                track="cardio",
                            )
                        else:
                            logger.warning(
                                "No LLM session for %s on day %d — creating placeholder",
                                sport,
                                slot["day_of_week"],
                            )
                            planned = PlannedSession(
                                plan_id=plan.id,
                                day_of_week=slot["day_of_week"],
                                sport=sport,
                                title=f"{sport.capitalize()} Session",
                                duration_minutes=None,
                                notes="LLM did not generate a session for this slot.",
                                track="cardio",
                            )
                        session.add(planned)
                else:
                    logger.error("Cardio plan generation failed: %s", cardio.error)
                    # Create placeholder sessions
                    for slot in cardio_slots:
                        planned = PlannedSession(
                            plan_id=plan.id,
                            day_of_week=slot["day_of_week"],
                            sport=slot["sport"],
                            title=f"{slot['sport'].capitalize()} Session (plan generation failed)",
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
        replaced = False
        if existing.scalar_one_or_none() is not None:
            if force:
                replaced = True
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.activity_id == activity_id,
                        CoachingInsight.insight_type == "post_workout",
                    )
                )
            else:
                raise PipelineSkip(
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
        variables = post_workout_variables(
            activity=activity_dict,
            gym_details=gym_details,
            planned_session=planned_session,
            similar_activities=similar,
            health_context=health_ctx,
        )
        prompt = await self._serve(Operation.POST_WORKOUT, variables)

        # The week session follows the activity's date, not the run's.
        now = self._clock()
        run = post_workout_run(
            activity_id,
            user_id,
            activity_date,
            regenerated_at=now if replaced else None,
        )
        with self._tracer.run(run):
            generation = await self._generate(
                session,
                operation="post_workout",
                prompt=prompt,
                variables=variables,
                model=self._llm.daily_model,
                response_model=PostWorkoutResponse,
            )
            self._score_code_checks(
                run, generation, operation="post_workout", at=score_timestamp(run.about, now)
            )
            parsed = generation.parsed

            if parsed is None:
                await session.commit()
                raise RuntimeError(f"Failed to generate post-workout analysis: {generation.error}")

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
            prompt_version=prompt.artifact_version,
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
        replaced = False
        if existing.scalar_one_or_none() is not None:
            if force:
                replaced = True
                await session.execute(
                    delete(CoachingInsight).where(
                        CoachingInsight.user_id == user_id,
                        CoachingInsight.insight_date == week_start,
                        CoachingInsight.insight_type == "weekly_recap",
                    )
                )
            else:
                raise PipelineSkip(f"Weekly recap already exists for week of {week_start}")

        # Gather context
        plan_adherence = await get_plan_adherence_for_week(session, user_id, week_start)
        weekly_activities = await get_activities_for_week(session, user_id, week_start)
        health_trends = await get_health_trends(session, user_id, days=7, today=week_start)
        mesocycle_ctx = await get_mesocycle_context(session, user_id)
        routine = await get_active_routine(session, user_id)
        availability = await get_availability_for_week(session, user_id, week_start)
        sport_profiles = await get_sport_profiles(session, user_id)
        weekly_gym_details = await get_gym_details_for_week(session, user_id, week_start)
        gym_history = await get_gym_performance_history(session, user_id, week_start, weeks=3)

        # Build prompts
        variables = weekly_recap_variables(
            week_start=week_start,
            plan_adherence=plan_adherence,
            weekly_activities=weekly_activities,
            health_trends=health_trends,
            mesocycle_context=mesocycle_ctx,
            routine=routine,
            availability=availability,
            weekly_gym_details=weekly_gym_details,
            gym_history=gym_history,
            sport_profiles=sport_profiles,
        )
        prompt = await self._serve(Operation.WEEKLY_RECAP, variables)

        # week_start is the recapped week, so the recap joins that week's session.
        now = self._clock()
        # Midnight of the recapped Monday, so a regenerated recap overwrites the ratio.
        self._score_plan_completion(plan_adherence, at=score_timestamp(week_start, now))
        run = weekly_recap_run(user_id, week_start, regenerated_at=now if replaced else None)
        with self._tracer.run(run):
            # Call LLM (daily model for cost efficiency)
            generation = await self._generate(
                session,
                operation="weekly_recap",
                prompt=prompt,
                variables=variables,
                model=self._llm.daily_model,
                max_tokens=8192,
                response_model=WeeklyRecapResponse,
            )
            self._score_code_checks(
                run, generation, operation="weekly_recap", at=score_timestamp(run.about, now)
            )
            parsed = generation.parsed

            if parsed is None:
                await session.commit()
                raise RuntimeError(f"Failed to generate weekly recap: {generation.error}")

        # Store the coaching insight
        content_json = parsed.model_dump_json()
        insight = CoachingInsight(
            user_id=user_id,
            insight_date=week_start,
            insight_type="weekly_recap",
            content=content_json,
            prompt_version=prompt.artifact_version,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)

        return insight
