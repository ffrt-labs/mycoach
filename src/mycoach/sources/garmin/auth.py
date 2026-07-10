"""Garmin Connect credentials and token cache location."""

from pathlib import Path

from mycoach.config import get_settings


class GarminAuth:
    """Holds Garmin Connect credentials and the token cache directory.

    Session resume, login fallback, and token persistence are all handled
    natively by garminconnect's Garmin.login(tokenstore=...) - see GarminClient.
    """

    def __init__(self, email: str = "", password: str = "", token_dir: Path | None = None) -> None:
        settings = get_settings()
        self.email = email or settings.garmin_email
        self.password = password or settings.garmin_password
        self.token_dir = token_dir or settings.garmin_token_dir
