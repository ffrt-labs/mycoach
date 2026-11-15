"""The Body API: log weight/waist, list, delete, height (ticket #198)."""

from httpx import AsyncClient

from mycoach import health
from mycoach.models.user import User
from tests.conftest import test_session


async def _create_user() -> None:
    async with test_session() as session:
        session.add(User(id=1, name="Test User", email="test@example.com"))
        await session.commit()


async def test_log_a_weight_and_waist(client: AsyncClient) -> None:
    await _create_user()

    resp = await client.post(
        "/api/body/measurements",
        json={
            "measured_at": "2026-11-12T07:05",
            "weight_kg": 82.4,
            "waist_cm": 91.5,
            "note": "after a salty dinner",
        },
    )

    assert resp.status_code == 201
    listed = (await client.get("/api/body/measurements")).json()
    assert [(m["weight_kg"], m["waist_cm"], m["note"]) for m in listed] == [
        (82.4, 91.5, "after a salty dinner")
    ]
    assert resp.json()["id"] == listed[0]["id"]


async def test_the_entered_time_is_local_and_stored_as_utc(client: AsyncClient) -> None:
    # Europe/London is on BST (UTC+1) in July.
    await _create_user()

    await client.post(
        "/api/body/measurements", json={"measured_at": "2026-07-10T08:00", "weight_kg": 80.0}
    )

    rows = await health.list_body_measurements(1)
    assert rows[0]["measured_at"] == "2026-07-10 07:00:00"


async def test_time_defaults_to_now(client: AsyncClient) -> None:
    await _create_user()

    resp = await client.post("/api/body/measurements", json={"waist_cm": 90.0})

    assert resp.status_code == 201
    assert resp.json()["measured_at"]


async def test_at_least_one_of_weight_or_waist_is_required(client: AsyncClient) -> None:
    await _create_user()

    resp = await client.post(
        "/api/body/measurements", json={"measured_at": "2026-11-12T07:05", "note": "hi"}
    )

    assert resp.status_code == 422
    assert (await client.get("/api/body/measurements")).json() == []


async def test_implausible_values_are_refused(client: AsyncClient) -> None:
    await _create_user()

    resp = await client.post("/api/body/measurements", json={"weight_kg": -3})

    assert resp.status_code == 422


async def test_the_list_holds_the_last_ten(client: AsyncClient) -> None:
    await _create_user()
    for day in range(1, 13):
        await client.post(
            "/api/body/measurements",
            json={"measured_at": f"2026-11-{day:02d}T07:00", "weight_kg": 80 + day / 10},
        )

    listed = (await client.get("/api/body/measurements")).json()

    assert len(listed) == 10
    assert listed[0]["weight_kg"] == 81.2


async def test_delete_a_measurement(client: AsyncClient) -> None:
    await _create_user()
    created = (
        await client.post("/api/body/measurements", json={"weight_kg": 82.0})
    ).json()

    resp = await client.delete(f"/api/body/measurements/{created['id']}")

    assert resp.status_code == 204
    assert (await client.get("/api/body/measurements")).json() == []
    assert (await client.delete(f"/api/body/measurements/{created['id']}")).status_code == 404


async def test_set_and_read_height(client: AsyncClient) -> None:
    await _create_user()

    resp = await client.put("/api/body/height", json={"height_cm": 181.5})

    assert resp.status_code == 200
    assert (await client.get("/api/body/height")).json() == {"height_cm": 181.5}


async def test_height_must_be_plausible(client: AsyncClient) -> None:
    await _create_user()

    assert (await client.put("/api/body/height", json={"height_cm": 18})).status_code == 422
