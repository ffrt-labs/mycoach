"""Which prompt a coaching run used, on its generations and its artifacts."""

from collections.abc import Mapping
from datetime import datetime
from unittest.mock import MagicMock

from langfuse.api import ChatMessage, Prompt_Chat
from langfuse.model import ChatPromptClient
from sqlalchemy import select

from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.prompt_builder import Operation
from mycoach.coaching.prompt_store import (
    LANGFUSE_PROMPT_NAMES,
    BundledPrompts,
    ServedPrompt,
    bundled_prompt,
)
from mycoach.models.activity import Activity
from mycoach.models.prompt_log import PromptLog
from mycoach.models.user import User
from tests.conftest import test_session as new_session
from tests.test_coaching.test_engine import _add_recovery_snapshot
from tests.test_coaching.test_engine import _create_user as _create_briefing_user
from tests.test_coaching.test_engine import _mock_llm_client as _briefing_llm
from tests.test_observability.conftest import Captured, attr, json_attr
from tests.test_observability.test_engine_tracing import (
    MONDAY,
    _gym_response,
    _setup_two_gym_one_cardio,
)
from tests.test_plans.test_engine import VALID_CARDIO_RESPONSE, VALID_GYM_ADJUSTMENT
from tests.test_post_workout.test_engine import _mock_llm_client as _post_workout_llm
from tests.test_weekly_recap.test_engine import _mock_llm_client as _recap_llm


class FromLangfuse:
    """Serves the bundled text as if it were Langfuse version ``versions[op]``."""

    def __init__(self, versions: Mapping[Operation, int]) -> None:
        self.versions = versions
        self.served: list[Operation] = []

    def serve(self, operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
        self.served.append(operation)
        bundled = bundled_prompt(operation, variables)
        if operation not in self.versions:
            return bundled
        name = LANGFUSE_PROMPT_NAMES[operation]
        version = self.versions[operation]
        link = ChatPromptClient(
            Prompt_Chat(
                name=name,
                version=version,
                prompt=[
                    ChatMessage(role="system", content="s"),
                    ChatMessage(role="user", content="u"),
                ],
                config={},
                labels=["production"],
                tags=[],
            )
        )
        return ServedPrompt(
            system=bundled.system,
            user=bundled.user,
            source="langfuse",
            name=name,
            version=version,
            link=link,
        )


class TestGenerationsShowThePrompt:
    async def test_langfuse_prompt_is_linked_and_marked(self, captured: Captured) -> None:
        prompts = FromLangfuse({Operation.DAILY_BRIEFING: 3})
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(
                llm_client=_briefing_llm(), tracer=captured.tracer, prompts=prompts
            )
            insight = await engine.generate_daily_briefing(session, user_id, MONDAY)
            [log] = (await session.execute(select(PromptLog))).scalars()

        [gen] = captured.generations()
        assert attr(gen, "langfuse.observation.prompt.name") == "mycoach/daily-briefing"
        assert attr(gen, "langfuse.observation.prompt.version") == 3
        assert json_attr(gen, "langfuse.observation.metadata.prompt_source") == "langfuse"
        assert insight.prompt_version == "lf:3"
        assert log.prompt_version == "lf:3"

    async def test_bundled_prompt_is_marked_and_not_linked(self, captured: Captured) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            await _add_recovery_snapshot(session, user_id, MONDAY)
            engine = CoachingEngine(
                llm_client=_briefing_llm(), tracer=captured.tracer, prompts=BundledPrompts()
            )
            insight = await engine.generate_daily_briefing(session, user_id, MONDAY)

        [gen] = captured.generations()
        assert attr(gen, "langfuse.observation.prompt.name") is None
        assert json_attr(gen, "langfuse.observation.metadata.prompt_source") == "bundled"
        assert insight.prompt_version == "bundled:v2"


class TestArtifactsRecordThePrompt:
    async def test_weekly_plan_records_gym_and_cardio_versions(self, captured: Captured) -> None:
        llm = MagicMock()
        llm.daily_model = "claude-sonnet-4-5-20250929"
        llm.weekly_model = "claude-opus-4-6"
        llm.call.side_effect = [
            _gym_response(VALID_GYM_ADJUSTMENT),
            _gym_response(VALID_GYM_ADJUSTMENT),
            _gym_response(VALID_CARDIO_RESPONSE),
        ]
        prompts = FromLangfuse({Operation.GYM_ADJUSTMENT: 4})
        async with new_session() as session:
            user_id = await _setup_two_gym_one_cardio(session)
            engine = CoachingEngine(llm_client=llm, tracer=captured.tracer, prompts=prompts)
            plan = await engine.generate_weekly_plan(session, user_id, MONDAY)

        assert plan.gym_prompt_version == "lf:4"
        assert plan.cardio_prompt_version == "bundled:v2"
        gym, _, cardio = captured.generations()
        assert attr(gym, "langfuse.observation.prompt.version") == 4
        assert json_attr(cardio, "langfuse.observation.metadata.prompt_source") == "bundled"

    async def test_post_workout_insight_records_its_version(self, captured: Captured) -> None:
        async with new_session() as session:
            user = User(email="t@example.com", name="T", fitness_level="intermediate")
            session.add(user)
            await session.flush()
            activity = Activity(
                user_id=user.id,
                sport="running",
                title="Easy run",
                start_time=datetime(2024, 6, 6, 9, 0),
                duration_minutes=40,
                data_source="garmin",
            )
            session.add(activity)
            await session.commit()
            engine = CoachingEngine(
                llm_client=_post_workout_llm(),
                tracer=captured.tracer,
                prompts=FromLangfuse({Operation.POST_WORKOUT: 12}),
            )
            insight = await engine.generate_post_workout_analysis(session, user.id, activity.id)

        assert insight.prompt_version == "lf:12"

    async def test_weekly_recap_insight_records_its_version(self, captured: Captured) -> None:
        async with new_session() as session:
            user_id = await _create_briefing_user(session)
            engine = CoachingEngine(
                llm_client=_recap_llm(),
                tracer=captured.tracer,
                prompts=FromLangfuse({Operation.WEEKLY_RECAP: 2}),
            )
            insight = await engine.generate_weekly_recap(session, user_id, MONDAY)

        assert insight.prompt_version == "lf:2"


async def test_engine_serves_bundled_prompts_when_langfuse_is_off() -> None:
    """The root conftest's tracer is a no-op, so the default store is bundled."""
    async with new_session() as session:
        user_id = await _create_briefing_user(session)
        await _add_recovery_snapshot(session, user_id, MONDAY)
        insight = await CoachingEngine(llm_client=_briefing_llm()).generate_daily_briefing(
            session, user_id, MONDAY
        )

    assert insight.prompt_version == "bundled:v2"
