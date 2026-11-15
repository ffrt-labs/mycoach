"""Seeding Langfuse with the bundled prompts, and the seeded prompts' parity."""

from collections.abc import Generator

import pytest
from langfuse import Langfuse

from mycoach.coaching.prompt_builder import PROMPT_VARIABLES, Operation, compile_prompt
from mycoach.coaching.prompt_store import LangfusePrompts, to_langfuse_template
from mycoach.tools.seed_langfuse_prompts import seed_prompts
from tests.fake_langfuse import FakeLangfuseAPI, langfuse_over
from tests.test_coaching.prompt_cases import CASES

ALL_NAMES = [
    "mycoach/cardio-plan",
    "mycoach/daily-briefing",
    "mycoach/gym-adjustment",
    "mycoach/post-workout",
    "mycoach/weekly-recap",
]


@pytest.fixture
def fake() -> FakeLangfuseAPI:
    return FakeLangfuseAPI()


@pytest.fixture
def client(fake: FakeLangfuseAPI) -> Generator[Langfuse, None, None]:
    with langfuse_over(fake) as lf:
        yield lf


def test_seed_creates_version_1_of_each_prompt_labelled_production(
    fake: FakeLangfuseAPI, client: Langfuse
) -> None:
    created = seed_prompts(client)

    assert sorted(created) == ALL_NAMES
    assert sorted(fake.versions) == ALL_NAMES
    for history in fake.versions.values():
        [version] = history
        assert version["version"] == 1
        assert version["labels"] == ["production"]
        assert [m["role"] for m in version["prompt"]] == ["system", "user"]


def test_seeding_twice_is_a_no_op(fake: FakeLangfuseAPI, client: Langfuse) -> None:
    seed_prompts(client)
    posts = sum(1 for r in fake.requests if r.method == "POST")

    assert seed_prompts(client) == []
    assert sum(1 for r in fake.requests if r.method == "POST") == posts


def test_seed_publishes_a_new_production_version_when_the_bundled_text_changed(
    fake: FakeLangfuseAPI, client: Langfuse
) -> None:
    seed_prompts(client)
    fake.add(
        "mycoach/daily-briefing",
        [{"role": "system", "content": "old"}, {"role": "user", "content": "old {{health_data}}"}],
    )

    published = seed_prompts(client)

    assert published == ["mycoach/daily-briefing"]
    history = fake.versions["mycoach/daily-briefing"]
    assert [v["version"] for v in history] == [1, 2, 3]
    assert history[-1]["labels"] == ["production"]
    assert history[-1]["prompt"] == history[0]["prompt"]


def test_seed_publishes_over_a_newer_version_without_the_production_label(
    fake: FakeLangfuseAPI, client: Langfuse
) -> None:
    fake.add("mycoach/weekly-recap", [{"role": "user", "content": "draft"}], labels=["staging"])

    published = seed_prompts(client)

    assert "mycoach/weekly-recap" in published
    history = fake.versions["mycoach/weekly-recap"]
    assert [v["labels"] for v in history] == [["staging"], ["production"]]


def test_seed_leaves_a_prompt_alone_when_its_latest_version_matches_the_bundled_text(
    fake: FakeLangfuseAPI, client: Langfuse
) -> None:
    seed_prompts(client)
    # An operator moved production back to an older version; the latest
    # version still matches the bundled text, so nothing is published.
    fake.versions["mycoach/cardio-plan"][0]["labels"] = []

    assert seed_prompts(client) == []
    assert len(fake.versions["mycoach/cardio-plan"]) == 1


@pytest.mark.parametrize("case", sorted(CASES))
def test_seeded_prompts_compile_to_the_bundled_messages(
    case: str, fake: FakeLangfuseAPI, client: Langfuse
) -> None:
    seed_prompts(client)
    operation, inputs = CASES[case]
    variables = PROMPT_VARIABLES[Operation(operation)](**inputs)

    served = LangfusePrompts(client).serve(Operation(operation), variables)

    assert served.source == "langfuse"
    assert served.version == 1
    assert (served.system, served.user) == compile_prompt(Operation(operation), variables)


def test_conversion_unescapes_json_braces_and_doubles_placeholders() -> None:
    assert to_langfuse_template('{{\n  "a": {{"b": "{x}"}}\n}}\n{y}') == (
        '{\n  "a": {"b": "{{x}}"}\n}\n{{y}}'
    )
