"""Life context episodes (#220): illness check-in, ramp-back, and how days are excused."""

from datetime import date

import pytest

from mycoach.coaching.life_context import (
    DayEffect,
    Episode,
    IllnessSeverity,
    LifeContext,
    RecoverySignals,
    effect_on,
    excusing_days,
    life_context_for,
    ramp_back_length,
    ramp_back_on,
    recovery_signals,
)
from mycoach.coaching.prompt_builder import _format_life_context
from mycoach.models.life_context import LifeContextEpisode
from mycoach.models.user import User
from tests.conftest import test_session


@pytest.mark.parametrize(
    ("severity", "ill_days", "expected"),
    [
        (IllnessSeverity.SICK, 2, 3),
        (IllnessSeverity.SICK, 5, 5),
        (IllnessSeverity.SICK, 10, 7),
        (IllnessSeverity.MILD, 1, 3),
        (IllnessSeverity.MILD, 10, 3),
    ],
)
def test_ramp_back_length(severity: IllnessSeverity, ill_days: int, expected: int) -> None:
    assert ramp_back_length(severity, ill_days) == expected


def _illness(start: date, end: date | None, severity: str = "sick") -> Episode:
    return Episode(id=1, kind="illness", started_on=start, ended_on=end, severity=severity)


# Sick Monday 2026-11-02 to Friday 2026-11-06: five days, so a five-day ramp-back.
SICK_WEEK = _illness(date(2026, 11, 2), date(2026, 11, 6))


@pytest.mark.parametrize(
    ("today", "day", "load_pct"),
    [
        (date(2026, 11, 7), 1, 50),
        (date(2026, 11, 8), 2, 60),
        (date(2026, 11, 9), 3, 70),
        (date(2026, 11, 11), 5, 90),
    ],
)
def test_ramp_back_rises_linearly_from_half_to_ninety_percent(
    today: date, day: int, load_pct: int
) -> None:
    ramp = ramp_back_on([SICK_WEEK], today)

    assert ramp is not None
    assert (ramp.day, ramp.length, ramp.load_pct) == (day, 5, load_pct)
    assert ramp.ends_on == date(2026, 11, 11)


def test_no_ramp_back_while_ill_or_after_it_ends() -> None:
    assert ramp_back_on([SICK_WEEK], date(2026, 11, 6)) is None
    assert ramp_back_on([SICK_WEEK], date(2026, 11, 12)) is None
    assert ramp_back_on([_illness(date(2026, 11, 2), None)], date(2026, 11, 9)) is None


def test_a_mild_cold_ramps_back_over_three_days() -> None:
    ramp = ramp_back_on([_illness(date(2026, 11, 2), date(2026, 11, 3), "mild")], date(2026, 11, 6))

    assert ramp is not None
    assert (ramp.day, ramp.length, ramp.load_pct) == (3, 3, 90)


def test_poor_recovery_on_the_last_day_suggests_extending_never_shortening() -> None:
    poor = RecoverySignals(hrv_below_baseline=True, resting_hr_elevated=False)
    good = RecoverySignals(hrv_below_baseline=False, resting_hr_elevated=False)

    last = ramp_back_on([SICK_WEEK], date(2026, 11, 11), recovery=poor)
    middle = ramp_back_on([SICK_WEEK], date(2026, 11, 8), recovery=poor)
    fine = ramp_back_on([SICK_WEEK], date(2026, 11, 8), recovery=good)

    assert last is not None and last.extend_suggested
    assert middle is not None and not middle.extend_suggested and middle.length == 5
    assert fine is not None and fine.length == 5 and fine.day == 2


class TestLifeContextFor:
    async def test_it_reads_the_window_and_todays_ramp_back(self) -> None:
        async with test_session() as session:
            session.add(User(id=1, email="a@example.com", name="A"))
            session.add_all(
                [
                    LifeContextEpisode(
                        user_id=1, kind="illness", severity="sick",
                        started_on=date(2026, 11, 2), ended_on=date(2026, 11, 6),
                    ),
                    LifeContextEpisode(
                        user_id=1, kind="illness", severity="mild",
                        started_on=date(2026, 10, 1), ended_on=date(2026, 10, 3),
                    ),
                ]
            )  # fmt: skip
            await session.commit()

            ctx = await life_context_for(
                session, 1, date(2026, 11, 5), date(2026, 11, 8), today=date(2026, 11, 8)
            )

        assert [e.started_on for e in ctx.episodes] == [date(2026, 11, 2)]
        assert ctx.ramp_back is not None
        assert (ctx.ramp_back.day, ctx.ramp_back.length) == (2, 5)
        assert ctx.today == date(2026, 11, 8)


