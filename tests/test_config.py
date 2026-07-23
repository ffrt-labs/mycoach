import logging

import pytest

from mycoach.config import Settings, announce_email_config


def _settings(**overrides: object) -> Settings:
    """Build Settings with a valid LLM config, so email-focused tests don't trip
    the provider-key validator (or pick up values from a local .env file)."""
    defaults: dict[str, object] = {
        "llm_provider": "gemini",
        "claude_api_key": "",
        "gemini_api_key": "test-key",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_anthropic_provider_requires_claude_key() -> None:
    with pytest.raises(ValueError, match="MYCOACH_CLAUDE_API_KEY"):
        Settings(llm_provider="anthropic", claude_api_key="", gemini_api_key="")


def test_gemini_provider_requires_gemini_key() -> None:
    with pytest.raises(ValueError, match="MYCOACH_GEMINI_API_KEY"):
        Settings(llm_provider="gemini", claude_api_key="", gemini_api_key="")


def test_anthropic_provider_with_key_is_valid() -> None:
    settings = Settings(llm_provider="anthropic", claude_api_key="sk-test", gemini_api_key="")
    assert settings.llm_provider == "anthropic"


def test_gemini_provider_with_key_is_valid() -> None:
    settings = Settings(llm_provider="gemini", claude_api_key="", gemini_api_key="test-key")
    assert settings.llm_provider == "gemini"


# --- Email startup validation ---------------------------------------------


def test_email_enabled_without_backend_fails() -> None:
    with pytest.raises(ValueError, match="MYCOACH_EMAIL_RESEND_API_KEY.*MYCOACH_EMAIL_SMTP_HOST"):
        _settings(
            email_enabled=True,
            email_to="me@example.com",
            email_resend_api_key="",
            email_smtp_host="",
        )


def test_email_enabled_without_recipient_fails() -> None:
    with pytest.raises(ValueError, match="MYCOACH_EMAIL_TO"):
        _settings(email_enabled=True, email_to="", email_resend_api_key="re_test")


def test_email_enabled_with_resend_backend_is_valid() -> None:
    settings = _settings(
        email_enabled=True,
        email_to="me@example.com",
        email_resend_api_key="re_test",
        email_smtp_host="",
    )
    assert settings.email_enabled is True


def test_email_enabled_with_smtp_backend_is_valid() -> None:
    settings = _settings(
        email_enabled=True,
        email_to="me@example.com",
        email_resend_api_key="",
        email_smtp_host="smtp.example.com",
    )
    assert settings.email_enabled is True


def test_email_disabled_is_valid_without_backend() -> None:
    settings = _settings(
        email_enabled=False,
        email_to="",
        email_resend_api_key="",
        email_smtp_host="",
    )
    assert settings.email_enabled is False


def test_announce_email_config_warns_when_disabled(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(email_enabled=False, email_to="", email_resend_api_key="")
    with caplog.at_level(logging.WARNING, logger="mycoach.config"):
        announce_email_config(settings)
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert "no" in caplog.text.lower() and "email" in caplog.text.lower()


def test_announce_email_config_silent_when_enabled(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(
        email_enabled=True,
        email_to="me@example.com",
        email_resend_api_key="re_test",
    )
    with caplog.at_level(logging.WARNING, logger="mycoach.config"):
        announce_email_config(settings)
    assert not any(record.levelno == logging.WARNING for record in caplog.records)
