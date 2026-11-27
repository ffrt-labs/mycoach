"""Tests for the dashboard page route."""

import json
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from mycoach.config import get_settings
from mycoach.localtime import local_today
from mycoach.models.coaching import CoachingInsight
from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.job_run import JobRun
from mycoach.models.life_context import LifeContextEpisode
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.user import User
from tests.conftest import test_session

pytestmark = pytest.mark.anyio


async def _seed_user() -> User:
    async with test_session() as session:
        user = User(id=1, email="test@example.com", name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_dashboard_empty(client: AsyncClient) -> None:
    """Dashboard renders with no data."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "MyCoach" in resp.text
    assert "No health metrics for today" in resp.text
    assert "No daily briefing generated yet" in resp.text


async def test_dashboard_with_health(client: AsyncClient) -> None:
    """Dashboard shows health metrics when available."""
    await _seed_user()
    async with test_session() as session:
        snapshot = DailyHealthSnapshot(
            user_id=1,
            snapshot_date=_local_today(),
            resting_hr=52,
            hrv_status=65.0,
            body_battery_high=85,
            sleep_score=82,
            sleep_duration_minutes=450,
            steps=8500,
            avg_stress=28,
            training_readiness=75,
        )
        session.add(snapshot)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "52" in resp.text  # resting HR
    assert "85" in resp.text  # body battery
    assert "82" in resp.text  # sleep score
    assert "8,500" in resp.text  # steps formatted with comma


async def test_dashboard_steps_and_stress_use_yesterday(client: AsyncClient) -> None:
    """Steps and Avg Stress render yesterday's completed values, labelled as yesterday's."""
    await _seed_user()
    today = _local_today()
    yesterday = today - timedelta(days=1)
    async with test_session() as session:
        session.add(
            DailyHealthSnapshot(
                user_id=1,
                snapshot_date=today,
                resting_hr=52,
                steps=120,  # today's partial total
                avg_stress=5,
                training_readiness=75,
            )
        )
        session.add(
            DailyHealthSnapshot(
                user_id=1,
                snapshot_date=yesterday,
                steps=9412,  # yesterday's completed total
                avg_stress=34,
            )
        )
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "9,412" in resp.text  # yesterday's steps, not today's 120
    assert "120" not in resp.text
    assert "34" in resp.text  # yesterday's avg stress
    assert "STEPS" in resp.text.upper()
    assert "(YEST." in resp.text.upper()  # labelled as yesterday's
    # Point-in-time tiles stay on today's data, untouched
    assert "52" in resp.text  # resting HR from today
    assert "75" in resp.text  # readiness from today


async def test_dashboard_steps_and_stress_fallback_when_no_yesterday(client: AsyncClient) -> None:
    """A missing yesterday snapshot degrades gracefully by falling back to today's."""
    await _seed_user()
    async with test_session() as session:
        session.add(
            DailyHealthSnapshot(
                user_id=1,
                snapshot_date=_local_today(),
                steps=8500,
                avg_stress=28,
            )
        )
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "8,500" in resp.text
    assert "28" in resp.text


async def test_dashboard_7day_baseline_excludes_today(client: AsyncClient) -> None:
    """The 7-day comparison window ends before today, excluding today's partial data."""
    await _seed_user()
    today = _local_today()
    async with test_session() as session:
        # Yesterday's total is distinct from the rest of the week so it can't be
        # mistaken for the main Steps tile (which also renders yesterday's value).
        session.add(
            DailyHealthSnapshot(
                user_id=1,
                snapshot_date=today - timedelta(days=1),
                steps=9000,
            )
        )
        for i in range(2, 8):
            session.add(
                DailyHealthSnapshot(
                    user_id=1,
                    snapshot_date=today - timedelta(days=i),
                    steps=10500,
                )
            )
        # Today's near-zero partial total should not drag the baseline down
        session.add(
            DailyHealthSnapshot(
                user_id=1,
                snapshot_date=today,
                steps=50,
            )
        )
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    # avg over the 7 days before today: (9000 + 6*10500) / 7 = 10,286
    assert "10,286" in resp.text
    # if today's 50 steps were still included, the average would be 9,006 instead
    assert "9,006" not in resp.text


async def test_dashboard_with_briefing(client: AsyncClient) -> None:
    """Dashboard shows readiness verdict from daily briefing."""
    await _seed_user()
    briefing_content = json.dumps(
        {
            "readiness_verdict": "go_hard",
            "recovery_status": "Fully recovered and ready to train.",
            "workout_adjustments": "No adjustments needed.",
            "sleep_recommendation": "Aim for 7.5 hours tonight.",
        }
    )
    async with test_session() as session:
        insight = CoachingInsight(
            user_id=1,
            insight_date=_local_today(),
            insight_type="daily_briefing",
            content=briefing_content,
            prompt_version="v1",
        )
        session.add(insight)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    # Template emits "go hard" and relies on a CSS `capitalize` class for display casing.
    assert "go hard" in resp.text
    assert "Fully recovered" in resp.text
    assert "7.5 hours tonight" in resp.text


async def _store_briefing(content: str) -> None:
    async with test_session() as session:
        session.add(
            CoachingInsight(
                user_id=1,
                insight_date=_local_today(),
                insight_type="daily_briefing",
                content=content,
                prompt_version="bundled:v5",
            )
        )
        await session.commit()


def _page_text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _v5_briefing(**overrides: object) -> str:
    """Coach notes (``DailyBriefingEmail``) with an option named by title, not by an
    open session."""
    from tests.test_email.test_briefing_notes import NOTES

    notes = {
        **NOTES,
        "readiness": "active_recovery",
        "headline": {"tone": "watch", "summary": "Short night; keep it easy."},
        "options": [
            {
                "open_session_id": None,
                "title": "Easy spin",
                "steps": ["30 min Z1", "Cadence 90"],
                "why": "Moves the legs without load.",
                "recommended": True,
            }
        ],
        "guardrail": {"when": "RHR up 5 tomorrow", "then": "Rest day"},
        "sleep_note": "Restless, awake 40 min.",
        "tonight": [{"tone": "watch", "text": "Bed by 22:00"}],
    }
    notes.update(overrides)
    return json.dumps(notes)


async def test_dashboard_renders_a_v5_briefing_from_its_coach_notes(client: AsyncClient) -> None:
    """#249: the same parts as the email: verdict, Lead, options, Guardrail, tonight."""
    await _seed_user()
    await _store_briefing(_v5_briefing())

    resp = await client.get("/")

    assert resp.status_code == 200
    text = _page_text(resp.text)
    assert "Active recovery" in text
    assert "Short night; keep it easy." in text
    assert "Easy spin" in text
    assert "30 min Z1" in text
    assert "Moves the legs without load." in text
    assert "Recommended" in text
    assert "IF RHR up 5 tomorrow → Rest day" in text
    assert "Restless, awake 40 min." in text
    assert "Bed by 22:00" in text
    assert "Coach notes unavailable" not in text


async def test_a_v5_option_names_its_open_session(client: AsyncClient) -> None:
    """An option linked by id takes its title from Open this week, as in the email."""
    await _seed_user()
    today = _local_today()
    await _plan_this_week(
        [PlannedSession(day_of_week=today.weekday(), sport="gym", title="Squat Day")]
    )
    async with test_session() as session:
        open_id = (await session.execute(select(PlannedSession.id))).scalar_one()
    option = {
        "open_session_id": open_id,
        "title": None,
        "steps": ["Last working weights"],
        "why": "Fresh legs today.",
        "recommended": True,
    }
    await _store_briefing(_v5_briefing(options=[option]))

    resp = await client.get("/")

    text = _page_text(resp.text)
    assert "Fresh legs today." in text
    assert "Planned today · gym" in text  # the option's context, from its open session


async def test_a_briefing_without_coach_notes_says_so(client: AsyncClient) -> None:
    """#249: a briefing stored as ``null`` after the notes fallback."""
    await _seed_user()
    await _store_briefing("null")

    resp = await client.get("/")

    assert resp.status_code == 200
    assert "Coach notes unavailable" in resp.text
    assert "Regenerate briefing" in resp.text
    assert "Awaiting briefing" not in resp.text


async def test_dashboard_with_plan(client: AsyncClient) -> None:
    """Dashboard shows today's planned workout."""
    await _seed_user()
    today = _local_today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
        )
        session.add(plan)
        await session.flush()

        planned = PlannedSession(
            plan_id=plan.id,
            day_of_week=today.weekday(),
            sport="gym",
            title="Upper Body Strength",
            duration_minutes=60,
            notes="Focus on compound movements",
            details=json.dumps({"exercises": ["Bench Press", "Rows", "OHP"]}),
            completed=False,
        )
        session.add(planned)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Upper Body Strength" in resp.text
    assert "gym" in resp.text
    assert "60 min" in resp.text
    assert "compound movements" in resp.text


