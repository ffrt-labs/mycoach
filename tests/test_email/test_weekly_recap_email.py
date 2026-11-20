"""The weekly recap email leads with the week's Completion (#232).

Every count, status and episode here is code-rendered from the week's Completion
(``get_plan_adherence_for_week``) and its Life context episodes, never from the LLM.
Tests render through ``send_weekly_recap`` and read the HTML by element class.
"""

import re
from datetime import date
from html.parser import HTMLParser
from typing import Any
from unittest.mock import patch

import pytest

from mycoach.coaching.life_context import Episode
from mycoach.config import Settings
from mycoach.email.sender import send_weekly_recap
from mycoach.models.life_context import EpisodeKind, Equipment, IllnessSeverity
from tests.test_coaching.prompt_cases import WEEK_2026_11_02_ADHERENCE


def _settings() -> Settings:
    return Settings(
        email_enabled=True,
        email_from="coach@example.com",
        email_to="athlete@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        env="test",
        app_base_url="https://coach.example.com",
    )


class _ByClass(HTMLParser):
    """Collects the text of every element carrying ``cls``, one string per element."""

    _VOID = frozenset({"br", "img", "meta", "link", "hr", "input"})

    def __init__(self, cls: str) -> None:
        super().__init__()
        self.cls = cls
        self.found: list[str] = []
        self._open: list[tuple[int, list[str]]] = []  # (depth, chunks) per open match
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VOID:
            return
        self._depth += 1
        classes = (dict(attrs).get("class") or "").split()
        if self.cls in classes:
            self._open.append((self._depth, []))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID:
            return
        if self._open and self._open[-1][0] == self._depth:
            _, chunks = self._open.pop()
            self.found.append(" ".join("".join(chunks).split()))
            if self._open:
                self._open[-1][1].extend(chunks)
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[-1][1].append(data)


def texts(html: str, cls: str) -> list[str]:
    parser = _ByClass(cls)
    parser.feed(html)
    return parser.found


def text(html: str, cls: str) -> str:
    found = texts(html, cls)
    assert len(found) == 1, f"expected one .{cls}, found {found}"
    return found[0]


def _render(adherence: Any = WEEK_2026_11_02_ADHERENCE, **kwargs: Any) -> str:
    content = kwargs.pop("content", {"week_summary": "Both lifts and the swim are in."})
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_weekly_recap(
            content,
            week_start="2026-11-02",
            settings=_settings(),
            adherence=adherence,
            **kwargs,
        )
    return str(mock_send.call_args[0][2])


def _excused_week(**overrides: Any) -> dict[str, Any]:
    return {
        "total_sessions": 0,
        "completed_sessions": 0,
        "substituted_sessions": 0,
        "adapted_sessions": 0,
        "excused_sessions": 4,
        "excused_days": {"excused_ill": 5},
        "sessions": [],
        "extras": [],
    } | overrides