class TestRecoverySignals:
    def test_hrv_seven_day_average_below_the_band_is_below_baseline(self) -> None:
        today = {"hrv_7day_avg": 44.0, "hrv_baseline_low": 47, "hrv_baseline_high": 58}

        assert recovery_signals(today, []) == RecoverySignals(
            hrv_below_baseline=True, resting_hr_elevated=False
        )

    def test_resting_hr_five_above_its_recent_level_is_elevated(self) -> None:
        recent = [{"resting_hr": 50}, {"resting_hr": 52}, {"resting_hr": None}]

        assert recovery_signals({"resting_hr": 56}, recent).resting_hr_elevated
        assert not recovery_signals({"resting_hr": 55}, recent).resting_hr_elevated

    def test_no_data_is_no_signal(self) -> None:
        assert recovery_signals({}, []) == RecoverySignals(
            hrv_below_baseline=False, resting_hr_elevated=False
        )


WEEK = date(2026, 11, 2)  # Monday
SUNDAY_AFTER = date(2026, 11, 9)


def _days(*days: int) -> list[date]:
    return [date(2026, 11, d) for d in days]


class TestExcusingDays:
    def test_each_sick_day_of_the_week_excuses_as_ill(self) -> None:
        sick = _illness(date(2026, 11, 2), date(2026, 11, 4))

        assert excusing_days([sick], WEEK, today=SUNDAY_AFTER) == {
            d: "excused_ill" for d in _days(2, 3, 4)
        }

    def test_a_mild_day_excuses_nothing(self) -> None:
        mild = _illness(date(2026, 11, 2), date(2026, 11, 4), "mild")

        assert excusing_days([mild], WEEK, today=SUNDAY_AFTER) == {}

    def test_overlapping_episodes_excuse_a_day_once(self) -> None:
        a = _illness(date(2026, 11, 2), date(2026, 11, 4))
        b = _illness(date(2026, 11, 3), date(2026, 11, 5))

        assert list(excusing_days([a, b], WEEK, today=SUNDAY_AFTER)) == _days(2, 3, 4, 5)

    def test_the_most_restrictive_effect_wins_on_an_overlapping_day(self) -> None:
        mild = _illness(date(2026, 11, 2), date(2026, 11, 6), "mild")
        sick = _illness(date(2026, 11, 4), date(2026, 11, 4))

        assert effect_on([mild, sick], date(2026, 11, 4), today=SUNDAY_AFTER) == DayEffect.SICK
        assert effect_on([mild, sick], date(2026, 11, 3), today=SUNDAY_AFTER) == DayEffect.MILD
        assert effect_on([mild, sick], date(2026, 11, 7), today=SUNDAY_AFTER) is None
        assert list(excusing_days([mild, sick], WEEK, today=SUNDAY_AFTER)) == _days(4)

    def test_an_open_episode_covers_up_to_today_and_only_this_week(self) -> None:
        still_ill = _illness(date(2026, 10, 30), None)

        assert list(excusing_days([still_ill], WEEK, today=date(2026, 11, 3))) == _days(2, 3)