async def test_dashboard_no_session_today(client: AsyncClient) -> None:
    """Dashboard shows rest day message when plan exists but no session today."""
    await _seed_user()
    today = _local_today()
    monday = today - timedelta(days=today.weekday())

    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=monday,
            status="active",
            prompt_version="v1",
        )
        session.add(plan)
        await session.flush()

        # Add session for a different day
        other_day = (today.weekday() + 1) % 7
        planned = PlannedSession(
            plan_id=plan.id,
            day_of_week=other_day,
            sport="swimming",
            title="Endurance Swim",
            duration_minutes=45,
            completed=False,
        )
        session.add(planned)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "rest day" in resp.text.lower()


async def test_dashboard_static_files(client: AsyncClient) -> None:
    """Static CSS and JS files are served."""
    css_resp = await client.get("/static/css/app.css")
    assert css_resp.status_code == 200

    js_resp = await client.get("/static/js/app.js")
    assert js_resp.status_code == 200

    manifest_resp = await client.get("/static/manifest.json")
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["name"] == "MyCoach"

    icon_192_resp = await client.get("/static/icon-192.png")
    assert icon_192_resp.status_code == 200
    assert icon_192_resp.headers["content-type"] == "image/png"

    icon_512_resp = await client.get("/static/icon-512.png")
    assert icon_512_resp.status_code == 200
    assert icon_512_resp.headers["content-type"] == "image/png"