class TestHero:
    def test_the_h1_is_the_first_segment_of_the_completion_line(self) -> None:
        html = _render()

        assert text(html, "completion-h1") == "4/5 done"
        assert text(html, "completion-rest") == "· 1 substituted"

    def test_a_week_without_substitutions_shows_the_bare_ratio(self) -> None:
        html = _render({"total_sessions": 3, "completed_sessions": 2, "substituted_sessions": 0})

        assert text(html, "completion-h1") == "2/3 done"
        assert texts(html, "completion-rest") == []

    def test_sessions_excused_by_illness_sit_beside_the_ratio(self) -> None:
        """Five planned, sick three days, two done (#220)."""
        html = _render(
            {
                "total_sessions": 2,
                "completed_sessions": 2,
                "substituted_sessions": 0,
                "excused_sessions": 3,
                "excused_days": {"excused_ill": 3},
            }
        )

        assert text(html, "completion-h1") == "2/2 done"
        assert text(html, "completion-rest") == "· 3 excused (ill 3 days)"

    def test_sessions_adapted_on_a_trip_sit_beside_the_ratio(self) -> None:
        """#221: adapted sessions are done; a no-training travel day excuses one."""
        html = _render(
            {
                "total_sessions": 3,
                "completed_sessions": 2,
                "substituted_sessions": 0,
                "adapted_sessions": 1,
                "excused_sessions": 1,
                "excused_days": {"excused_travel": 1},
            }
        )

        assert text(html, "completion-h1") == "2/3 done"
        assert text(html, "completion-rest") == (
            "· 1 adapted (travel) · 1 excused (no training while travelling 1 day)"
        )

    def test_the_eyebrow_names_the_week(self) -> None:
        assert "Week of 2 Nov · completion" in texts(_render(), "sec")

    def test_the_coach_summary_follows_the_completion(self) -> None:
        html = _render()

        assert html.index("completion-h1") < html.index("Both lifts and the swim are in.")

    def test_on_planned_day_counts_only_sessions_with_a_known_day(self) -> None:
        # Squat done Wednesday, swim, deadlift and the padel on their days; Thursday's
        # intervals were not done, so their day is unknown and left out.
        assert text(_render(), "on-planned-day") == "On planned day 3/4 · informational"

    def test_on_planned_day_is_left_out_when_no_session_has_a_known_day(self) -> None:
        assert texts(_render(_excused_week()), "on-planned-day") == []

    def test_an_all_excused_week_shows_the_excused_count_never_zero_of_zero(self) -> None:
        html = _render(_excused_week())

        assert text(html, "completion-h1") == "4 excused"
        assert text(html, "completion-rest") == "· ill 5 days"
        assert "0/0" not in html

    def test_a_week_with_no_plan_says_so(self) -> None:
        html = _render(None)

        assert text(html, "completion-h1") == "No plan this week"
        assert texts(html, "completion-rest") == []
        assert texts(html, "on-planned-day") == []

    def test_the_hero_never_shows_an_llm_completion_figure(self) -> None:
        html = _render(content={"week_summary": "Fine.", "adherence_analysis": "9/9 done"})

        assert text(html, "completion-h1") == "4/5 done"


def _session(status: str, **overrides: Any) -> dict[str, Any]:
    done = status in ("done", "substituted", "adapted_travel")
    return {
        "id": 7,
        "day": "Thursday",
        "sport": "running",
        "title": "Intervals 6x3 min",
        "intent": "intervals",
        "completed": done,
        "status": status,
        "on_planned_day": True if done else None,
        "actual_day": "Thursday" if done else None,
        "substitute": None,
    } | overrides


def _week_of(*sessions: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "total_sessions": len(sessions),
        "completed_sessions": sum(s["completed"] for s in sessions),
        "substituted_sessions": 0,
        "sessions": list(sessions),
        "extras": [],
    } | overrides


_PADEL = {
    "external_id": "g-padel-1108",
    "sport": "padel",
    "title": "Padel",
    "day": "Sunday",
    "intent": "aerobic_base",
    "duration_minutes": 101,
    "avg_hr": 118,
    "training_effect_aerobic": 2.7,
}


