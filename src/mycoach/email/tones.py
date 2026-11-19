"""Shared tone, session-status, Session intent and Life context maps for every email (#231).

Tones are the only colours that carry meaning. The LLM (in later prompt versions)
picks a tone *label*; code maps it to a chip here, and ``base.html`` generates the
light and dark CSS for every style from ``CHIP_STYLES``. Templates read these maps
(registered as Jinja globals in ``sender``) and never re-type a label or a colour:
status labels come from ``STATUS_LABEL``, intent labels from ``INTENT_LABEL``.

Alert is "act now" (a sick banner, a failed job). It is never a session status:
"not done" is watch.
"""

from dataclasses import dataclass

from mycoach.coaching.life_context import STATUS_LABEL, DayEffect
from mycoach.models.plan import INTENT_LABEL

DARK_PAGE = "#0b0c0e"
"""The dark-mode page background the dark chip colours sit on."""


@dataclass(frozen=True)
class Colours:
    bg: str
    fg: str
    border: str


@dataclass(frozen=True)
class ChipStyle:
    light: Colours
    dark: Colours


def _filled(light_bg: str, light_fg: str, dark_bg: str, dark_fg: str) -> ChipStyle:
    return ChipStyle(Colours(light_bg, light_fg, light_bg), Colours(dark_bg, dark_fg, dark_bg))


def _outlined(light_border: str, light_fg: str, dark_border: str, dark_fg: str) -> ChipStyle:
    return ChipStyle(
        Colours("#ffffff", light_fg, light_border), Colours(DARK_PAGE, dark_fg, dark_border)
    )


TONES: tuple[str, ...] = ("good", "neutral", "watch", "alert")

CHIP_STYLES: dict[str, ChipStyle] = {
    # The four tones.
    "good": _filled("#ecfdf5", "#047857", "#0f2a22", "#6ee7b7"),
    "neutral": _filled("#f4f4f5", "#52525b", "#232327", "#c9c9cd"),
    "watch": _filled("#fffbeb", "#b45309", "#2d2410", "#fcd34d"),
    "alert": _filled("#fef2f2", "#b91c1c", "#331616", "#fca5a5"),
    # Travel: adapted (travel) and the travelling banner.
    "info": _filled("#eff6ff", "#1d4ed8", "#14213d", "#93c5fd"),
    # Brand orange: the quality intents, "Today's pick", "Recommended".
    "accent": _filled("#ea6d1f", "#ffffff", "#fb923c", "#0a0b0d"),
    "neutral-outline": _outlined("#d4d4d8", "#52525b", "#3f3f46", "#c9c9cd"),
    "muted-outline": _outlined("#e4e4e7", "#71717a", "#3f3f46", "#a1a1aa"),
    # Session intents other than the quality ones.
    "intent-strength": _outlined("#fed7aa", "#9a3412", "#7c2d12", "#fdba74"),
    "intent-aerobic_base": _outlined("#bfdbfe", "#1d4ed8", "#1e3a8a", "#93c5fd"),
    "intent-recovery": _outlined("#a7f3d0", "#047857", "#065f46", "#6ee7b7"),
    "intent-skill": _outlined("#ddd6fe", "#6d28d9", "#4c1d95", "#c4b5fd"),
}


@dataclass(frozen=True)
class Chip:
    """A label and the ``CHIP_STYLES`` entry it renders in."""

    label: str
    style: str

    @property
    def css(self) -> str:
        return f"chip c-{self.style}"


def tone_chip(tone: str, label: str) -> Chip:
    """A chip in an LLM-picked tone; an unknown tone renders neutral."""
    return Chip(label, tone if tone in TONES else "neutral")


def _heading(label: str) -> str:
    return label[0].upper() + label[1:]


_STATUS_STYLE: dict[str, str] = {
    "done": "good",
    "substituted": "good",
    "adapted_travel": "info",
    "excused_ill": "neutral",
    "excused_travel": "neutral",
    "not_done_travelling": "neutral-outline",
    "not_done": "watch",
}

SESSION_STATUS_CHIP: dict[str, Chip] = {
    **{
        status: Chip(_heading(STATUS_LABEL[status]), style)
        for status, style in _STATUS_STYLE.items()
    },
    # An Extra activity is not a session status; it is listed apart and never counted.
    "extra": Chip("Extra", "muted-outline"),
}
"""Session status → chip, with the glossary label from ``STATUS_LABEL``."""

QUALITY_INTENTS: frozenset[str] = frozenset({"threshold", "intervals"})
"""The quality intents: solid orange chip, and their day is outlined on the week strip."""

INTENT_CHIP: dict[str, Chip] = {
    intent: Chip(label, "accent" if intent in QUALITY_INTENTS else f"intent-{intent}")
    for intent, label in INTENT_LABEL.items()
}


def intent_chip(intent: str | None) -> Chip | None:
    """The Session intent's chip, or None: no intent (or an unknown one) has no chip."""
    return INTENT_CHIP.get(intent) if intent else None


LIFE_CONTEXT_STYLE: dict[str, str] = {
    DayEffect.SICK: "alert",
    DayEffect.MILD: "watch",
    DayEffect.TRAVEL: "info",
    DayEffect.TRAVEL_NO_TRAINING: "neutral",
    "ramp_back": "good",
}
"""Life context state (the day's ``DayEffect``, or a ramp-back day) → banner/chip style."""

JINJA_GLOBALS = {
    "chip_styles": CHIP_STYLES,
    "session_status_chip": SESSION_STATUS_CHIP,
    "intent_chip": intent_chip,
    "tone_chip": tone_chip,
    "life_context_style": LIFE_CONTEXT_STYLE,
    "quality_intents": QUALITY_INTENTS,
}
"""What every email template can read."""
