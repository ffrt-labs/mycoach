"""Garmin Connect API client wrapper using garminconnect library."""

import contextlib
import fcntl
import logging
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from garminconnect import (  # type: ignore[import-untyped]
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from mycoach.sources.garmin.auth import GarminAuth

logger = logging.getLogger(__name__)

TOKEN_FILE = "garmin_tokens.json"
"""Where garminconnect keeps the session when given a directory as its tokenstore."""

REJECTED_TOKEN_FILE = "garmin_tokens.json.rejected"
"""Where a session Garmin refused is moved, kept for diagnosis rather than deleted."""

LOCK_FILE = ".login.lock"
LOCK_TIMEOUT_SECONDS = 120.0


class GarminLoginBusyError(Exception):
    """Another login held the token lock for longer than we were willing to wait."""


@contextlib.contextmanager
def _token_lock(token_dir: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold an exclusive lock on the token store while logging in or refreshing.

    The scheduled sync, a manual sync and a weight push can each build a client at the
    same time. Each would read the same refresh token and spend it; a server that rotates
    refresh tokens can treat that reuse as theft and revoke the session. ``flock`` locks
    belong to the open file, so this serialises threads of this process as well as other
    processes sharing the volume.

    garminconnect also refreshes inside individual API calls when the token is about to
    expire; that path is the library's and is not covered here. ``login`` already
    refreshes a token within 15 minutes of expiry, which makes the window small.
    """
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECONDS
    token_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(token_dir / LOCK_FILE, "w") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise GarminLoginBusyError(
                        f"another Garmin login held the lock for over {timeout:.0f}s"
                    ) from None
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _is_rejected_session(error: BaseException) -> bool:
    """Whether ``error`` means Garmin refused the saved session, not that it was unreachable.

    garminconnect turns every failure of its post-login profile fetch into the same
    ``GarminConnectAuthenticationError``, network outages included, so the cause chain is
    what tells them apart: a 401 from the API, or the refresh endpoint refusing the token.
    """
    seen: BaseException | None = error
    while seen is not None:
        message = str(seen)
        if isinstance(seen, GarminConnectConnectionError) and "API Error 401" in message:
            return True
        if isinstance(seen, GarminConnectAuthenticationError) and (
            "DI token refresh failed" in message or "No DI refresh token" in message
        ):
            return True
        seen = seen.__cause__
    return False


def _describe(error: BaseException) -> str:
    """A one-line reason for a failed login, for job runs, the dashboard and the alert."""
    message = str(error)
    if isinstance(error, GarminLoginBusyError):
        return message
    if isinstance(error, GarminConnectTooManyRequestsError) or "429" in message:
        return "Garmin is rate limiting logins from this server; this usually clears within hours"
    if "Invalid Username or Password" in message:
        return "Garmin rejected the email or password"
    if _is_rejected_session(error):
        return "Garmin refused the saved session and there was no password login to fall back on"
    cause = f" ({error.__cause__})" if error.__cause__ is not None else ""
    return f"{type(error).__name__}: {message}{cause}"


class GarminClient:
    """Wraps the garminconnect library to fetch health and activity data."""

    def __init__(self, auth: GarminAuth | None = None) -> None:
        self.auth = auth or GarminAuth()
        self._api: Garmin | None = None
        self.last_error: str | None = None
        """Why the last ``connect`` failed, or None if it succeeded."""

    def connect(self) -> bool:
        """Authenticate and initialize the Garmin API client.

        Resumes the saved session from the token store, which garminconnect refreshes
        when it is about to expire. When Garmin refuses that session (the refresh token
        was revoked or expired), garminconnect never falls back to the password by itself:
        it keeps presenting the dead token and every call answers 401. So a refused session
        is moved aside and the login is retried once with email and password, which saves
        a fresh session. Only once: each attempt counts against Garmin's login rate limit.

        Returns:
            True if connection succeeds, False otherwise, with the reason in ``last_error``.
        """
        self.last_error = None
        try:
            with _token_lock(self.auth.token_dir):
                self._api = self._login_with_recovery()
        except Exception as e:
            logger.exception("Failed to initialize Garmin API client")
            self._api = None
            self.last_error = _describe(e)
            return False
        logger.info("Garmin API client connected")
        return True

    def _login(self) -> Garmin:
        api = Garmin(email=self.auth.email, password=self.auth.password)
        api.login(tokenstore=str(self.auth.token_dir))
        return api

    def _login_with_recovery(self) -> Garmin:
        try:
            return self._login()
        except GarminConnectAuthenticationError as e:
            token_file = self.auth.token_dir / TOKEN_FILE
            if not (_is_rejected_session(e) and token_file.exists()):
                raise
            logger.warning(
                "Garmin refused the saved session (%s); moving it to %s and logging in "
                "with email and password",
                e.__cause__ or e,
                REJECTED_TOKEN_FILE,
            )
            token_file.replace(self.auth.token_dir / REJECTED_TOKEN_FILE)
            return self._login()

    @property
    def api(self) -> Garmin:
        if self._api is None:
            raise RuntimeError("GarminClient not connected. Call connect() first.")
        return self._api

    def get_stats(self, day: date) -> dict[str, Any]:
        """Get daily stats (steps, HR, stress, Body Battery, etc.)."""
        return self.api.get_stats(day.isoformat())  # type: ignore[no-any-return]

    def get_heart_rates(self, day: date) -> dict[str, Any]:
        """Get heart rate data for a day."""
        return self.api.get_heart_rates(day.isoformat())  # type: ignore[no-any-return]

    def get_hrv_data(self, day: date) -> dict[str, Any]:
        """Get HRV data for a day."""
        return self.api.get_hrv_data(day.isoformat())  # type: ignore[no-any-return]

    def get_sleep_data(self, day: date) -> dict[str, Any]:
        """Get sleep data for a day."""
        return self.api.get_sleep_data(day.isoformat())  # type: ignore[no-any-return]

    def get_stress_data(self, day: date) -> dict[str, Any]:
        """Get stress data for a day."""
        return self.api.get_stress_data(day.isoformat())  # type: ignore[no-any-return]

    def get_body_battery(self, start: date, end: date) -> list[dict[str, Any]]:
        """Get Body Battery data for a date range."""
        return self.api.get_body_battery(  # type: ignore[no-any-return]
            start.isoformat(), end.isoformat()
        )

    def get_training_readiness(self, day: date) -> dict[str, Any]:
        """Get training readiness score for a day."""
        return self.api.get_training_readiness(day.isoformat())  # type: ignore[no-any-return]

    def get_training_status(self, day: date) -> dict[str, Any]:
        """Get training status for a day."""
        return self.api.get_training_status(day.isoformat())  # type: ignore[no-any-return]

    def get_max_metrics(self, day: date) -> dict[str, Any]:
        """Get max metrics (VO2max, etc.) for a day."""
        return self.api.get_max_metrics(day.isoformat())  # type: ignore[no-any-return]

    def get_respiration_data(self, day: date) -> dict[str, Any]:
        """Get respiration data for a day."""
        return self.api.get_respiration_data(day.isoformat())  # type: ignore[no-any-return]

    def get_spo2_data(self, day: date) -> dict[str, Any]:
        """Get SpO2 data for a day."""
        return self.api.get_spo2_data(day.isoformat())  # type: ignore[no-any-return]

    def get_device_last_used(self) -> dict[str, Any]:
        """Get the last-used device, including when it last uploaded to Garmin.

        This is the only endpoint that distinguishes "the watch has nothing to
        say about today" from "the watch has not spoken to Garmin's servers
        since Tuesday" — the two look identical in a day's health response, and
        only the second is something the user can act on.
        """
        return self.api.get_device_last_used()  # type: ignore[no-any-return]

    def get_activities_by_date(self, start: date, end: date) -> list[dict[str, Any]]:
        """Get activities for a date range."""
        return self.api.get_activities_by_date(  # type: ignore[no-any-return]
            start.isoformat(), end.isoformat()
        )
