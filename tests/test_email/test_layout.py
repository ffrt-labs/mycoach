"""The shared email layout system (#231): width, spacing, dividers, dark mode.

Every email is rendered through its public ``send_*`` function with today's
(v4) content, and the HTML handed to ``send_email`` is inspected.
"""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from mycoach.coaching.life_context import Episode, LifeContext, RampBack
from mycoach.config import Settings
from mycoach.email import sender
from mycoach.models.life_context import EpisodeKind, Equipment, IllnessSeverity
from tests.test_coaching.prompt_cases import WEEK_2026_11_02_ADHERENCE
from tests.test_email.test_briefing_notes import NOTES as BRIEFING_NOTES
from tests.test_email.test_post_workout_notes import NOTES as POST_WORKOUT_NOTES
from tests.test_email.test_post_workout_stats import LAST_SWIM, ZONED_SWIM
from tests.test_email.test_recap_notes import GYM_SETS, HEALTH_WEEK, _load_focus
from tests.test_email.test_recap_notes import NOTES as RECAP_NOTES

# The stat grid, zone bar and trend's numbers (#239), on the fixture's own activity.
SWIM_NUMBERS = {k: v for k, v in ZONED_SWIM.items() if k not in ("external_id", "title")}


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
        # Three tips, the most there can be: thirds of the row are the uneven case.
        BRIEFING_NOTES
        | {"tonight": [*BRIEFING_NOTES["tonight"], {"tone": "good", "text": "Magnesium"}]},
        s,
        hrv={
            "hrv_7day_avg": 52,
            "hrv_status": 48,
            "sleep_score": 84,
            "sleep_duration_minutes": 470,
            "sleep_deep_minutes": 90,
            "sleep_rem_minutes": 110,
            "sleep_light_minutes": 260,
            "sleep_awake_minutes": 10,
            "resting_hr": 52,
        },
        life=LifeContext(
            today=date(2026, 11, 19),
            episodes=[
                Episode(
                    id=1,
                    kind=EpisodeKind.ILLNESS,
                    started_on=date(2026, 11, 19),
                    ended_on=None,
                    severity=IllnessSeverity.MILD,
                )
            ],
            ramp_back=None,
        ),
        open_this_week={
            "sessions": [
                {
                    "id": 11,  # the open session BRIEFING_NOTES recommends
                    "day": "Monday",
                    "day_of_week": 0,
                    "status": "overdue",
                    "sport": "gym",
                    "track": "gym",
                    "title": "Squat Day",
                    "intent": "strength",
                    "duration_minutes": 75,
                    "details": None,
                    "notes": None,
                }
            ],
            "days_remaining": 4,
        },
    )


def _post_workout(s: Settings) -> None:
    sender.send_post_workout(
        POST_WORKOUT_NOTES,
        "Morning Swim",
        s,
        activity={"external_id": "g-walk-1107", "title": "Morning Swim", **SWIM_NUMBERS},
        history=[LAST_SWIM],
        week=WEEK_2026_11_02_ADHERENCE,  # type: ignore[arg-type]
        life=LifeContext(
            today=date(2026, 11, 19),
            episodes=[],
            ramp_back=RampBack(
                episode_id=1, day=2, length=5, load_pct=60, ends_on=date(2026, 11, 22)
            ),
        ),
        open_this_week={
            "sessions": [
                {
                    "id": 1,
                    "day": "Monday",
                    "day_of_week": 0,
                    "status": "overdue",
                    "sport": "gym",
                    "track": "gym",
                    "title": "Squat Day",
                    "intent": "strength",
                    "duration_minutes": 75,
                    "details": None,
                    "notes": None,
                }
            ],
            "days_remaining": 4,
        },
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
                "intent": "strength",
                "notes": "Heavy",
                "details": {
                    "exercises": [
                        {"name": "Squat", "sets": 4, "reps": 5},
                        {"name": "Deadlift", "sets": 36, "reps": 5},
                    ]
                },
            },
            {
                "day_name": "Tuesday",
                "title": "Easy swim",
                "sport": "swimming",
                "duration_minutes": 45,
                "intent": "threshold",
                "notes": "The only hard session.",
                "details": {
                    "blocks": [
                        {
                            "phase": "main",
                            "set": "8×100",
                            "cue": None,
                            "target": "RPE 6",
                            "rest_s": 20,
                        }
                    ],
                    "foot": "Total ~2000 m.",
                },
            },
        ],
        "2026-11-16",
        availability_source="default",
        notes={
            "focus": "One quality day.",
            "guardrails": [{"when": "Legs heavy", "then": "Skip the strides"}],
            "goal_assessment": "On track.",
        },
        settings=s,
        life=LifeContext(
            today=date(2026, 11, 15),
            episodes=[
                Episode(
                    id=1,
                    kind=EpisodeKind.TRAVEL,
                    started_on=date(2026, 11, 18),
                    ended_on=date(2026, 11, 20),
                    equipment=Equipment.HOTEL_GYM,
                    tz_shift_hours=6,
                )
            ],
            ramp_back=None,
        ),
    )


