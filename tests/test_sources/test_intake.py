"""The shared source-record intake (map #85, ticket #185, design #109).

Seam under test: ``intake_source_record``, the one function every ingestion path
funnels through. It records an immutable source record, gives it a canonical
activity at once, links the two, and is idempotent on (athlete, source, external_id).
"""

import ast
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from mycoach.models.activity import Activity
from mycoach.models.source_record import CanonicalActivitySource, SourceRecord
from mycoach.sources.intake import intake_source_record

START = datetime(2024, 6, 10, 9, 0)


def _canonical(**overrides: object) -> Activity:
    fields: dict[str, object] = {
        "user_id": 1,
        "sport": "gym",
        "title": "Push Day",
        "start_time": START,
        "end_time": datetime(2024, 6, 10, 10, 0),
        "data_source": "logger",
        "external_id": "uuid-1",
    }
    fields.update(overrides)
    return Activity(**fields)  # type: ignore[arg-type]


async def _count(session, model) -> int:  # noqa: ANN001
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_intake_records_a_source_record_and_its_canonical_activity() -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        result = await intake_source_record(
            session,
            athlete_id=1,
            source="logger",
            external_id="uuid-1",
            raw_payload={"title": "Push Day", "sets": [1, 2]},
            canonical=_canonical(),
        )
        await session.commit()

    assert result.created is True
    async with test_session() as session:
        record = (await session.execute(select(SourceRecord))).scalar_one()
        assert (record.athlete_id, record.source, record.external_id) == (1, "logger", "uuid-1")
        assert record.raw_payload == {"title": "Push Day", "sets": [1, 2]}
        assert (record.sport, record.title, record.start_time) == ("gym", "Push Day", START)
        activity = (await session.execute(select(Activity))).scalar_one()
        link = (await session.execute(select(CanonicalActivitySource))).scalar_one()
        assert (link.canonical_activity_id, link.source_record_id) == (activity.id, record.id)


@pytest.mark.asyncio
async def test_replaying_an_intake_is_a_no_op() -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        first = await intake_source_record(
            session,
            athlete_id=1,
            source="logger",
            external_id="uuid-1",
            raw_payload={"v": 1},
            canonical=_canonical(),
        )
        await session.commit()

    async with test_session() as session:
        again = await intake_source_record(
            session,
            athlete_id=1,
            source="logger",
            external_id="uuid-1",
            raw_payload={"v": 2},  # a replay never rewrites the immutable record
            canonical=_canonical(title="Renamed"),
        )
        await session.commit()

    assert again.created is False
    assert again.source_record_id == first.source_record_id
    assert again.canonical_activity_id == first.canonical_activity_id
    async with test_session() as session:
        assert await _count(session, SourceRecord) == 1
        assert await _count(session, Activity) == 1
        assert await _count(session, CanonicalActivitySource) == 1
        record = (await session.execute(select(SourceRecord))).scalar_one()
        assert record.raw_payload == {"v": 1}
        assert (await session.execute(select(Activity.title))).scalar_one() == "Push Day"


@pytest.mark.asyncio
async def test_the_same_external_id_from_another_source_is_a_different_record() -> None:
    from tests.conftest import test_session

    async with test_session() as session:
        for source in ("logger", "hevy"):
            await intake_source_record(
                session,
                athlete_id=1,
                source=source,
                external_id="shared",
                raw_payload={},
                canonical=_canonical(data_source=source, external_id="shared"),
            )
        await session.commit()
        assert await _count(session, SourceRecord) == 2


def test_intake_does_not_import_coaching() -> None:
    """The seam (#184): the health side never imports coaching."""
    root = Path(__file__).resolve().parents[2] / "src" / "mycoach" / "sources"
    for name in ("intake.py", "reconcile.py"):
        path = root / name
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            assert not [m for m in modules if m.startswith("mycoach.coaching")], path
            assert not [m for m in modules if m == "mycoach.plan_linking"], path


async def test_canonical_external_ids_stay_unique_when_two_sources_share_an_id() -> None:
    """Coaching names an activity by its external id, so two activities never share one."""
    from sqlalchemy import select

    from mycoach.models.activity import Activity
    from tests.conftest import test_session

    async with test_session() as session:
        for source in ("logger", "hevy"):
            await intake_source_record(
                session,
                athlete_id=1,
                source=source,
                external_id="shared",
                raw_payload={},
                canonical=_canonical(data_source=source, external_id="shared"),
            )
        await session.commit()
        ids = (await session.execute(select(Activity.external_id).order_by(Activity.id))).scalars()
        assert list(ids) == ["shared", "hevy:shared"]
