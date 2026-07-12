import pytest

from mycoach.config import Settings


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