def _weekly_recap(s: Settings) -> None:
    sender.send_weekly_recap(
        RECAP_NOTES | {"block_suggestion": "Start a 4-week strength block."},
        "2026-11-09",
        s,
        adherence=WEEK_2026_11_02_ADHERENCE,
        episodes=[],
        health_week=[
            *HEALTH_WEEK,
            {
                "snapshot_date": "2026-11-08",
                "load_focus": _load_focus(aerobic_low_target_min=300, aerobic_low_target_max=600),
            },
        ],
        gym_sets=GYM_SETS,
        has_mesocycle=False,
        plan_failed=True,
    )


def _weekly_recap_nothing_logged(s: Settings) -> None:
    """The 05 artboard: nothing done, no health data, the cause quick replies (#237)."""
    sessions = [
        dict(
            row,
            completed=False,
            status="not_done",
            on_planned_day=None,
            actual_day=None,
            substitute=None,
        )
        for row in WEEK_2026_11_02_ADHERENCE["sessions"]
    ]
    sender.send_weekly_recap(
        {"week_summary": "No activity, gym log or health data this week."},
        "2026-11-02",
        s,
        adherence=WEEK_2026_11_02_ADHERENCE
        | {"completed_sessions": 0, "substituted_sessions": 0, "sessions": sessions},
        episodes=[],
        today=date(2026, 11, 9),
        health_days_missing=7,
        garmin_auth_failed=True,
    )


