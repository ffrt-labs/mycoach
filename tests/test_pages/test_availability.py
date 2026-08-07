"""Tests for the availability input page route."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient

import re

from mycoach.models.availability import DefaultAvailability, WeeklyAvailability
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
            sport="gym",
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


async def test_availability_page_multiple_slots(client: AsyncClient) -> None:
    """Availability page pre-fills multiple existing slots."""
    await _seed_user()
    next_mon = _next_monday()

    async with test_session() as session:
        slot1 = WeeklyAvailability(
            user_id=1,
            week_start=next_mon,
            day_of_week=0,  # Monday
            sport="gym",
        )
        slot2 = WeeklyAvailability(
            user_id=1,
            week_start=next_mon,
            day_of_week=3,  # Thursday
            sport="swimming",
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


async def test_availability_page_current_week_unchanged_when_empty(client: AsyncClient) -> None:
    """The current-week tab keeps today's behaviour: no pre-fill from the default."""
    await _seed_user()
    async with test_session() as session:
        session.add(DefaultAvailability(user_id=1, day_of_week=0, sport="gym"))
        await session.commit()

    resp = await client.get("/availability?week=current")
    assert resp.status_code == 200
    day0_cb = re.search(r'name="day_0_enabled"[^>]*', resp.text)
    assert day0_cb is not None
    assert "checked" not in day0_cb.group(0)
    assert "From default" not in resp.text


async def test_availability_page_next_week_prefills_from_default(client: AsyncClient) -> None:
    """An empty next week pre-fills from the default and marks it as such."""
    await _seed_user()
    next_mon = _next_monday()
    async with test_session() as session:
        session.add(DefaultAvailability(user_id=1, day_of_week=0, sport="gym"))
        await session.commit()

    resp = await client.get("/availability?week=next")
    assert resp.status_code == 200
    html = resp.text
    day0_cb = re.search(r'name="day_0_enabled"[^>]*', html)
    assert day0_cb is not None
    assert "checked" in day0_cb.group(0)
    assert "From default" in html

    # Purely display — nothing was written for the week.
    async with test_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(WeeklyAvailability).where(
                WeeklyAvailability.user_id == 1,
                WeeklyAvailability.week_start == next_mon,
            )
        )
        assert result.scalars().first() is None


async def test_availability_page_next_week_prefills_rest_day_as_unchecked(
    client: AsyncClient,
) -> None:
    """A default rest day (explicit sport=None row) pre-fills as unchecked.

    Rendering it checked would leave the sport dropdown with nothing selected,
    which the browser defaults to "gym" — silently turning a declared rest day
    into a training day the moment the pre-filled week gets saved.
    """
    await _seed_user()
    async with test_session() as session:
        session.add(DefaultAvailability(user_id=1, day_of_week=0, sport="gym"))
        session.add(DefaultAvailability(user_id=1, day_of_week=1, sport=None))
        await session.commit()

    resp = await client.get("/availability?week=next")
    assert resp.status_code == 200
    html = resp.text
    day1_cb = re.search(r'name="day_1_enabled"[^>]*', html)
    assert day1_cb is not None
    assert "checked" not in day1_cb.group(0)
    # Still marked as coming from the default, even though it's unchecked.
    assert "From default" in html


async def test_availability_page_next_week_declared_not_marked_as_default(
    client: AsyncClient,
) -> None:
    """A declared next week renders its own rows, not marked as coming from the default."""
    await _seed_user()
    next_mon = _next_monday()
    async with test_session() as session:
        session.add(DefaultAvailability(user_id=1, day_of_week=0, sport="gym"))
        session.add(
            WeeklyAvailability(user_id=1, week_start=next_mon, day_of_week=0, sport="padel")
        )
        await session.commit()

    resp = await client.get("/availability?week=next")
    assert resp.status_code == 200
    html = resp.text
    assert "From default" not in html
    day0_select = re.search(r'name="day_0_sport".*?</select>', html, re.DOTALL)
    assert day0_select is not None
    padel_option = re.search(r'<option value="padel"[^>]*>', day0_select.group(0))
    assert padel_option is not None
    assert "selected" in padel_option.group(0)


async def test_availability_page_default_tab_renders_prefilled(client: AsyncClient) -> None:
    """The default tab renders the standing schedule, pre-filled from DefaultAvailability."""
    await _seed_user()
    async with test_session() as session:
        session.add(DefaultAvailability(user_id=1, day_of_week=0, sport="gym"))
        session.add(DefaultAvailability(user_id=1, day_of_week=2, sport=None))
        await session.commit()

    resp = await client.get("/availability?week=default")
    assert resp.status_code == 200
    html = resp.text
    assert "Standing schedule" in html
    day0_cb = re.search(r'name="day_0_enabled"[^>]*', html)
    assert day0_cb is not None
    assert "checked" in day0_cb.group(0)
    day2_cb = re.search(r'name="day_2_enabled"[^>]*', html)
    assert day2_cb is not None
    assert "checked" in day2_cb.group(0)
    day6_cb = re.search(r'name="day_6_enabled"[^>]*', html)
    assert day6_cb is not None
    assert "checked" not in day6_cb.group(0)


async def test_availability_page_default_tab_empty_is_valid(client: AsyncClient) -> None:
    """An empty default is a valid, renderable state."""
    resp = await client.get("/availability?week=default")
    assert resp.status_code == 200
    assert "Standing schedule" in resp.text