async def test_dashboard_never_synced_state(client: AsyncClient) -> None:
    """With no Garmin data at all, the header shows a sensible never-synced state."""
    await _seed_user()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Never synced" in resp.text
    assert "Synced " not in resp.text  # no "Synced HH:MM" stamp rendered


async def test_dashboard_shows_sync_time_in_local_timezone(client: AsyncClient) -> None:
    """The 'Synced HH:MM' stamp renders in local time, not raw UTC."""
    await _seed_user()
    # Pick a UTC hour whose local rendering differs from the raw UTC hour,
    # so the assertion actually exercises the timezone conversion.
    utc_sync_time = datetime(2026, 6, 15, 23, 30, 0)
    local_tz = ZoneInfo(get_settings().timezone)
    expected_local = utc_sync_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(local_tz)
    assert expected_local.strftime("%H:%M") != utc_sync_time.strftime("%H:%M")

    async with test_session() as session:
        snapshot = DailyHealthSnapshot(
            user_id=1,
            snapshot_date=_local_today(),
            resting_hr=52,
            data_source="garmin",
        )
        snapshot.created_at = utc_sync_time
        session.add(snapshot)
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert f"Synced {expected_local.strftime('%H:%M')}" in resp.text
    assert f"Synced {utc_sync_time.strftime('%H:%M')}" not in resp.text


async def test_dashboard_sync_stamp_advances_on_no_op_resync(client: AsyncClient) -> None:
    """Re-syncing identical data still moves the displayed stamp forward.

    Mirrors what the dashboard's Sync button does against already-held data:
    the row gets UPSERTed with no field changes, but updated_at must still
    advance so the header time isn't stuck.
    """
    await _seed_user()
    today = _local_today()

    async with test_session() as session:
        snapshot = DailyHealthSnapshot(
            user_id=1,
            snapshot_date=today,
            resting_hr=52,
            data_source="garmin",
        )
        snapshot.created_at = datetime(2026, 1, 1, 5, 0, 0)
        session.add(snapshot)
        await session.commit()

    resp = await client.get("/")
    first_sync_text = resp.text

    async with test_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(DailyHealthSnapshot).where(DailyHealthSnapshot.user_id == 1)
        )
        row = result.scalar_one()
        row.updated_at = datetime(2026, 6, 15, 9, 0, 0)  # simulate a re-sync touch
        await session.commit()

    resp = await client.get("/")
    assert resp.text != first_sync_text


async def test_dashboard_reports_the_retry_loops_own_state(client: AsyncClient) -> None:
    """The old text blamed a sync that had in fact succeeded.

    ``dashboard.html`` is server-rendered Jinja and ``job_runs`` is an ordinary
    table, so the true state is one query away — no new API endpoint. The
    window is forced open from midnight so the assertion does not depend on
    what time the suite happens to run.
    """
    await _seed_user()
    async with test_session() as session:
        for i in range(4):
            session.add(
                JobRun(
                    job_name="daily_briefing",
                    started_at=datetime.utcnow() - timedelta(minutes=15 * (i + 1)),
                    duration_ms=800,
                    status="skipped",
                    skip_reason="no usable Garmin health data",
                )
            )
        await session.commit()

    always_open = get_settings().model_copy(
        update={"scheduler_briefing_hour": 0, "scheduler_briefing_minute": 0}
    )
    with patch(
        "mycoach.scheduler.briefing_window.get_settings", return_value=always_open
    ):
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "Waiting on Garmin" in resp.text or "never returned" in resp.text
    assert "No daily briefing generated yet" not in resp.text


