"""Serving coaching prompts from Langfuse, with the bundled files as fallback.

Runs a real Langfuse client against an in-memory prompts API
(``tests/fake_langfuse.py``) and a hand-driven clock.
"""

from collections.abc import Generator

import pytest
from langfuse import Langfuse

from mycoach.coaching.prompt_builder import Operation, compile_prompt
from mycoach.coaching.prompt_store import BundledPrompts, LangfusePrompts
from tests.fake_langfuse import FakeLangfusePrompts, langfuse_over

NAME = "mycoach/cardio-plan"
VARIABLES = {
    "cardio_slots": "Tue run",
    "sport_profiles": "runner",
    "last_week_cardio": "none",
    "health_trends": "fine",
    "mesocycle_context": "week 2",
}
TEMPLATE = [
    {"role": "system", "content": "Coach."},
    {
        "role": "user",
        "content": (
            "Slots {{cardio_slots}} / {{sport_profiles}} / {{last_week_cardio}}"
            ' / {{health_trends}} / {{ mesocycle_context }} {"json": {"ok": 1}}'
        ),
    },
]
COMPILED_USER = 'Slots Tue run / runner / none / fine / week 2 {"json": {"ok": 1}}'


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake() -> FakeLangfusePrompts:
    return FakeLangfusePrompts()


@pytest.fixture
def client(fake: FakeLangfusePrompts) -> Generator[Langfuse, None, None]:
    with langfuse_over(fake) as lf:
        yield lf


@pytest.fixture
def clock() -> Clock:
    return Clock()


def store(client: Langfuse, clock: Clock) -> LangfusePrompts:
    return LangfusePrompts(client, ttl_seconds=300, clock=clock)


def bundled() -> tuple[str, str]:
    return compile_prompt(Operation.CARDIO_PLAN, VARIABLES)


class TestServedFromLangfuse:
    def test_production_version_is_compiled_and_linked(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.add(NAME, TEMPLATE)
        fake.add(NAME, TEMPLATE, labels=["production", "latest"])

        served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)

        assert (served.system, served.user) == ("Coach.", COMPILED_USER)
        assert served.source == "langfuse"
        assert served.name == NAME
        assert served.version == 2
        assert served.artifact_version == "lf:2"
        assert served.link is not None

    def test_cache_hit_within_ttl_makes_no_request(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.add(NAME, TEMPLATE)
        prompts = store(client, clock)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)
        fake.add(NAME, TEMPLATE)  # promoted, but the cache is still fresh

        clock.now += 299
        served = prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.version == 1
        assert fake.fetches() == 1

    def test_refreshes_after_ttl(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.add(NAME, TEMPLATE)
        prompts = store(client, clock)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)
        fake.add(NAME, TEMPLATE)

        clock.now += 301
        served = prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.version == 2
        assert fake.fetches() == 2

    def test_failed_refresh_keeps_the_stale_version(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.add(NAME, TEMPLATE)
        prompts = store(client, clock)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        fake.down = True
        clock.now += 301
        served = prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.source == "langfuse"
        assert served.version == 1
        # The failed refresh is not retried on every call.
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)
        assert fake.fetches() == 2

    def test_unlabelled_prompt_falls_back_and_drops_the_stale_copy(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        """Removing the production label is deliberate, unlike an outage."""
        fake.add(NAME, TEMPLATE)
        prompts = store(client, clock)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        fake.versions[NAME][0]["labels"] = []
        clock.now += 301
        served = prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.source == "bundled"


class TestBundledFallback:
    def test_cold_start_with_langfuse_down_serves_bundled(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.down = True

        served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)

        assert (served.system, served.user) == bundled()
        assert served.source == "bundled"
        assert served.version is None
        assert served.artifact_version == "bundled:v2"
        assert served.link is None

    def test_cold_failure_is_not_retried_on_every_call(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        """Langfuse down must not add a fetch timeout to every coaching call."""
        fake.down = True
        prompts = store(client, clock)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)
        prompts.serve(Operation.CARDIO_PLAN, VARIABLES)
        assert fake.fetches() == 1

        fake.down = False
        fake.add(NAME, TEMPLATE)
        clock.now += 61
        served = prompts.serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.source == "langfuse"
        assert fake.fetches() == 2

    def test_fetch_is_a_single_short_attempt(self, client: Langfuse, clock: Clock) -> None:
        calls: list[dict[str, object]] = []

        def get_prompt(name: str, **kwargs: object) -> object:
            calls.append(kwargs)
            raise ConnectionError("down")

        client.get_prompt = get_prompt  # type: ignore[method-assign,assignment]
        store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)

        [kwargs] = calls
        assert kwargs["label"] == "production"
        assert kwargs["type"] == "chat"
        assert kwargs["max_retries"] == 0
        assert kwargs["fetch_timeout_seconds"] == 2

    def test_unseeded_prompt_serves_bundled(self, client: Langfuse, clock: Clock) -> None:
        served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)
        assert served.source == "bundled"

    @pytest.mark.parametrize(
        "user",
        [
            "{{cardio_slots}} {{unknown_variable}} {{sport_profiles}} {{last_week_cardio}}"
            " {{health_trends}} {{mesocycle_context}}",
            "{{cardio_slots}} {{sport_profiles}} {{last_week_cardio}} {{health_trends}}"
            " {{mesocycle_context}} {{ not a name }}",
            "{{cardio_slots}} {{sport_profiles}} {{last_week_cardio}} {{health_trends}}"
            " {{mesocycle_context}} {{ unclosed",
        ],
        ids=["unknown-variable", "not-an-identifier", "unclosed"],
    )
    def test_invalid_template_serves_bundled(
        self,
        user: str,
        fake: FakeLangfusePrompts,
        client: Langfuse,
        clock: Clock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake.add(NAME, [{"role": "system", "content": "s"}, {"role": "user", "content": user}])

        served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)

        assert (served.system, served.user) == bundled()
        assert served.source == "bundled"
        assert "mycoach/cardio-plan v1" in caplog.text

    def test_template_that_drops_a_variable_is_served(
        self,
        fake: FakeLangfusePrompts,
        client: Langfuse,
        clock: Clock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # An experiment may leave a variable out; only unknown ones are rejected.
        user = "{{cardio_slots}} {{sport_profiles}} {{last_week_cardio}} {{health_trends}}"
        fake.add(NAME, [{"role": "system", "content": "s"}, {"role": "user", "content": user}])

        with caplog.at_level("DEBUG", logger="mycoach.coaching.prompt_store"):
            served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)

        assert served.source == "langfuse"
        assert served.user == "Tue run runner none fine"
        [record] = [r for r in caplog.records if "mesocycle_context" in r.getMessage()]
        assert record.levelname == "INFO"

    def test_template_not_shaped_system_then_user_serves_bundled(
        self, fake: FakeLangfusePrompts, client: Langfuse, clock: Clock
    ) -> None:
        fake.add(NAME, [TEMPLATE[1], TEMPLATE[0]])
        served = store(client, clock).serve(Operation.CARDIO_PLAN, VARIABLES)
        assert served.source == "bundled"


def test_bundled_store_serves_the_bundled_files() -> None:
    served = BundledPrompts().serve(Operation.CARDIO_PLAN, VARIABLES)

    assert (served.system, served.user) == bundled()
    assert served.source == "bundled"
    assert served.name == NAME
    assert served.artifact_version == "bundled:v2"
