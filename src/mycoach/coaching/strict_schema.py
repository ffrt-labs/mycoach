"""A strict JSON schema for a Coach notes model, as a provider's structured outputs need it.

``model_json_schema()`` as is fails strict mode: it leaves fields with a default
out of ``required`` and lets objects take extra keys. Strict mode (OpenAI's rules,
which OpenRouter passes on) wants every property required, an optional field as
``anyOf [T, null]``, ``additionalProperties: false`` on every object, and no
``default``. Nested models are inlined rather than referenced through ``$defs``,
which not every provider resolves. Pydantic stays the gate: a model may still
ignore ``maxLength``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

# Keywords strict mode rejects or that only cost tokens.
_DROPPED = frozenset({"default", "title", "description", "examples"})


@dataclass(frozen=True)
class ResponseSchema:
    """The schema a provider may enforce on an answer, under a name it can show."""

    name: str
    schema: dict[str, Any]


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """``model``'s JSON schema in strict form (see the module docstring)."""
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})
    strict: dict[str, Any] = _strict(raw, defs)
    return strict


def response_schema(name: str, model: type[BaseModel]) -> ResponseSchema:
    return ResponseSchema(name=name, schema=strict_json_schema(model))


def _strict(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_strict(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _strict(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    out = {key: _strict(value, defs) for key, value in node.items() if key not in _DROPPED}
    if out.get("type") == "object":
        # Property names are data, not keywords: a field called "title" stays.
        properties = {
            name: _strict(value, defs) for name, value in node.get("properties", {}).items()
        }
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out
