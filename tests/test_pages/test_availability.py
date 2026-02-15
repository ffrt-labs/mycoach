"""Tests for the availability input page route."""

from datetime import date, time, timedelta

import pytest
from httpx import AsyncClient

from mycoach.models.availability import WeeklyAvailability
from mycoach.models.user import User
from tests.conftest import test_session

pytestmark = pytest.mark.anyio


def _next_monday() -> date:
    today = date.today()
    days_ahead = 7 - today.weekday()
    return today + timedelta(days=days_ahead)


async def _seed_user() -> User:
    async with test_session() as session:
        user = User(id=1, email="test@example.com", name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_availability_page_empty(client: AsyncClient) -> None:
    """Availability page renders with no existing slots."""
    resp = await client.get("/availability")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Set Availability" in resp.text
    assert "Monday" in resp.text
    assert "Sunday" in resp.text
    assert "Gym" in resp.text
    assert "Swimming" in resp.text
    assert "Padel" in resp.text


async def test_availability_page_shows_week_dates(client: AsyncClient) -> None:
    """Availability page shows the correct week date range."""
    next_mon = _next_monday()
    next_sun = next_mon + timedelta(days=6)
    resp = await client.get("/availability")
    assert resp.status_code == 200
    assert next_mon.strftime("%b %d") in resp.text
    assert next_sun.strftime("%b %d, %Y") in resp.text


async def test_availability_page_prefills_existing(client: AsyncClient) -> None:
    """Availability page pre-fills existing slots with checked checkboxes."""
    await _seed_user()
    next_mon = _next_monday()

    async with test_session() as session:
        slot = WeeklyAvailability(
            user_id=1,
            week_start=next_mon,
            day_of_week=0,  # Monday
            start_time=time(7, 30),
            duration_minutes=90,
            preferred_sport="gym",
        )
        session.add(slot)
        await session.commit()

    resp = await client.get("/availability")
    assert resp.status_code == 200
    # Monday's checkbox should be checked
    html = resp.text
    assert 'name="day_0_enabled"' in html
    # The checked attribute should be present for day 0
    # Find the checkbox for day 0 and verify it's checked
    import re
    day0_checkbox = re.search(r'name="day_0_enabled"[^>]*', html)
    assert day0_checkbox is not None
    assert "checked" in day0_checkbox.group(0)
    # The time should be pre-filled
    assert "07:30" in html
    # The duration should be selected (90 min)
    assert "90 min" in html


async def test_availability_page_multiple_slots(client: AsyncClient) -> None:
    """Availability page pre-fills multiple existing slots."""
    await _seed_user()
    next_mon = _next_monday()

    async with test_session() as session:
        slot1 = WeeklyAvailability(
            user_id=1,
            week_start=next_mon,
            day_of_week=0,  # Monday
            start_time=time(7, 0),
            duration_minutes=60,
            preferred_sport="gym",
        )
        slot2 = WeeklyAvailability(
            user_id=1,
            week_start=next_mon,
            day_of_week=3,  # Thursday
            start_time=time(18, 0),
            duration_minutes=45,
            preferred_sport="swimming",
        )
        session.add_all([slot1, slot2])
        await session.commit()

    resp = await client.get("/availability")
    assert resp.status_code == 200
    html = resp.text
    # Both slots should have checked checkboxes
    import re
    day0_cb = re.search(r'name="day_0_enabled"[^>]*', html)
    assert day0_cb is not None
    assert "checked" in day0_cb.group(0)
    day3_cb = re.search(r'name="day_3_enabled"[^>]*', html)
    assert day3_cb is not None
    assert "checked" in day3_cb.group(0)
    # Unchecked days should not have "checked"
    day1_cb = re.search(r'name="day_1_enabled"[^>]*', html)
    assert day1_cb is not None
    assert "checked" not in day1_cb.group(0)


async def test_availability_page_has_save_button(client: AsyncClient) -> None:
    """Availability page has a save button."""
    resp = await client.get("/availability")
    assert resp.status_code == 200
    assert "Save Availability" in resp.text
