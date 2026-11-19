"""Shared tone, status, intent and life-context maps for every email (#231).

Expected colours are the spec's tables (docs/superpowers/specs/2026-11-19-email-redesign.md);
labels are the glossary words, which live in STATUS_LABEL and SessionIntent.
"""

import re

import pytest

from mycoach.coaching.life_context import DayEffect
from mycoach.email import tones
from mycoach.email.sender import _jinja_env
from mycoach.models.plan import SessionIntent

# --- Tones -------------------------------------------------------------------


def test_the_four_tones_carry_the_spec_colours() -> None:
    assert {
        t: (tones.CHIP_STYLES[t].light.bg, tones.CHIP_STYLES[t].light.fg) for t in tones.TONES
    } == {
        "good": ("#ecfdf5", "#047857"),
        "neutral": ("#f4f4f5", "#52525b"),
        "watch": ("#fffbeb", "#b45309"),
        "alert": ("#fef2f2", "#b91c1c"),
    }


def test_tone_chip_maps_an_llm_tone_label_to_its_chip() -> None:
    chip = tones.tone_chip("watch", "Adjust")
    assert (chip.label, chip.css) == ("Adjust", "chip c-watch")


def test_an_unknown_tone_falls_back_to_neutral() -> None:
    assert tones.tone_chip("shouty", "x").css == "chip c-neutral"


# --- Session status ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "label", "style"),
    [
        ("done", "Done", "good"),
        ("substituted", "Substituted", "good"),
        ("adapted_travel", "Adapted (travel)", "info"),
        ("excused_ill", "Excused (ill)", "neutral"),
        ("excused_travel", "Excused (no training while travelling)", "neutral"),
        ("not_done_travelling", "Not done (travelling)", "neutral-outline"),
        ("not_done", "Not done", "watch"),
        ("extra", "Extra", "muted-outline"),
    ],
)
def test_each_session_status_has_its_glossary_label_and_tone(
    status: str, label: str, style: str
) -> None:
    chip = tones.SESSION_STATUS_CHIP[status]
    assert (chip.label, chip.css) == (label, f"chip c-{style}")


def test_status_labels_are_read_from_status_label_not_retyped() -> None:
    from mycoach.coaching.life_context import STATUS_LABEL

    for status, label in STATUS_LABEL.items():
        assert tones.SESSION_STATUS_CHIP[status].label.lower() == label


def test_alert_is_never_a_session_status() -> None:
    assert all(c.style != "alert" for c in tones.SESSION_STATUS_CHIP.values())


# --- Session intent ----------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "label", "style"),
    [
        ("strength", "strength", "intent-strength"),
        ("aerobic_base", "aerobic base", "intent-aerobic_base"),
        ("threshold", "threshold", "accent"),
        ("intervals", "intervals", "accent"),
        ("recovery", "recovery", "intent-recovery"),
        ("skill", "skill", "intent-skill"),
    ],
)
def test_each_intent_has_a_spaced_label_and_its_chip(intent: str, label: str, style: str) -> None:
    chip = tones.intent_chip(intent)
    assert chip is not None
    assert (chip.label, chip.css) == (label, f"chip c-{style}")


def test_every_session_intent_is_mapped() -> None:
    assert set(tones.INTENT_CHIP) == {i.value for i in SessionIntent}


def test_no_intent_or_an_unknown_one_has_no_chip() -> None:
    assert tones.intent_chip(None) is None
    assert tones.intent_chip("yoga") is None


def test_the_quality_intents_are_threshold_and_intervals() -> None:
    assert {"threshold", "intervals"} == tones.QUALITY_INTENTS


def test_intent_outlines_use_the_spec_colours() -> None:
    assert {
        name: (tones.CHIP_STYLES[name].light.border, tones.CHIP_STYLES[name].light.fg)
        for name in ("intent-strength", "intent-aerobic_base", "intent-recovery", "intent-skill")
    } == {
        "intent-strength": ("#fed7aa", "#9a3412"),
        "intent-aerobic_base": ("#bfdbfe", "#1d4ed8"),
        "intent-recovery": ("#a7f3d0", "#047857"),
        "intent-skill": ("#ddd6fe", "#6d28d9"),
    }
    accent = tones.CHIP_STYLES["accent"].light
    assert (accent.bg, accent.fg) == ("#ea6d1f", "#ffffff")


# --- Life context ------------------------------------------------------------


def test_each_life_context_state_has_its_banner_tone() -> None:
    assert tones.LIFE_CONTEXT_STYLE == {
        DayEffect.SICK: "alert",
        DayEffect.MILD: "watch",
        DayEffect.TRAVEL: "info",
        DayEffect.TRAVEL_NO_TRAINING: "neutral",
        "ramp_back": "good",
    }


def test_travel_is_the_spec_blue() -> None:
    info = tones.CHIP_STYLES["info"].light
    assert (info.bg, info.fg) == ("#eff6ff", "#1d4ed8")


# --- Templates read the maps -------------------------------------------------


def test_the_chip_macro_renders_a_mapped_status() -> None:
    html = _jinja_env.from_string(
        '{% import "components/_layout.html" as ui %}'
        "{{ ui.chip(session_status_chip['not_done']) }}{{ ui.chip(intent_chip(none)) }}"
    ).render()
    assert html == '<span class="chip c-watch">Not done</span>'


# --- Dark mode ---------------------------------------------------------------


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("name", sorted(tones.CHIP_STYLES))
def test_every_chip_is_legible_in_dark_mode(name: str) -> None:
    dark = tones.CHIP_STYLES[name].dark
    assert re.fullmatch(r"#[0-9a-f]{6}", dark.bg) and re.fullmatch(r"#[0-9a-f]{6}", dark.fg)
    assert _contrast(dark.fg, dark.bg) >= 4.5


@pytest.mark.parametrize("name", sorted(tones.TONES))
def test_every_tone_is_legible_in_light_mode(name: str) -> None:
    light = tones.CHIP_STYLES[name].light
    assert _contrast(light.fg, light.bg) >= 4.5
