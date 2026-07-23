import logging
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MYCOACH_",
        case_sensitive=False,
        # Ignore env vars that are no longer mapped to a setting (e.g. the
        # retired Hevy web-API credentials) so stale .env files don't break boot.
        extra="ignore",
    )

    # App
    env: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_token: str = ""
    timezone: str = "Europe/London"

    # Security
    encryption_key: str = ""  # Fernet key for encrypting credentials at rest

    # Database
    db_url: str = "sqlite+aiosqlite:///./mycoach.db"

    # Garmin
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_token_dir: Path = Field(default=Path(".garmin_tokens"))

    # Gym workouts arrive via Hevy CSV import or the offline companion logger
    # (POST /api/sources/import/workouts, authenticated with api_token above).

    # LLM Provider (anthropic, gemini)
    llm_provider: str = "anthropic"

    # Anthropic Claude API
    claude_api_key: str = ""
    claude_model_daily: str = "claude-sonnet-4-5-20250929"
    claude_model_weekly: str = "claude-opus-4-6"
    claude_monthly_cost_ceiling: float = 30.0

    # Google Gemini API
    gemini_api_key: str = ""
    gemini_model_daily: str = "gemini-2.5-flash"
    gemini_model_weekly: str = "gemini-2.5-pro"

    # Email
    email_enabled: bool = False
    email_from: str = ""
    email_to: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_resend_api_key: str = ""

    # Scheduler
    scheduler_timezone: str = ""
    scheduler_sync_hour: int = 6
    scheduler_sync_minute: int = 0
    scheduler_briefing_hour: int = 6
    scheduler_briefing_minute: int = 30
    scheduler_post_workout_hour: int = 7
    scheduler_post_workout_minute: int = 0
    scheduler_weekly_plan_day: str = "sun"
    scheduler_weekly_plan_hour: int = 18
    scheduler_weekly_plan_minute: int = 0
    scheduler_weekly_recap_day: str = "mon"
    scheduler_weekly_recap_hour: int = 7
    scheduler_weekly_recap_minute: int = 0

    @model_validator(mode="after")
    def _default_scheduler_timezone(self) -> "Settings":
        if not self.scheduler_timezone:
            self.scheduler_timezone = self.timezone
        return self

    @model_validator(mode="after")
    def _require_llm_provider_key(self) -> "Settings":
        provider = self.llm_provider.lower()
        if provider == "anthropic" and not self.claude_api_key:
            raise ValueError(
                "MYCOACH_LLM_PROVIDER=anthropic but MYCOACH_CLAUDE_API_KEY is not set."
            )
        if provider == "gemini" and not self.gemini_api_key:
            raise ValueError(
                "MYCOACH_LLM_PROVIDER=gemini but MYCOACH_GEMINI_API_KEY is not set."
            )
        return self

    @model_validator(mode="after")
    def _require_email_config_when_enabled(self) -> "Settings":
        """Refuse to boot with email switched on but unable to actually send.

        The global email flag short-circuits every per-user preference check, so
        an enabled-but-unsendable configuration is silently mute in production.
        Fail fast instead: demand a resolvable backend and a recipient.
        """
        if not self.email_enabled:
            return self
        if not (self.email_resend_api_key or self.email_smtp_host):
            raise ValueError(
                "MYCOACH_EMAIL_ENABLED is true but no email backend is configured: "
                "set MYCOACH_EMAIL_RESEND_API_KEY or MYCOACH_EMAIL_SMTP_HOST."
            )
        if not self.email_to:
            raise ValueError(
                "MYCOACH_EMAIL_ENABLED is true but no recipient is configured: "
                "set MYCOACH_EMAIL_TO."
            )
        return self


def announce_email_config(settings: Settings) -> None:
    """Announce email delivery status loudly at startup.

    A fully-configured, enabled setup is validated at construction time. When
    email is switched off that is a legitimate choice, but it must be surfaced
    prominently rather than silently assumed — this is the class of mistake that
    left the app mute for its entire history.
    """
    if not settings.email_enabled:
        logger.warning(
            "EMAIL IS DISABLED (MYCOACH_EMAIL_ENABLED is false) — "
            "no coaching emails will be sent."
        )


def get_settings() -> Settings:
    return Settings()
