"""Observed-health events: plain data with deterministic ids (map #85, ticket #112, ADR 0002).

Health returns these as values from its commands; the call site dispatches them to coaching
after health's transaction has committed. Health never imports coaching.

Ids are deterministic hashes, not random, so a handler dedupes by id:
``Recorded``/``Settled`` fire at most once per activity (type + external id); ``Revised``
can fire many times, so its id also carries the revision timestamp.
"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, ClassVar


def event_id(event_type: str, activity_external_id: str, revised_at: datetime | None = None) -> str:
    parts = [event_type, activity_external_id]
    if revised_at is not None:
        parts.append(revised_at.isoformat())
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


class _Event:
    type_name: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type_name}
        for key, value in asdict(self).items():  # type: ignore[call-overload]
            out[key] = value.isoformat() if isinstance(value, datetime) else value
        return out


@dataclass(frozen=True)
class CanonicalActivityRecorded(_Event):
    type_name: ClassVar[str] = "CanonicalActivityRecorded"
    event_id: str
    athlete_id: int
    activity_external_id: str
    sport: str
    source: str
    occurred_at: datetime

    @classmethod
    def build(
        cls,
        athlete_id: int,
        activity_external_id: str,
        sport: str,
        source: str,
        occurred_at: datetime,
    ) -> "CanonicalActivityRecorded":
        return cls(
            event_id(cls.type_name, activity_external_id),
            athlete_id,
            activity_external_id,
            sport,
            source,
            occurred_at,
        )


@dataclass(frozen=True)
class CanonicalActivitySettled(_Event):
    type_name: ClassVar[str] = "CanonicalActivitySettled"
    event_id: str
    athlete_id: int
    activity_external_id: str
    sport: str
    settled_at: datetime
    settlement_reason: str  # "reconciled" | "deadline"

    @classmethod
    def build(
        cls,
        athlete_id: int,
        activity_external_id: str,
        sport: str,
        settled_at: datetime,
        reason: str,
    ) -> "CanonicalActivitySettled":
        return cls(
            event_id(cls.type_name, activity_external_id),
            athlete_id,
            activity_external_id,
            sport,
            settled_at,
            reason,
        )


@dataclass(frozen=True)
class CanonicalActivityRevised(_Event):
    type_name: ClassVar[str] = "CanonicalActivityRevised"
    event_id: str
    athlete_id: int
    activity_external_id: str
    sport: str
    revised_at: datetime
    changed_fields: list[str]

    @classmethod
    def build(
        cls,
        athlete_id: int,
        activity_external_id: str,
        sport: str,
        revised_at: datetime,
        changed_fields: list[str],
    ) -> "CanonicalActivityRevised":
        return cls(
            event_id(cls.type_name, activity_external_id, revised_at),
            athlete_id,
            activity_external_id,
            sport,
            revised_at,
            list(changed_fields),
        )


HealthEvent = CanonicalActivityRecorded | CanonicalActivitySettled | CanonicalActivityRevised
