"""How a sport is shown: in the emails and on the dashboard's history page.

One map for every place a sport is named or coloured, keyed by the stored sport
(lower case). A sport not in it falls back to its own name, a neutral chip and gray.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SportDisplay:
    label: str
    """Short display name: "Swim" (the post-workout eyebrow)."""
    name: str
    """Full display name: "Swimming" (session rows: "Swimming · aerobic base")."""
    short: str
    """The weekly plan's week-strip label: "SWIM"."""
    chip: str
    """The email chip style (``tones.CHIP_STYLES``) of the weekly plan's sport chip."""
    colour: str
    """The history page's Tailwind hue: "cyan"."""


SPORTS: dict[str, SportDisplay] = {
    "gym": SportDisplay("Gym", "Gym", "GYM", "warm", "blue"),
    "strength": SportDisplay("Strength", "Strength", "GYM", "warm", "gray"),
    "swimming": SportDisplay("Swim", "Swimming", "SWIM", "info", "cyan"),
    "swim": SportDisplay("Swim", "Swim", "SWIM", "info", "gray"),
    "cycling": SportDisplay("Cycling", "Cycling", "BIKE", "info", "gray"),
    "running": SportDisplay("Run", "Running", "RUN", "good", "gray"),
    "run": SportDisplay("Run", "Run", "RUN", "good", "gray"),
    "padel": SportDisplay("Padel", "Padel", "PADEL", "good", "green"),
    "rest": SportDisplay("Rest", "Rest", "REST", "neutral", "gray"),
    "cardio": SportDisplay("Cardio", "Cardio", "CARDIO", "neutral", "red"),
}


def sport_display(sport: str | None) -> SportDisplay:
    """The sport's display, or one made from its own name for a sport not in ``SPORTS``."""
    key = (sport or "").lower()
    known = SPORTS.get(key)
    if known is not None:
        return known
    return SportDisplay(
        label=key.replace("_", " ").capitalize(),
        name=key.capitalize(),
        short=key.upper(),
        chip="neutral",
        colour="gray",
    )
