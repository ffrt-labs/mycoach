"""The strict JSON schema the v5 contract sends to a provider (spec #230, seam 2).

OpenRouter's strict structured outputs (OpenAI's rules) need every property listed
in ``required``, optional fields as nullable, and ``additionalProperties: false`` on
every object. Pydantic stays the gate; the schema only steers the model.
"""

from typing import Any

from mycoach.coaching.email_schemas import PostWorkoutEmail
from mycoach.coaching.strict_schema import strict_json_schema


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every object schema in ``node``, nested ones included."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


def test_every_object_requires_all_its_properties_and_allows_no_others() -> None:
    schema = strict_json_schema(PostWorkoutEmail)

    objects = _objects(schema)
    assert schema in objects
    # PostWorkoutEmail, Headline, Action, Tip, Block, Guardrail
    assert len(objects) == 6
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_an_optional_field_is_nullable_not_omittable() -> None:
    schema = strict_json_schema(PostWorkoutEmail)

    week_addition = schema["properties"]["week_addition"]
    assert week_addition == {"anyOf": [{"type": "string", "maxLength": 60}, {"type": "null"}]}
    assert "week_addition" in schema["required"]


def test_nested_models_are_inlined_with_their_length_limits() -> None:
    schema = strict_json_schema(PostWorkoutEmail)

    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    action = schema["properties"]["actions"]["items"]
    assert action["properties"]["do"] == {"type": "string", "maxLength": 70}
    assert schema["properties"]["actions"]["minItems"] == 1
    assert schema["properties"]["actions"]["maxItems"] == 3


def test_no_defaults_or_titles_reach_the_provider() -> None:
    text = str(strict_json_schema(PostWorkoutEmail))

    assert "'default'" not in text
    assert "'title'" not in text
