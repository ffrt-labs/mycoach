"""The health events are plain data with deterministic ids (#112, ticket #186)."""

from datetime import datetime

from mycoach.health.events import (
    CanonicalActivityRecorded,
    CanonicalActivityRevised,
    CanonicalActivitySettled,
    event_id,
)

T = datetime(2026, 11, 1, 9, 0)


def test_recorded_and_settled_ids_depend_only_on_type_and_external_id():
    a = CanonicalActivityRecorded.build(1, "ext-1", "gym", "logger", T)
    b = CanonicalActivityRecorded.build(1, "ext-1", "gym", "logger", datetime(2030, 1, 1))
    assert a.event_id == b.event_id
    s = CanonicalActivitySettled.build(1, "ext-1", "gym", T, "reconciled")
    assert s.event_id != a.event_id
    assert s.event_id == event_id("CanonicalActivitySettled", "ext-1")


def test_revised_id_includes_the_revision_timestamp():
    r1 = CanonicalActivityRevised.build(1, "ext-1", "gym", T, ["avg_hr"])
    r2 = CanonicalActivityRevised.build(1, "ext-1", "gym", datetime(2026, 11, 2), ["avg_hr"])
    assert r1.event_id != r2.event_id
    assert r1.event_id == CanonicalActivityRevised.build(1, "ext-1", "gym", T, ["x"]).event_id


def test_events_are_plain_data_and_serialise():
    r = CanonicalActivityRevised.build(1, "ext-1", "gym", T, ["avg_hr", "calories"])
    d = r.to_dict()
    assert d["type"] == "CanonicalActivityRevised"
    assert d["activity_external_id"] == "ext-1"
    assert d["changed_fields"] == ["avg_hr", "calories"]
    assert isinstance(d["revised_at"], str)
