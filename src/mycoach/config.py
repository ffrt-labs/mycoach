from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MYCOACH_",
        case_sensitive=False,
    )

    # App
    env: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_token: str = ""

    # Database
    db_url: str = "sqlite+aiosqlite:///./mycoach.db"

    # Garmin
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_token_dir: Path = Field(default=Path(".garmin_tokens"))

    # Claude API
    claude_api_key: str = ""
    claude_model_daily: str = "claude-sonnet-4-5-20250929"
    claude_model_weekly: str = "claude-opus-4-6"
    claude_monthly_cost_ceiling: float = 30.0

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
    scheduler_timezone: str = "Europe/London"
    scheduler_sync_hour: int = 6
    scheduler_sync_minute: int = 0
    scheduler_briefing_hour: int = 6
    scheduler_briefing_minute: int = 30
    scheduler_weekly_plan_day: str = "sun"
    scheduler_weekly_plan_hour: int = 18


def get_settings() -> Settings:
    return Settings()
