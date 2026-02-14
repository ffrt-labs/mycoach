"""Garmin authentication via garth token management."""

import logging
from pathlib import Path

import garth

from mycoach.config import get_settings

logger = logging.getLogger(__name__)


class GarminAuth:
    """Handles Garmin Connect authentication and token persistence via garth."""

    def __init__(self, email: str = "", password: str = "", token_dir: Path | None = None) -> None:
        settings = get_settings()
        self.email = email or settings.garmin_email
        self.password = password or settings.garmin_password
        self.token_dir = token_dir or settings.garmin_token_dir

    def login(self) -> bool:
        """Authenticate with Garmin Connect.

        Tries to resume from saved tokens first. Falls back to email/password login.

        Returns:
            True if authentication succeeds, False otherwise.
        """
        # Try resuming from saved tokens
        if self.token_dir.exists():
            try:
                garth.resume(str(self.token_dir))
                # Validate tokens are still good
                _ = garth.client.username
                logger.info("Resumed Garmin session from saved tokens")
                return True
            except Exception:
                logger.info("Saved tokens expired or invalid, re-authenticating")

        # Fall back to email/password
        if not self.email or not self.password:
            logger.error("No Garmin credentials configured")
            return False

        try:
            garth.login(self.email, self.password)
            self._save_tokens()
            logger.info("Garmin login successful")
            return True
        except Exception:
            logger.exception("Garmin login failed")
            return False

    def _save_tokens(self) -> None:
        """Persist garth tokens to disk for later resumption."""
        self.token_dir.mkdir(parents=True, exist_ok=True)
        garth.save(str(self.token_dir))
        logger.debug("Garmin tokens saved to %s", self.token_dir)