async def test_dashboard_disables_the_button_it_knows_would_be_refused(
    client: AsyncClient,
) -> None:
    """Six days of inviting a click that would have briefed on nothing."""
    await _seed_user()
    async with test_session() as session:
        session.add(
            DailyHealthSnapshot(user_id=1, snapshot_date=_local_today(), resting_hr=58)
        )
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Can&#39;t generate:" in resp.text or "Can't generate:" in resp.text
    assert "sleep, HRV, or Body Battery" in resp.text


async def test_dashboard_leaves_the_button_alone_on_a_usable_day(
    client: AsyncClient,
) -> None:
    await _seed_user()
    async with test_session() as session:
        session.add(
            DailyHealthSnapshot(
                user_id=1, snapshot_date=_local_today(), resting_hr=58, sleep_score=82
            )
        )
        await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Can't generate" not in resp.text and "Can&#39;t generate" not in resp.text


async def _plan_this_week(sessions: list[PlannedSession]) -> None:
    today = _local_today()
    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1,
            week_start=today - timedelta(days=today.weekday()),
            status="active",
            prompt_version="v3",
        )
        session.add(plan)
        await session.flush()
        for ps in sessions:
            ps.plan_id = plan.id
            session.add(ps)
        await session.commit()


async def test_dashboard_lists_the_sessions_open_this_week(client: AsyncClient) -> None:
    """#218: a session whose day isn't today is still listed while it is open."""
    await _seed_user()
    today = _local_today()
    other_day = (today.weekday() + 1) % 7
    await _plan_this_week(
        [
            PlannedSession(day_of_week=other_day, sport="gym", title="Squat Day", track="gym"),
            PlannedSession(day_of_week=other_day, sport="swimming", title="Swim", completed=True),
        ]
    )

    resp = await client.get("/")

    assert resp.status_code == 200
    days_left = 7 - today.weekday()
    remaining = f"{days_left} day{'s' if days_left != 1 else ''} remaining"
    assert f"Open this week — 1 left · {remaining}" in resp.text
    assert "Squat Day" in resp.text
    assert "Swim" not in resp.text


# --- Life context (#220) -------------------------------------------------------------


def _local_today() -> date:
    return local_today(get_settings().timezone)


async def _episode(**fields: object) -> int:
    async with test_session() as session:
        episode = LifeContextEpisode(user_id=1, **({"kind": "illness"} | fields))
        session.add(episode)
        await session.commit()
        return episode.id


async def test_dashboard_offers_the_feeling_unwell_check_in(client: AsyncClient) -> None:
    await _seed_user()

    resp = await client.get("/")

    assert "Feeling unwell" in resp.text
    assert 'value="mild"' in resp.text
    assert 'value="sick"' in resp.text
    assert "Still unwell?" not in resp.text


async def test_an_open_illness_shows_the_still_unwell_banner(client: AsyncClient) -> None:
    await _seed_user()
    episode_id = await _episode(
        severity="sick", started_on=_local_today() - timedelta(days=1), note="fever"
    )

    resp = await client.get("/")

    assert "Still unwell?" in resp.text
    assert "Feeling better" in resp.text
    assert f"closeEpisode({episode_id})" in resp.text
    assert f"setSeverity({episode_id}, this.value)" in resp.text
    assert '<option value="sick" selected>' in resp.text
    assert "day 2" in resp.text
    assert "Feeling unwell" not in resp.text


async def test_a_ramp_back_shows_its_day(client: AsyncClient) -> None:
    await _seed_user()
    today = _local_today()
    # Sick five days, ended the day before yesterday: today is ramp-back day 2 of 5.
    await _episode(
        severity="sick",
        started_on=today - timedelta(days=6),
        ended_on=today - timedelta(days=2),
    )

    resp = await client.get("/")

    assert "Returning from illness · day 2/5" in resp.text
    assert "Feeling unwell" in resp.text


# --- Travel (#221) -------------------------------------------------------------------


async def test_dashboard_offers_the_travelling_form(client: AsyncClient) -> None:
    await _seed_user()

    resp = await client.get("/")

    assert "Travelling" in resp.text
    assert "travelling(event)" in resp.text
    for equipment in ("none", "hotel_gym", "full_gym"):
        assert f'value="{equipment}"' in resp.text
    assert 'name="no_training"' in resp.text
    assert 'name="tz_shift_hours"' in resp.text


