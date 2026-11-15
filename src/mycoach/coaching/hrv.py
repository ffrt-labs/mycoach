"""HRV as both the prompt and the email write it (#197): one formatting, two renderings.

Garmin's personal baseline band (``hrv_baseline_low``/``hrv_baseline_high``) is the
reference the 7-day average is read against; before Garmin has one there is no band.
"""

from typing import Any


def format_ms(value: Any) -> str:
    """A millisecond value without a spurious ``.0`` (52.0 -> "52", 61.5 -> "61.5")."""
    return f"{float(value):g}"


def hrv_status_word(status: Any) -> str:
    """Garmin's status code as a word: BALANCED -> Balanced, LOW_STRESS -> Low stress."""
    return str(status).replace("_", " ").capitalize()


def hrv_band(snapshot: dict[str, Any]) -> tuple[Any, Any] | None:
    """``(low, high)`` of Garmin's baseline band for the day, or None while it is building."""
    low, high = snapshot.get("hrv_baseline_low"), snapshot.get("hrv_baseline_high")
    if low is None or high is None:
        return None
    return low, high


def hrv_band_text(snapshot: dict[str, Any]) -> str | None:
    """``"47–58"``, or None without a band."""
    found = hrv_band(snapshot)
    return None if found is None else f"{format_ms(found[0])}–{format_ms(found[1])}"
