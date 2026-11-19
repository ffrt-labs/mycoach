"""The shared email layout system (#231): width, spacing, dividers, dark mode.

Every email is rendered through its public ``send_*`` function with today's
(v4) content, and the HTML handed to ``send_email`` is inspected.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from mycoach.config import Settings
from mycoach.email import sender


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        env="test",
    )


def _daily_briefing(s: Settings) -> None:
    sender.send_daily_briefing(
        {
            "readiness_verdict": "go_hard",
            "recovery_status": "Fully recovered",
            "sleep_assessment": "Good sleep",
            "workout_adjustments": "Keep it easy",
            "key_metrics": {"resting_hr": 52, "sleep_score": 84},
            "sleep_recommendation": "Sleep by 10pm",
        },
        s,
        hrv={"hrv_7day_avg": 52, "hrv_status": 48},
    )


def _post_workout(s: Settings) -> None:
    sender.send_post_workout(
        {
            "performance_summary": "Strong session",
            "planned_vs_actual": "On plan",
            "hr_analysis": "Mostly Z2",
            "performance_trends": "Pace improving",
            "key_highlights": ["Negative split"],
            "areas_for_improvement": ["Longer warm-up"],
            "next_session_recommendations": "Repeat 5x200",
            "recovery_notes": "24 h",
        },
        "Morning Swim",
        s,
    )


def _weekly_plan(s: Settings) -> None:
    sender.send_weekly_plan(
        "Build aerobic base",
        [
            {
                "day_name": "Monday",
                "title": "Squat Day",
                "sport": "gym",
                "duration_minutes": 75,
                "notes": "Heavy",
                "details": {"exercises": [{"name": "Squat", "sets": 4, "reps": 5}]},
            },
            {
                "day_name": "Tuesday",
                "title": "Easy swim",
                "sport": "swimming",
                "duration_minutes": 45,
                "details": {"distance_km": 2},
            },
        ],
        "2026-11-16",
        availability_source="default",
        settings=s,
    )


def _weekly_recap(s: Settings) -> None:
    sender.send_weekly_recap(
        {
            "week_summary": "Solid week",
            "adherence_analysis": "Four of five",
            "training_load_analysis": "Load steady",
            "recovery_assessment": "Recovered",
            "performance_highlights": ["PR on squat"],
            "areas_of_concern": ["Sleep"],
            "next_week_recommendations": "Hold volume",
            "mesocycle_progress": "Week 3 of 4",
            "coach_recommendations": ["Deload soon"],
        },
        "2026-11-09",
        s,
        adherence={"total_sessions": 5, "completed_sessions": 4, "substituted_sessions": 1},
    )


def _no_availability(s: Settings) -> None:
    sender.send_no_availability("2026-11-23", s)


def _garmin_auth_failed(s: Settings) -> None:
    sender.send_garmin_auth_failed("rate limited", s)


def _briefing_unavailable(s: Settings) -> None:
    sender.send_briefing_unavailable(
        "Thu 19 Nov",
        failures=1,
        is_broken=False,
        can_still_retry=True,
        detail="No sleep data",
        last_upload=datetime(2026, 11, 18, 5, 20, tzinfo=UTC),
        device_name="Forerunner 965",
        settings=s,
    )


EMAILS: dict[str, Callable[[Settings], None]] = {
    "daily_briefing": _daily_briefing,
    "post_workout": _post_workout,
    "weekly_plan": _weekly_plan,
    "weekly_recap": _weekly_recap,
    "no_availability": _no_availability,
    "garmin_auth_failed": _garmin_auth_failed,
    "briefing_unavailable": _briefing_unavailable,
}


def _render(name: str) -> str:
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        EMAILS[name](_settings())
    return str(mock_send.call_args[0][2])


@pytest.fixture(params=sorted(EMAILS))
def html(request: pytest.FixtureRequest) -> str:
    return _render(request.param)


def test_every_email_is_a_600px_table_and_no_420_remains(html: str) -> None:
    assert re.search(r'<table[^>]*class="email"[^>]*width="600"', html)
    assert "max-width:600px" in html
    assert "420" not in html


def _tds(html: str, cls: str) -> list[dict[str, str]]:
    """Every ``<td>`` carrying class ``cls``, as {property: value} of its inline style."""
    cells = []
    for tag in re.findall(r"<td\b[^>]*>", html):
        classes = re.search(r'class="([^"]*)"', tag)
        if not classes or cls not in classes.group(1).split():
            continue
        style = re.search(r'style="([^"]*)"', tag)
        decls = (style.group(1) if style else "").split(";")
        cells.append(
            {k.strip(): v.strip() for k, _, v in (d.partition(":") for d in decls) if k.strip()}
        )
    return cells


def _media_block(html: str, query: str) -> str:
    """The body of the first ``@media`` rule whose query contains ``query``."""
    start = re.search(r"@media[^{]*" + re.escape(query) + r"[^{]*\{", html)
    assert start, f"no @media rule for {query}"
    depth, i = 1, start.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        i += 1
    return html[start.end() : i - 1]


def test_side_padding_is_40px_inline(html: str) -> None:
    cells = _tds(html, "px")
    assert cells
    for style in cells:
        assert style.get("padding-left") == "40px", style
        assert style.get("padding-right") == "40px", style


DIVIDER = "1px solid #e7e5e4"


def test_the_hero_opens_with_32px_and_the_first_section_after_it_hugs_it(html: str) -> None:
    (hero,) = _tds(html, "hero")
    assert hero["padding-top"] == "32px"
    sections = _tds(html, "section")
    if sections:
        first = sections[0]
        assert (first["padding-top"], first["padding-bottom"]) == ("0px", "32px")


def test_every_later_section_has_32_32_padding(html: str) -> None:
    for style in _tds(html, "section")[1:]:
        assert (style["padding-top"], style["padding-bottom"]) == ("32px", "32px"), style


def test_every_section_has_a_divider_including_the_first_after_the_hero(html: str) -> None:
    for style in _tds(html, "section"):
        assert style.get("border-top") == DIVIDER, style


def test_the_weekly_plan_default_schedule_notice_sits_in_the_hero() -> None:
    html = _render("weekly_plan")
    hero = re.search(r'<td class="px hero"[^>]*>(.*?)</td>', html, re.S)
    assert hero
    assert "Planned against your standing schedule" in hero.group(1)
    starts = [m.start() for m in re.finditer(r'class="px section', html)]
    assert html.index("Planned against your standing schedule") < starts[0]
    assert starts[0] < html.index("Squat Day") < starts[1], "the first section is Monday's card"


def test_an_included_block_right_after_the_hero_is_the_first_section() -> None:
    """With no key metrics, the code-rendered HRV block is what follows the hero."""
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        sender.send_daily_briefing(
            {"readiness_verdict": "rest"}, _settings(), hrv={"hrv_7day_avg": 52}
        )
    html = str(mock_send.call_args[0][2])
    sections = _tds(html, "section")
    assert sections[0]["padding-top"] == "0px"
    assert html.index("HRV") > html.index('class="px section')


@pytest.mark.parametrize("name", ["daily_briefing", "post_workout", "weekly_recap"])
def test_todays_content_is_laid_out_in_sections(name: str) -> None:
    """The fixtures fill every v4 field, so each coaching email has several sections."""
    assert len(_tds(_render(name), "section")) >= 4


def test_the_cta_is_full_width_with_a_10px_radius(html: str) -> None:
    (cta,) = re.findall(r'<a [^>]*class="cta"[^>]*>', html)
    assert "display:block" in cta
    assert "border-radius:10px" in cta
    assert "padding:15px 0" in cta


def test_dark_mode_overrides_every_tone_and_chip(html: str) -> None:
    from mycoach.email.tones import CHIP_STYLES

    dark = _media_block(html, "prefers-color-scheme: dark")
    for name, style in CHIP_STYLES.items():
        rule = re.search(r"\.c-" + re.escape(name) + r"\s*\{([^}]*)\}", dark)
        assert rule, name
        assert f"background:{style.dark.bg} !important" in rule.group(1)
        assert f"color:{style.dark.fg} !important" in rule.group(1)


def test_side_padding_drops_to_20px_at_480px_and_below(html: str) -> None:
    rule = re.search(r"\.px\s*\{([^}]*)\}", _media_block(html, "max-width:480px"))
    assert rule
    assert re.search(r"padding-left:\s*20px\s*!important", rule.group(1))
    assert re.search(r"padding-right:\s*20px\s*!important", rule.group(1))