def _weekly_recap_quick_replies(s: Settings) -> None:
    """The 05 artboard: a quiet week with Coach notes, so both the cause quick replies
    and Next week follow the hero."""
    sessions = [
        dict(row, completed=False, status="not_done", on_planned_day=None, actual_day=None)
        for row in WEEK_2026_11_02_ADHERENCE["sessions"]
    ]
    sender.send_weekly_recap(
        RECAP_NOTES,
        "2026-11-02",
        s,
        adherence=WEEK_2026_11_02_ADHERENCE
        | {"completed_sessions": 0, "substituted_sessions": 0, "sessions": sessions},
        episodes=[],
        today=date(2026, 11, 9),
        health_days_missing=7,
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
    "weekly_recap_nothing_logged": _weekly_recap_nothing_logged,
    "weekly_recap_quick_replies": _weekly_recap_quick_replies,
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
        cells.append(_style(style.group(1) if style else ""))
    return cells


def _style(decls: str) -> dict[str, str]:
    """An inline style's declarations as {property: value}."""
    return {
        k.strip(): v.strip()
        for k, _, v in (d.partition(":") for d in decls.split(";"))
        if k.strip()
    }


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


def _closes_layout(hero: dict[str, str]) -> bool:
    """A hero that holds all its own blocks (the weekly plan's strip and notices, 03
    artboard) ends 32 px down; the first section after it is a regular one."""
    return hero["padding-bottom"] == "32px"


def test_the_hero_opens_with_32px_and_the_first_section_after_it_sits_inside_it(
    html: str,
) -> None:
    """In the artboards the block after the hero (Do today, Next week, the stat grid)
    belongs to the hero: 0 / 32 px and no divider above it."""
    (hero,) = _tds(html, "hero")
    assert hero["padding-top"] == "32px"
    sections = _tds(html, "section")
    if sections and not _closes_layout(hero):
        assert sections[0] in _tds(html, "hero-block"), sections[0]


def test_a_hero_block_hugs_the_hero_with_no_divider(html: str) -> None:
    for style in _tds(html, "hero-block"):
        assert (style["padding-top"], style["padding-bottom"]) == ("0px", "32px"), style
        assert "border-top" not in style, style


def test_every_other_section_has_32_32_padding_under_a_divider(html: str) -> None:
    in_hero = _tds(html, "hero-block")
    for style in _tds(html, "section"):
        if style in in_hero:
            continue
        assert (style["padding-top"], style["padding-bottom"]) == ("32px", "32px"), style
        assert style.get("border-top") == DIVIDER, style


def test_the_recap_quick_replies_and_next_week_both_sit_in_the_hero() -> None:
    """The 05 artboard: the cause quick replies, then Next week, both 0 / 32 px under
    the hero with no divider; Sessions is the first regular section."""
    html = _render("weekly_recap_quick_replies")
    assert html.index("I was ill") < html.index("Next week") < html.index("Sessions")
    sections = _tds(html, "section")
    assert len(_tds(html, "hero-block")) == 2
    assert sections[:2] == _tds(html, "hero-block")
    assert (sections[2]["padding-top"], sections[2].get("border-top")) == ("32px", DIVIDER)


def test_the_weekly_plan_rules_sit_under_a_divider_with_32px_above() -> None:
    """The 03 artboard: "Rules this week" is a regular section, its label off the line."""
    html = _render("weekly_plan")
    (hero,) = _tds(html, "hero")
    assert hero["padding-bottom"] == "32px"
    rules = _tds(html, "section")[0]
    assert (rules["padding-top"], rules["padding-bottom"]) == ("32px", "32px")
    assert rules.get("border-top") == DIVIDER


def test_the_weekly_plan_default_schedule_notice_sits_in_the_hero() -> None:
    html = _render("weekly_plan")
    # The hero holds nested tables (the week strip), so locate by position, not by </td>.
    hero_start = html.index('class="px hero"')
    starts = [m.start() for m in re.finditer(r'class="px section', html)]
    assert hero_start < html.index("Planned against your standing schedule") < starts[0]
    assert starts[0] < html.index("Rules this week") < starts[1], "Guardrails come first (#243)"
    assert starts[1] < html.index("Squat Day") < starts[2], "then Monday's card"


def test_an_included_block_right_after_the_hero_is_the_first_section() -> None:
    """Without Coach notes, the code-rendered Last night block is what follows the hero."""
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        sender.send_daily_briefing(None, _settings(), hrv={"hrv_7day_avg": 52})
    html = str(mock_send.call_args[0][2])
    sections = _tds(html, "section")
    assert sections[0]["padding-top"] == "0px"
    assert html.index("HRV") > html.index('class="px section')


@pytest.mark.parametrize("name", ["daily_briefing", "post_workout", "weekly_recap"])
def test_todays_content_is_laid_out_in_sections(name: str) -> None:
    """The fixtures fill every field, so each coaching email has several sections."""
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


def test_every_badge_is_a_chip_coloured_from_the_shared_styles(html: str) -> None:
    from mycoach.email.tones import CHIP_STYLES

    classes = [c for attr in re.findall(r'class="([^"]*)"', html) for c in attr.split()]
    assert not [c for c in classes if c == "badge" or c.startswith("b-")]
    assert re.search(r"\.b-\w+\s*\{", html) is None, "no hand-typed badge colours"
    for c in classes:
        if c.startswith("c-"):
            assert c[2:] in CHIP_STYLES, c


def test_the_weekly_plan_sport_chips_keep_their_colours() -> None:
    html = _render("weekly_plan")
    assert '<span class="chip c-warm">gym</span>' in html
    assert '<span class="chip c-info">swimming</span>' in html


def test_side_padding_drops_to_20px_at_480px_and_below(html: str) -> None:
    rule = re.search(r"\.px\s*\{([^}]*)\}", _media_block(html, "max-width:480px"))
    assert rule
    assert re.search(r"padding-left:\s*20px\s*!important", rule.group(1))
    assert re.search(r"padding-right:\s*20px\s*!important", rule.group(1))


def test_the_weekly_plan_eyebrow_dates_the_week_like_the_artboard() -> None:
    html = _render("weekly_plan")
    assert '<div class="sec" style="margin-bottom:12px;">Week of 16 Nov</div>' in html
    assert "2026-11-16" not in html


def test_the_week_strip_day_cells_share_one_width_and_one_height() -> None:
    """Seven same-styled boxes in one table row: a row's cells share its height, and the
    fixed layout splits the width left by the gaps equally among them (03 artboard)."""
    html = _render("weekly_plan")
    start = html.rindex("<td", 0, html.index('class="ws-day'))
    strip = html[start : html.index("</table>", start)]
    cells = _tds(strip, "ws-day")
    assert len(cells) == 7
    # The box (border, radius, padding) is the row cell itself, not a table nested in it.
    assert "<table" not in strip
    for style in cells:
        assert style["border-radius"] == "8px"
        assert "width" not in style
    # Every day cell has the same box; the 4 px gaps are spacer cells between them.
    boxes = [{k: v for k, v in s.items() if k != "border"} for s in cells]
    assert all(b == boxes[0] for b in boxes), boxes
    assert len(re.findall(r'<td class="ws-gap"[^>]*width="4"', strip)) == 6


# --- Tiles in a row (#248): flush with the content edge, equal widths, an 8px gap ----


def _px(value: str) -> int:
    """A whole-pixel length: fractional px round differently across clients (Outlook)."""
    assert re.fullmatch(r"\d+px", value), value
    return int(value[:-2])


def _tile_table(html: str, cls: str) -> tuple[str, list[list[dict[str, str]]]]:
    """The first table of class ``cls``: its opening tag, and each row's cell styles."""
    start = html.index(f'class="{cls}"')
    tag = html[html.rindex("<table", 0, start) : html.index(">", start) + 1]
    body = html[start : html.index("</table>", start)]
    rows = [
        [_style(s) for s in re.findall(r'<td\b[^>]*style="([^"]*)"', row)]
        for row in body.split("<tr>")[1:]
    ]
    return tag, rows


def _assert_flush_equal_tiles(html: str, cls: str) -> None:
    table, rows = _tile_table(html, cls)
    assert "border-spacing:8px" not in table, "cellspacing insets the tiles"
    assert "table-layout:fixed" in table, "content must not widen a column"
    assert rows
    for cells in rows:
        widths = {c["width"] for c in cells}
        assert len(widths) == 1, f"uneven columns: {widths}"
        assert len(cells) * float(widths.pop().rstrip("%")) <= 100.01
        left = [_px(c["padding-left"]) for c in cells]
        right = [_px(c["padding-right"]) for c in cells]
        assert left[0] == 0 and right[-1] == 0, "the row is flush with the content edge"
        lost = [lft + rgt for lft, rgt in zip(left, right, strict=True)]
        assert max(lost) - min(lost) <= 1, "every tile gives the gaps the same width, ±1px"
        for i in range(len(cells) - 1):
            assert right[i] + left[i + 1] == 8, "an 8px gap between tiles"


@pytest.mark.parametrize("name", ["post_workout", "daily_briefing"])
def test_the_stat_grid_is_flush_with_equal_columns_and_8px_gaps(name: str) -> None:
    _assert_flush_equal_tiles(_render(name), "stat-grid")


def test_the_stat_grid_rows_are_8px_apart() -> None:
    _, rows = _tile_table(_render("post_workout"), "stat-grid")
    assert len(rows) > 1, "the fixture fills two rows"
    for cells in rows[:-1]:
        assert {c["padding-bottom"] for c in cells} == {"8px"}
    assert {c.get("padding-bottom", "0px") for c in rows[-1]} == {"0px"}


def test_the_tonight_tips_are_flush_with_equal_columns_and_8px_gaps() -> None:
    _assert_flush_equal_tiles(_render("daily_briefing"), "tonight")


def test_the_recommended_chip_sits_8px_after_the_option_title() -> None:
    html = _render("daily_briefing")
    chip = re.search(r'<span style="([^"]*)"><span class="chip [^"]*">Recommended<', html)
    assert chip, "the chip is wrapped"
    assert "margin-left:8px" in chip.group(1)
    assert html[: chip.start()].endswith("</span>"), "no stray space before it"


@pytest.mark.parametrize("name", ["post_workout", "daily_briefing"])
def test_a_stat_value_and_its_unit_never_wrap(name: str) -> None:
    values = re.findall(r'<div class="strong tile-v" style="([^"]*)">', _render(name))
    assert values
    for decls in values:
        assert _style(decls).get("white-space") == "nowrap", decls


@pytest.mark.parametrize("name", ["post_workout", "weekly_recap"])  # the briefing has none
def test_the_action_numbers_are_centred_in_their_circles(name: str) -> None:
    markers = re.findall(r'<span style="([^"]*)">(\d)</span>', _render(name))
    assert [n for _, n in markers][:2] == ["1", "2"]
    for decls, _ in markers:
        style = _style(decls)
        # The line box fills the circle: no font shorthand resetting it to "normal" after.
        order = [k for k in style if k in ("line-height", "font")]
        assert order[-1] == "line-height" or "/" + style["height"] in style["font"], decls
        assert style.get("line-height", style["height"]) == style["height"], decls


# --- Borders and badges in dark mode (#252) -------------------------------------------


def test_dark_mode_overrides_every_border_class(html: str) -> None:
    from mycoach.email.tones import BORDER_STYLES

    dark = _media_block(html, "prefers-color-scheme: dark")
    for name, style in BORDER_STYLES.items():
        rule = re.search(r"\.bd-" + re.escape(name) + r"\s*\{([^}]*)\}", dark)
        assert rule, name
        assert f"border-color:{style.dark} !important" in rule.group(1), name


def _dark_border_classes(dark: str) -> set[str]:
    """Every class the dark-mode block gives an ``!important`` border colour."""
    classes: set[str] = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", dark):
        if re.search(r"border-color:[^;]*!important", body):
            classes |= set(re.findall(r"\.([\w-]+)", selectors))
    return classes


def test_every_inline_border_colour_has_a_dark_mode_override(html: str) -> None:
    """An inline border colour beats the style sheet unless dark mode overrides it with
    !important: without a class that does, the border stays light on a dark page."""
    overridden = _dark_border_classes(_media_block(html, "prefers-color-scheme: dark"))
    body = html[html.index("<body") :]
    bare = []
    for tag in re.findall(r"<\w+\b[^>]*>", body):
        style = re.search(r'style="([^"]*)"', tag)
        if not style:
            continue
        coloured = [
            f"{k}:{v}"
            for k, v in _style(style.group(1)).items()
            if k.startswith("border") and re.search(r"#[0-9a-fA-F]{3,6}", v)
        ]
        classes = re.search(r'class="([^"]*)"', tag)
        if coloured and not overridden & set(classes.group(1).split() if classes else []):
            bare.append((coloured, tag[:120]))
    assert not bare, bare
