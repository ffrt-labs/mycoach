"""The Body API: log weight/waist, list, delete, height (ticket #198)."""

from unittest.mock import MagicMock

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


# ── Garmin weight push (#199) ─────────────────────────────────────────


async def test_a_saved_weight_is_pushed_to_garmin_after_the_save(
    client: AsyncClient, fake_garmin: MagicMock
) -> None:
    await _create_user()
    fake_garmin.get_daily_weigh_ins.return_value = {
        "dateWeightList": [{"samplePk": 77, "weight": 82400.0, "timestampGMT": 0}]
    }

    resp = await client.post(
        "/api/body/measurements", json={"measured_at": "2026-07-10T08:00", "weight_kg": 82.4}
    )

    assert resp.status_code == 201
    fake_garmin.add_weigh_in_with_timestamps.assert_called_once()
    listed = (await client.get("/api/body/measurements")).json()
    assert listed[0]["garmin_push_status"] == "pushed"

    await client.delete(f"/api/body/measurements/{listed[0]['id']}")
    fake_garmin.delete_weigh_in.assert_called_once_with("77", "2026-07-10")


async def test_a_garmin_outage_never_blocks_or_rolls_back_the_save(
    client: AsyncClient, fake_garmin: MagicMock
) -> None:
    await _create_user()
    fake_garmin.add_weigh_in_with_timestamps.side_effect = ConnectionError("Garmin is down")

    resp = await client.post("/api/body/measurements", json={"weight_kg": 82.4})

    assert resp.status_code == 201
    listed = (await client.get("/api/body/measurements")).json()
    assert [(m["weight_kg"], m["garmin_push_status"]) for m in listed] == [(82.4, "failed")]


async def test_a_garmin_login_failure_never_blocks_the_save(
    client: AsyncClient, fake_garmin: MagicMock
) -> None:
    await _create_user()
    fake_garmin.login.side_effect = RuntimeError("Garmin is down")

    resp = await client.post("/api/body/measurements", json={"weight_kg": 82.4})

    assert resp.status_code == 201
    assert len((await client.get("/api/body/measurements")).json()) == 1


async def test_a_waist_only_entry_is_not_sent_to_garmin(
    client: AsyncClient, fake_garmin: MagicMock
) -> None:
    await _create_user()

    await client.post("/api/body/measurements", json={"waist_cm": 91.0})

    fake_garmin.add_weigh_in_with_timestamps.assert_not_called()
    fake_garmin.login.assert_not_called()


async def test_deleting_a_weight_pushed_without_a_known_id_still_deletes_it_in_garmin(
    client: AsyncClient, fake_garmin: MagicMock
) -> None:
    """Garmin took the weigh-in but its id wasn't found at push time: look it up now."""
    await _create_user()
    await client.post(
        "/api/body/measurements",
        json={"measured_at": "2026-07-10T08:00:00", "weight_kg": 82.4},
    )
    [listed] = (await client.get("/api/body/measurements")).json()
    assert listed["garmin_push_status"] == "pushed"
    fake_garmin.get_daily_weigh_ins.return_value = {
        "dateWeightList": [
            {"samplePk": 78, "weight": 82400.0, "timestampGMT": 1783666800000},
        ]
    }

    await client.delete(f"/api/body/measurements/{listed['id']}")

    fake_garmin.delete_weigh_in.assert_called_once_with("78", "2026-07-10")