async def test_a_trip_under_way_shows_its_day_equipment_and_back_home(
    client: AsyncClient,
) -> None:
    await _seed_user()
    today = _local_today()
    trip_id = await _episode(
        kind="travel",
        equipment="hotel_gym",
        tz_shift_hours=-6,
        started_on=today - timedelta(days=1),
        ended_on=today + timedelta(days=2),
    )

    resp = await client.get("/")

    assert "Travelling · day 2" in resp.text
    assert "6 h behind home" in resp.text
    assert f"setEquipment({trip_id}, this.value)" in resp.text
    assert '<option value="hotel_gym" selected>' in resp.text
    assert f"closeEpisode({trip_id})" in resp.text
    assert "Back home" in resp.text


async def test_a_trip_entered_in_advance_shows_as_planned_and_cancellable(
    client: AsyncClient,
) -> None:
    await _seed_user()
    start = _local_today() + timedelta(days=3)
    trip_id = await _episode(kind="travel", equipment="none", no_training=True, started_on=start)

    resp = await client.get("/")

    assert f"Trip planned from {start.strftime('%A %d %B')}" in resp.text
    assert "no training possible" in resp.text
    assert f"cancelTrip({trip_id})" in resp.text


# --- Backdated illness (#236): prefilled check-in links and the confirmation -------


def _opened(html: str, details_id: str) -> bool:
    tag = re.search(rf'<details[^>]*id="{details_id}"[^>]*>', html)
    assert tag is not None, details_id
    return re.search(r"\bopen\b", tag.group(0)) is not None


async def _episode_count() -> int:
    async with test_session() as session:
        return len((await session.execute(select(LifeContextEpisode))).scalars().all())


async def test_the_check_in_is_closed_without_a_prefill_link(client: AsyncClient) -> None:
    await _seed_user()

    resp = await client.get("/")

    assert not _opened(resp.text, "feeling-unwell")
    assert not _opened(resp.text, "illness-earlier")
    assert not _opened(resp.text, "travelling")


async def test_an_illness_link_opens_the_check_in_with_its_dates_and_changes_nothing(
    client: AsyncClient,
) -> None:
    await _seed_user()

    resp = await client.get("/?log=illness&from=2026-11-09&to=2026-11-15")

    assert resp.status_code == 200
    assert _opened(resp.text, "feeling-unwell")
    assert _opened(resp.text, "illness-earlier")
    assert 'name="started_on" value="2026-11-09"' in resp.text
    assert 'name="ended_on" value="2026-11-15"' in resp.text
    assert 'data-prefill="illness"' in resp.text
    assert await _episode_count() == 0


async def test_a_travel_link_opens_the_travelling_form_with_its_dates(
    client: AsyncClient,
) -> None:
    await _seed_user()

    resp = await client.get("/?log=travel&from=2026-11-09&to=2026-11-12")

    assert _opened(resp.text, "travelling")
    assert not _opened(resp.text, "feeling-unwell")
    assert 'name="started_on" value="2026-11-09"' in resp.text
    assert 'name="ended_on" value="2026-11-12"' in resp.text
    assert await _episode_count() == 0


async def test_a_malformed_prefill_link_still_renders_the_dashboard(client: AsyncClient) -> None:
    await _seed_user()

    resp = await client.get("/?log=illness&from=soon&to=")

    assert resp.status_code == 200
    assert _opened(resp.text, "feeling-unwell")
    assert not _opened(resp.text, "illness-earlier")


async def test_after_saving_an_illness_the_dashboard_confirms_what_it_excused(
    client: AsyncClient,
) -> None:
    await _seed_user()
    async with test_session() as session:
        plan = WeeklyPlan(
            user_id=1, week_start=date(2000, 11, 6), status="active", prompt_version="v3"
        )
        session.add(plan)
        await session.flush()
        session.add_all(
            [
                PlannedSession(plan_id=plan.id, day_of_week=0, sport="gym", title="Upper A"),
                PlannedSession(plan_id=plan.id, day_of_week=1, sport="running", title="Easy"),
                PlannedSession(plan_id=plan.id, day_of_week=4, sport="running", title="Long"),
            ]
        )
        await session.commit()
    told = await client.post(
        "/api/life-context/illness",
        json={"severity": "sick", "started_on": "2000-11-06", "ended_on": "2000-11-08"},
    )

    resp = await client.get(f"/?logged={told.json()['id']}")

    # Mon and Tue excuse their own sessions; Wednesday finds no earlier one left.
    assert "Logged ill 6–8 Nov · 2 sessions excused" in resp.text


async def test_no_confirmation_for_an_unknown_episode(client: AsyncClient) -> None:
    await _seed_user()

    resp = await client.get("/?logged=999")

    assert resp.status_code == 200
    assert "Logged ill" not in resp.text
