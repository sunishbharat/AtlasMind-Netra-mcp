"""LLMProvider factory and credential validation (core/llm_provider.py)."""

import pytest

from config.settings import LLMSettings
from core.llm_provider import (
    AnthropicLLMProvider,
    BedrockLLMProvider,
    GroqLLMProvider,
    LLMProviderError,
    OpenAICompatibleLLMProvider,
    _UnknownLLMProvider,
    create_llm_provider,
)

# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------


def test_factory_groq_prefix() -> None:
    assert isinstance(
        create_llm_provider(LLMSettings(model="groq:llama-3.3-70b-versatile")),
        GroqLLMProvider,
    )


def test_factory_anthropic_prefix() -> None:
    assert isinstance(
        create_llm_provider(LLMSettings(model="anthropic:claude-sonnet-4-6")),
        AnthropicLLMProvider,
    )


def test_factory_openai_prefix() -> None:
    settings = LLMSettings(model="openai:minimax-m2.7", base_url="http://example.com/v1")
    assert isinstance(create_llm_provider(settings), OpenAICompatibleLLMProvider)


def test_factory_bedrock_prefix() -> None:
    assert isinstance(
        create_llm_provider(LLMSettings(model="bedrock:anthropic.claude-sonnet-4-5")),
        BedrockLLMProvider,
    )


def test_factory_unknown_prefix_returns_passthrough() -> None:
    assert isinstance(
        create_llm_provider(LLMSettings(model="ollama:phi4")),
        _UnknownLLMProvider,
    )


def test_factory_no_colon_returns_passthrough() -> None:
    assert isinstance(
        create_llm_provider(LLMSettings(model="plain-model-name")),
        _UnknownLLMProvider,
    )


# ---------------------------------------------------------------------------
# make_model() for string-returning providers
# ---------------------------------------------------------------------------


def test_groq_make_model_returns_model_string() -> None:
    assert (
        GroqLLMProvider("groq:llama-3.3-70b-versatile").make_model()
        == "groq:llama-3.3-70b-versatile"
    )


def test_anthropic_make_model_returns_model_string() -> None:
    assert (
        AnthropicLLMProvider("anthropic:claude-sonnet-4-6").make_model()
        == "anthropic:claude-sonnet-4-6"
    )


def test_unknown_make_model_returns_model_string() -> None:
    assert _UnknownLLMProvider("some-string").make_model() == "some-string"


# ---------------------------------------------------------------------------
# validate_credentials() - Groq
# ---------------------------------------------------------------------------


def test_groq_validate_raises_when_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMProviderError, match="GROQ_API_KEY"):
        GroqLLMProvider("groq:llama-3.3-70b-versatile").validate_credentials()


def test_groq_validate_passes_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    GroqLLMProvider("groq:llama-3.3-70b-versatile").validate_credentials()


# ---------------------------------------------------------------------------
# validate_credentials() - Anthropic
# ---------------------------------------------------------------------------


def test_anthropic_validate_raises_when_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicLLMProvider("anthropic:claude-sonnet-4-6").validate_credentials()


def test_anthropic_validate_passes_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    AnthropicLLMProvider("anthropic:claude-sonnet-4-6").validate_credentials()


# ---------------------------------------------------------------------------
# validate_credentials() - OpenAI-compatible
# ---------------------------------------------------------------------------


def test_openai_compatible_validate_raises_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMProviderError, match="API key"):
        OpenAICompatibleLLMProvider(
            "minimax-m2.7", "http://example.com/v1", api_key=None
        ).validate_credentials()


def test_openai_compatible_validate_passes_with_explicit_key() -> None:
    OpenAICompatibleLLMProvider(
        "minimax-m2.7", "http://example.com/v1", api_key="nvapi-test"
    ).validate_credentials()


def test_openai_compatible_validate_passes_with_openai_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    OpenAICompatibleLLMProvider(
        "minimax-m2.7", "http://example.com/v1", api_key=None
    ).validate_credentials()


# ---------------------------------------------------------------------------
# validate_credentials() - Bedrock
# ---------------------------------------------------------------------------


def test_bedrock_validate_logs_debug_when_no_explicit_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    # Should NOT raise - IAM roles are a valid alternative that cannot be checked here.
    BedrockLLMProvider("anthropic.claude-sonnet-4-5", None).validate_credentials()


def test_bedrock_validate_passes_with_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "token")
    BedrockLLMProvider("anthropic.claude-sonnet-4-5", None).validate_credentials()


def test_bedrock_validate_passes_with_key_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    BedrockLLMProvider("anthropic.claude-sonnet-4-5", None).validate_credentials()


# ---------------------------------------------------------------------------
# validate_credentials() - Unknown passthrough
# ---------------------------------------------------------------------------


def test_unknown_provider_validate_never_raises() -> None:
    _UnknownLLMProvider("whatever:model").validate_credentials()