class TestSessionRows:
    def test_one_row_per_planned_session_in_planned_day_order(self) -> None:
        rows = texts(_render(), "session-row")

        assert [r.split()[0] for r in rows] == ["MON", "TUE", "THU", "FRI", "SUN"]
        assert "Squat" in rows[0]
        assert "Gym · strength" in rows[0]

    def test_rows_follow_the_planned_day_whatever_order_they_arrive_in(self) -> None:
        week = _week_of(
            _session("not_done", day="Saturday", title="Long run"),
            _session("not_done", day="Tuesday", title="Swim"),
        )

        assert [r.split()[0] for r in texts(_render(week), "session-row")] == ["TUE", "SAT"]

    @pytest.mark.parametrize(
        ("status", "style", "label"),
        [
            ("done", "good", "Done"),
            ("substituted", "good", "Substituted"),
            ("adapted_travel", "info", "Adapted (travel)"),
            ("excused_ill", "neutral", "Excused (ill)"),
            ("excused_travel", "neutral", "Excused (no training while travelling)"),
            ("not_done_travelling", "neutral-outline", "Not done (travelling)"),
            ("not_done", "watch", "Not done"),
        ],
    )
    def test_each_status_renders_its_chip(self, status: str, style: str, label: str) -> None:
        html = _render(_week_of(_session(status)))

        assert f'<span class="chip c-{style}">{label}</span>' in html
        assert text(html, "session-row").endswith(label)

    def test_a_session_done_on_another_day_says_which_in_muted_text(self) -> None:
        details = texts(_render(), "session-detail")

        assert details == [
            "Done Wednesday",
            "← Padel Sun · 101 min · avg HR 118 · aerobic TE 2.7",
        ]

    def test_a_session_done_on_its_day_has_no_detail(self) -> None:
        assert texts(_render(_week_of(_session("done"))), "session-detail") == []

    @pytest.mark.parametrize("status", ["substituted", "adapted_travel"])
    def test_a_filled_session_shows_what_filled_it(self, status: str) -> None:
        html = _render(_week_of(_session(status, substitute=_PADEL)))

        assert text(html, "session-detail") == (
            "← Padel Sun · 101 min · avg HR 118 · aerobic TE 2.7"
        )

    def test_a_substitute_line_leaves_out_what_garmin_did_not_record(self) -> None:
        padel = _PADEL | {"avg_hr": None, "training_effect_aerobic": None}

        html = _render(_week_of(_session("substituted", substitute=padel)))

        assert text(html, "session-detail") == "← Padel Sun · 101 min"

    @pytest.mark.parametrize("status", ["excused_ill", "excused_travel"])
    def test_an_excused_session_is_struck_through(self, status: str) -> None:
        html = _render(_week_of(_session(status)))

        assert re.search(r'class="[^"]*session-title[^"]*"[^>]*line-through[^>]*>Intervals', html)

    def test_a_session_still_owed_is_not_struck_through(self) -> None:
        assert "line-through" not in _render(_week_of(_session("not_done")))

    def test_a_week_with_no_plan_lists_no_sessions(self) -> None:
        assert texts(_render(None), "session-row") == []


class TestExtras:
    def test_extras_are_listed_apart_as_training_load_only(self) -> None:
        html = _render()

        assert text(html, "extras-label") == "Extras · training load only, not counted"
        assert texts(html, "extra-row") == ["SAT Walk · 45 min · recovery"]

    def test_extras_are_never_counted(self) -> None:
        walk = WEEK_2026_11_02_ADHERENCE["extras"][0]
        week = WEEK_2026_11_02_ADHERENCE | {"extras": [walk, walk | {"day": "Monday"}]}

        assert text(_render(week), "completion-h1") == "4/5 done"

    def test_extras_follow_the_day_they_were_done(self) -> None:
        walk = WEEK_2026_11_02_ADHERENCE["extras"][0]
        week = WEEK_2026_11_02_ADHERENCE | {
            "extras": [walk, walk | {"day": "Monday", "title": "Yoga", "intent": None}]
        }

        assert texts(_render(week), "extra-row") == [
            "MON Yoga · 45 min",
            "SAT Walk · 45 min · recovery",
        ]

    def test_a_week_without_extras_has_no_extras_list(self) -> None:
        html = _render(WEEK_2026_11_02_ADHERENCE | {"extras": []})

        assert texts(html, "extras-label") == []


def _illness(started: date, ended: date | None, severity: str = "sick") -> Episode:
    return Episode(
        id=1,
        kind=EpisodeKind.ILLNESS,
        started_on=started,
        ended_on=ended,
        severity=IllnessSeverity(severity),
    )


def _trip(started: date, ended: date | None, **fields: Any) -> Episode:
    return Episode(
        id=2,
        kind=EpisodeKind.TRAVEL,
        started_on=started,
        ended_on=ended,
        equipment=fields.pop("equipment", Equipment.HOTEL_GYM),
        **fields,
    )


