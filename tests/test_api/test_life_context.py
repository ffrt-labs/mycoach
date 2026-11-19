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


# --- Travel (#221) ------------------------------------------------------------------


async def test_a_trip_can_be_entered_in_advance(client: AsyncClient) -> None:
    start, back = _today() + timedelta(days=5), _today() + timedelta(days=8)

    resp = await client.post(
        "/api/life-context/travel",
        json={
            "started_on": str(start),
            "ended_on": str(back),
            "equipment": "hotel_gym",
            "tz_shift_hours": -6,
            "no_training": False,
            "note": "Boston",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "travel"
    assert (body["started_on"], body["ended_on"]) == (str(start), str(back))
    assert (body["equipment"], body["tz_shift_hours"], body["no_training"]) == (
        "hotel_gym", -6.0, False,
    )  # fmt: skip
    assert body["severity"] is None


async def test_a_trip_defaults_to_today_with_no_return_date(client: AsyncClient) -> None:
    resp = await client.post("/api/life-context/travel", json={"equipment": "none"})

    assert resp.status_code == 201
    assert (resp.json()["started_on"], resp.json()["ended_on"]) == (str(_today()), None)
    assert resp.json()["no_training"] is False


async def test_a_trip_returning_before_it_starts_is_refused(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/life-context/travel",
        json={
            "started_on": str(_today()),
            "ended_on": str(_today() - timedelta(days=1)),
            "equipment": "none",
        },
    )

    assert resp.status_code == 422


async def test_equipment_is_editable_mid_trip(client: AsyncClient) -> None:
    trip = (await client.post("/api/life-context/travel", json={"equipment": "none"})).json()

    resp = await client.patch(
        f"/api/life-context/episodes/{trip['id']}",
        json={"equipment": "full_gym", "no_training": True},
    )

    assert resp.status_code == 200
    assert (resp.json()["equipment"], resp.json()["no_training"]) == ("full_gym", True)


async def test_an_illness_has_no_equipment(client: AsyncClient) -> None:
    ill = (await client.post("/api/life-context/illness", json={"severity": "mild"})).json()

    resp = await client.patch(f"/api/life-context/episodes/{ill['id']}", json={"equipment": "none"})

    assert resp.status_code == 422


async def test_back_home_ends_the_trip_today(client: AsyncClient) -> None:
    async with test_session() as session:
        trip = LifeContextEpisode(
            user_id=1, kind="travel", equipment="none", started_on=_today() - timedelta(days=3)
        )
        session.add(trip)
        await session.commit()
        trip_id = trip.id

    resp = await client.post(f"/api/life-context/episodes/{trip_id}/close")

    # The travel day home is still a travel day.
    assert resp.json()["ended_on"] == str(_today())


async def test_a_trip_not_yet_started_can_be_cancelled(client: AsyncClient) -> None:
    later = str(_today() + timedelta(days=2))
    trip = (
        await client.post(
            "/api/life-context/travel", json={"started_on": later, "equipment": "none"}
        )
    ).json()

    resp = await client.delete(f"/api/life-context/episodes/{trip['id']}")

    assert resp.status_code == 204
    gone = await client.post(f"/api/life-context/episodes/{trip['id']}/close")
    assert gone.status_code == 404


async def test_a_trip_under_way_cannot_be_cancelled(client: AsyncClient) -> None:
    trip = (await client.post("/api/life-context/travel", json={"equipment": "none"})).json()

    assert (await client.delete(f"/api/life-context/episodes/{trip['id']}")).status_code == 409
