"""Life context API (#220): the dashboard's illness check-in, severity edit and close."""

from datetime import timedelta

import pytest
from httpx import AsyncClient

from mycoach.config import get_settings
from mycoach.localtime import local_today
from mycoach.models.life_context import LifeContextEpisode
from mycoach.models.user import User
from tests.conftest import test_session

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def _user() -> None:
    async with test_session() as session:
        session.add(User(id=1, email="a@example.com", name="A"))
        await session.commit()


def _today():  # type: ignore[no-untyped-def]
    return local_today(get_settings().timezone)


async def test_feeling_unwell_opens_an_illness_episode_today(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/life-context/illness", json={"severity": "sick", "note": "fever, aches"}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "illness"
    assert body["severity"] == "sick"
    assert body["note"] == "fever, aches"
    assert body["started_on"] == str(_today())
    assert body["ended_on"] is None


async def test_an_unknown_severity_is_refused(client: AsyncClient) -> None:
    resp = await client.post("/api/life-context/illness", json={"severity": "dying"})

    assert resp.status_code == 422


async def test_a_second_open_illness_is_refused(client: AsyncClient) -> None:
    await client.post("/api/life-context/illness", json={"severity": "mild"})

    resp = await client.post("/api/life-context/illness", json={"severity": "sick"})

    assert resp.status_code == 409


async def test_severity_and_note_are_editable_while_open(client: AsyncClient) -> None:
    opened = (await client.post("/api/life-context/illness", json={"severity": "mild"})).json()

    resp = await client.patch(
        f"/api/life-context/episodes/{opened['id']}", json={"severity": "sick", "note": "chest"}
    )

    assert resp.status_code == 200
    assert resp.json()["severity"] == "sick"
    assert resp.json()["note"] == "chest"


async def test_feeling_better_ends_the_episode_yesterday(client: AsyncClient) -> None:
    async with test_session() as session:
        episode = LifeContextEpisode(
            user_id=1, kind="illness", severity="sick", started_on=_today() - timedelta(days=3)
        )
        session.add(episode)
        await session.commit()
        episode_id = episode.id

    resp = await client.post(f"/api/life-context/episodes/{episode_id}/close")

    assert resp.status_code == 200
    # The day you feel better is ramp-back day 1, not another ill day.
    assert resp.json()["ended_on"] == str(_today() - timedelta(days=1))


async def test_closing_an_episode_opened_today_ends_it_today(client: AsyncClient) -> None:
    opened = (await client.post("/api/life-context/illness", json={"severity": "mild"})).json()

    resp = await client.post(f"/api/life-context/episodes/{opened['id']}/close")

    assert resp.json()["ended_on"] == str(_today())


async def test_an_unknown_episode_is_404(client: AsyncClient) -> None:
    assert (await client.post("/api/life-context/episodes/999/close")).status_code == 404
    patched = await client.patch("/api/life-context/episodes/999", json={"severity": "sick"})
    assert patched.status_code == 404