class TestLifeContextPromptText:
    """The ``life_context`` variable every coaching run gets (#220)."""

    def test_nothing_going_on(self) -> None:
        ctx = LifeContext(today=date(2026, 11, 5), episodes=[], ramp_back=None)

        assert _format_life_context(ctx) == "Nothing to report: no illness, travel or ramp-back."
        assert _format_life_context(None) == "Nothing to report: no illness, travel or ramp-back."

    def test_an_open_illness(self) -> None:
        ill = Episode(
            id=1, kind="illness", started_on=date(2026, 11, 3), ended_on=None,
            severity="sick", note="fever",
        )  # fmt: skip
        ctx = LifeContext(today=date(2026, 11, 5), episodes=[ill], ramp_back=None)

        assert _format_life_context(ctx) == (
            "- Illness, sick (below the neck): since Tuesday 2026-11-03, still unwell "
            "today (day 3). Note: fever"
        )

    def test_an_ended_illness_and_its_ramp_back(self) -> None:
        mild = _illness(date(2026, 11, 2), date(2026, 11, 4), "mild")
        ramp = ramp_back_on([mild], date(2026, 11, 6))
        ctx = LifeContext(today=date(2026, 11, 6), episodes=[mild], ramp_back=ramp)

        assert _format_life_context(ctx) == (
            "- Illness, mild (above the neck): Monday 2026-11-02 to Wednesday 2026-11-04 "
            "(3 days)\n"
            "Returning from illness: ramp-back day 2/3, about 70% of normal training load "
            "today (50% on day 1 rising to 90% on day 3; last day Saturday 2026-11-07)."
        )

    def test_poor_recovery_on_the_last_day_may_extend(self) -> None:
        poor = RecoverySignals(hrv_below_baseline=False, resting_hr_elevated=True)
        ramp = ramp_back_on([SICK_WEEK], date(2026, 11, 11), recovery=poor)
        ctx = LifeContext(today=date(2026, 11, 11), episodes=[], ramp_back=ramp)

        assert _format_life_context(ctx).endswith(
            "Resting HR is still elevated on the last ramp-back day: you may suggest "
            "extending the ramp-back by a few days. Never shorten it."
        )


def _trip(start: date, end: date | None, **kw: object) -> Episode:
    return Episode(id=7, kind="travel", started_on=start, ended_on=end, **kw)  # type: ignore[arg-type]


class TestTravelPromptText:
    """Travel episodes in the ``life_context`` variable (#221)."""

    def test_a_trip_under_way_with_jet_lag(self) -> None:
        trip = _trip(
            date(2026, 11, 3), date(2026, 11, 6), equipment="hotel_gym", tz_shift_hours=-6.0,
            note="Boston",
        )  # fmt: skip
        ctx = LifeContext(today=date(2026, 11, 4), episodes=[trip], ramp_back=None)

        assert _format_life_context(ctx) == (
            "- Travel: since Tuesday 2026-11-03 (day 2), until Friday 2026-11-06. "
            "Equipment: hotel gym (a few machines, dumbbells, a treadmill). "
            "Time zone: -6 h from home: jet lag, apply the jet-lag principles. Note: Boston"
        )

    def test_a_small_time_zone_shift_is_not_jet_lag(self) -> None:
        trip = _trip(date(2026, 11, 3), None, equipment="full_gym", tz_shift_hours=1.0)
        ctx = LifeContext(today=date(2026, 11, 3), episodes=[trip], ramp_back=None)

        assert _format_life_context(ctx) == (
            "- Travel: since Tuesday 2026-11-03 (day 1), return date not set. "
            "Equipment: full gym. Time zone: +1 h from home."
        )

    def test_a_trip_entered_in_advance_without_training(self) -> None:
        trip = _trip(date(2026, 11, 11), date(2026, 11, 12), equipment="none", no_training=True)
        ctx = LifeContext(today=date(2026, 11, 8), episodes=[trip], ramp_back=None)

        assert _format_life_context(ctx) == (
            "- Travel, planned: Wednesday 2026-11-11 to Thursday 2026-11-12 (2 days). "
            "Equipment: none (bodyweight only). No training possible on these days."
        )


async def test_the_context_reads_trips_entered_in_advance() -> None:
    async with test_session() as session:
        session.add(User(id=1, email="a@example.com", name="A", fitness_level="intermediate"))
        session.add(
            LifeContextEpisode(
                user_id=1, kind="travel", started_on=date(2026, 11, 11),
                ended_on=date(2026, 11, 13), equipment="none",
            )
        )  # fmt: skip
        await session.commit()

        ctx = await life_context_for(
            session, 1, date(2026, 11, 9), date(2026, 11, 15), today=date(2026, 11, 8)
        )

    assert [(e.kind, e.started_on, e.equipment) for e in ctx.episodes] == [
        ("travel", date(2026, 11, 11), "none")
    ]
