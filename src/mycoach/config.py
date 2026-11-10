import logging
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_CACHE_TTL = 300


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
    timezone: str = "Europe/London"
    app_base_url: str = "http://localhost:8000"  # links back into emails (e.g. dashboard CTA)

    # The logger PWA's own origin once split out (e.g. https://logger.example.com).
    # Empty means it's still same-origin under /logger: no CORS is opened and
    # GET /logger keeps serving the shell directly instead of redirecting.
    logger_origin: str = ""

    # Security
    encryption_key: str = ""  # Fernet key for encrypting credentials at rest

    # Database
    db_url: str = "sqlite+aiosqlite:///./mycoach.db"
    # Migration logins on Postgres (ticket #184): each history runs as its schema's owner
    # role, never as the DML-only runtime login in ``db_url``. Empty falls back to
    # ``db_url`` (SQLite, and the one-off cutover run as the instance's superuser).
    health_owner_db_url: str = ""
    # The health module's own runtime login (ticket #186): ``health_role`` only. ``db_url`` is
    # then the coaching login (``coaching_role`` only). Empty falls back to ``db_url``, which is
    # SQLite, and the interim single bridge login until the cutover (docs/migrations/186).
    health_db_url: str = ""
    # A gym-sourced canonical activity settles on a Garmin match or this many hours after its
    # start, whichever comes first (map #85, ticket #112).
    activity_settlement_deadline_hours: int = 24
    coaching_owner_db_url: str = ""

    # Garmin
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_token_dir: Path = Field(default=Path(".garmin_tokens"))

    # Gym workouts arrive via Hevy CSV import or the offline companion logger
    # (POST /api/sources/import/workouts).

    # LLM Provider (anthropic, claude_code)
    llm_provider: str = "anthropic"

    # Anthropic Claude API
    claude_api_key: str = ""
    claude_model_daily: str = "claude-sonnet-4-5-20250929"
    claude_model_weekly: str = "claude-opus-4-6"
    claude_monthly_cost_ceiling: float = 30.0

    # Claude Code CLI, billed against a Claude Pro subscription. The token is
    # the long-lived one from `claude setup-token`, under the CLI's own variable
    # name rather than a MYCOACH_ one. Model aliases, not dated IDs, so a
    # retired model ID can't break generation.
    claude_code_oauth_token: str = Field(
        default="",
        validation_alias=AliasChoices("claude_code_oauth_token", "CLAUDE_CODE_OAUTH_TOKEN"),
    )
    claude_code_model_daily: str = "sonnet"
    claude_code_model_weekly: str = "sonnet"

    # Langfuse tracing of coaching runs, under the SDK's own variable names.
    # On only when all three are set: the SDK's default base URL is Langfuse
    # Cloud, so keys without a base URL must never enable it. The base URL is
    # a deploy *variable*, the keys are secrets.
    langfuse_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("langfuse_base_url", "LANGFUSE_BASE_URL"),
    )
    langfuse_public_key: str = Field(
        default="",
        validation_alias=AliasChoices("langfuse_public_key", "LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("langfuse_secret_key", "LANGFUSE_SECRET_KEY"),
    )
    # How long a coaching prompt fetched from Langfuse is served before it is
    # fetched again, in seconds (a failed refresh keeps the cached version).
    langfuse_prompt_cache_ttl: int = Field(
        default=DEFAULT_PROMPT_CACHE_TTL,
        ge=0,
        validation_alias=AliasChoices("langfuse_prompt_cache_ttl", "LANGFUSE_PROMPT_CACHE_TTL"),
    )

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
    # Garmin uploads can land a day or more after the fact, so each sync
    # re-fetches a window rather than only the newest days. import_health_snapshot
    # fills nulls on re-fetch, so a wider window is pure recovery.
    scheduler_sync_lookback_days: int = 7
    scheduler_briefing_hour: int = 9
    scheduler_briefing_minute: int = 30
    # The briefing's own pre-generation sync, which runs on every tick of the
    # 15-minute retry poll rather than once a day. Deliberately far narrower
    # than scheduler_sync_lookback_days above: recovering a five-day-old late
    # upload is the 06:00 sync's job, and re-asking for it forty times a day
    # only risks throttling the account the poll depends on.
    scheduler_briefing_sync_lookback_days: int = 2
    scheduler_post_workout_hour: int = 7
    scheduler_post_workout_minute: int = 0
    scheduler_weekly_plan_day: str = "sun"
    scheduler_weekly_plan_hour: int = 18
    scheduler_weekly_plan_minute: int = 0
    scheduler_weekly_recap_day: str = "mon"
    scheduler_weekly_recap_hour: int = 7
    scheduler_weekly_recap_minute: int = 0

    @field_validator("langfuse_prompt_cache_ttl", mode="before")
    @classmethod
    def _empty_ttl_is_default(cls, value: object) -> object:
        # The deploy writes LANGFUSE_PROMPT_CACHE_TTL even when the GitHub
        # variable is unset, so an empty value must mean the default.
        if isinstance(value, str) and not value.strip():
            return DEFAULT_PROMPT_CACHE_TTL
        return value

    @model_validator(mode="after")
    def _default_scheduler_timezone(self) -> "Settings":
        if not self.scheduler_timezone:
            self.scheduler_timezone = self.timezone
        return self

    @model_validator(mode="after")
    def _require_llm_provider_key(self) -> "Settings":
        provider = self.llm_provider.lower()
        required_keys = {
            "anthropic": ("MYCOACH_CLAUDE_API_KEY", self.claude_api_key),
            "claude_code": ("CLAUDE_CODE_OAUTH_TOKEN", self.claude_code_oauth_token),
        }
        if provider not in required_keys:
            raise ValueError(
                f"MYCOACH_LLM_PROVIDER={self.llm_provider!r} is not a recognized provider; "
                f"expected one of {sorted(required_keys)}."
            )
        key_env, key_value = required_keys[provider]
        if not key_value:
            raise ValueError(f"MYCOACH_LLM_PROVIDER={provider} but {key_env} is not set.")
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