class TestLifeContext:
    def test_an_ended_illness_names_its_days_severity_and_ramp_back(self) -> None:
        # Sick 11-15 Nov, five days: five days of ramp-back, to 20 Nov.
        html = _render(episodes=[_illness(date(2026, 11, 11), date(2026, 11, 15))])

        assert texts(html, "episode") == ["Ill 11–15 Nov · sick · ramp-back to 20 Nov"]

    def test_a_mild_illness_ramps_back_three_days(self) -> None:
        html = _render(episodes=[_illness(date(2026, 11, 3), date(2026, 11, 4), "mild")])

        assert texts(html, "episode") == ["Ill 3–4 Nov · mild · ramp-back to 7 Nov"]

    def test_an_open_illness_says_since_when(self) -> None:
        html = _render(episodes=[_illness(date(2026, 11, 6), None)])

        assert texts(html, "episode") == ["Ill since 6 Nov · sick"]

    def test_a_trip_names_its_days_and_equipment(self) -> None:
        html = _render(episodes=[_trip(date(2026, 11, 9), date(2026, 11, 12))])

        assert texts(html, "episode") == ["Trip 9–12 Nov · hotel gym"]

    def test_a_trip_with_no_training_possible_says_so(self) -> None:
        trip = _trip(date(2026, 11, 7), date(2026, 11, 7), no_training=True)

        assert texts(_render(episodes=[trip]), "episode") == [
            "Trip 7 Nov · hotel gym · no training possible"
        ]

    def test_an_episode_across_months_names_both(self) -> None:
        html = _render(episodes=[_trip(date(2026, 10, 30), date(2026, 11, 3))])

        assert texts(html, "episode") == ["Trip 30 Oct–3 Nov · hotel gym"]

    def test_every_episode_touching_the_week_is_listed(self) -> None:
        html = _render(
            episodes=[
                _illness(date(2026, 11, 3), date(2026, 11, 4), "mild"),
                _trip(date(2026, 11, 7), date(2026, 11, 8)),
            ]
        )

        assert len(texts(html, "episode")) == 2
        assert texts(html, "life-context-empty") == []

    def test_a_week_with_no_episodes_offers_to_log_one_for_that_week(self) -> None:
        html = _render(episodes=[])

        assert text(html, "life-context-empty") == (
            "No illness or travel logged this week. Something going on? Log it →"
        )
        assert (
            'href="https://coach.example.com/?log=illness&amp;from=2026-11-02'
            '&amp;to=2026-11-08#life-context"' in html
        )

    def test_episodes_that_could_not_be_read_leave_the_section_out(self) -> None:
        html = _render(episodes=None)

        assert "Life context" not in texts(html, "sec")
        assert texts(html, "life-context-empty") == []


def test_no_code_rendered_text_says_missed_moved_or_skipped() -> None:
    week = _week_of(
        *(
            _session(status, title=f"Session {i}", day=day, substitute=_PADEL)
            for i, (status, day) in enumerate(
                [
                    ("done", "Monday"),
                    ("substituted", "Tuesday"),
                    ("adapted_travel", "Wednesday"),
                    ("excused_ill", "Thursday"),
                    ("excused_travel", "Friday"),
                    ("not_done_travelling", "Saturday"),
                    ("not_done", "Sunday"),
                ]
            )
        ),
        extras=WEEK_2026_11_02_ADHERENCE["extras"],
        excused_sessions=2,
        excused_days={"excused_ill": 1, "excused_travel": 1},
    )
    shown = text(
        _render(
            week,
            content={},
            episodes=[
                _illness(date(2026, 11, 5), date(2026, 11, 5)),
                _trip(date(2026, 11, 6), date(2026, 11, 7), no_training=True),
            ],
        ),
        "email",
    ).lower()

    assert "excused (ill)" in shown  # the visible email, not just its head
    for word in ("missed", "moved", "skipped", "skip", "late", "swap"):
        assert not re.search(rf"\b{word}\b", shown), word
