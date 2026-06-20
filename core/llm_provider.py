"""LLM provider classes for PydanticAI agent model resolution.

Each supported backend has a dedicated subclass of LLMProvider. To add a new
provider: subclass LLMProvider, implement make_model() and validate_credentials(),
then add a branch in create_llm_provider().

Pattern mirrors the AtlasMind backend client design
adapted for PydanticAI model resolution instead of direct API calls.
"""

import os
from abc import ABC, abstractmethod

import structlog
from pydantic_ai.models import Model

from config.settings import LLMSettings

logger = structlog.get_logger(__name__)


class LLMProviderError(Exception):
    """Raised when a required credential or configuration is absent."""


class LLMProvider(ABC):
    """Abstract base for resolving a PydanticAI model for Netra-mcp agents.

    Subclass for each LLM backend. All Netra-mcp agents (Clarifier,
    AgendaDecomposer, IssueAnalyser) accept a PydanticAI model string or Model
    object - this class provides that via make_model(). validate_credentials()
    can be called at startup to surface missing API keys early.
    """

    @abstractmethod
    def make_model(self) -> "Model | str":
        """Return the PydanticAI model string or Model object for this provider."""

    @abstractmethod
    def validate_credentials(self) -> None:
        """Raise LLMProviderError if required credentials are absent."""


class GroqLLMProvider(LLMProvider):
    """Groq provider via PydanticAI. Reads GROQ_API_KEY from the environment."""

    def __init__(self, model_str: str) -> None:
        self._model_str = model_str

    def make_model(self) -> str:
        return self._model_str

    def validate_credentials(self) -> None:
        if not os.environ.get("GROQ_API_KEY"):
            raise LLMProviderError(
                f"GROQ_API_KEY is not set (required for model '{self._model_str}')"
            )


class AnthropicLLMProvider(LLMProvider):
    """Anthropic provider via PydanticAI. Reads ANTHROPIC_API_KEY from the environment."""

    def __init__(self, model_str: str) -> None:
        self._model_str = model_str

    def make_model(self) -> str:
        return self._model_str

    def validate_credentials(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMProviderError(
                f"ANTHROPIC_API_KEY is not set (required for model '{self._model_str}')"
            )


class OpenAICompatibleLLMProvider(LLMProvider):
    """OpenAI-compatible provider with a custom base URL (e.g. Minimax, NVIDIA NIM).

    NETRA_LLM__BASE_URL must be set. NETRA_LLM__API_KEY or OPENAI_API_KEY supplies
    the bearer token; either is accepted.
    """

    def __init__(self, model_name: str, base_url: str, api_key: str | None) -> None:
        self._model_name = model_name
        self._base_url = base_url
        self._api_key = api_key

    def make_model(self) -> Model:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(base_url=self._base_url, api_key=self._api_key)
        return OpenAIChatModel(model_name=self._model_name, provider=provider)

    def validate_credentials(self) -> None:
        if not self._api_key and not os.environ.get("OPENAI_API_KEY"):
            raise LLMProviderError(
                f"No API key for OpenAI-compatible provider '{self._model_name}': "
                "set NETRA_LLM__API_KEY or OPENAI_API_KEY"
            )


class BedrockLLMProvider(LLMProvider):
    """AWS Bedrock provider via PydanticAI's BedrockConverseModel.

    Credentials are read from the standard boto3 credential chain:
    AWS_BEARER_TOKEN_BEDROCK (bearer token, same as the AtlasMind backend),
    AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, ~/.aws/credentials, or an IAM role.
    Region is read from AWS_DEFAULT_REGION; override with NETRA_LLM__BASE_URL for
    a custom Bedrock endpoint URL.

    Model string format: bedrock:<bedrock-model-id>
    Example: bedrock:anthropic.claude-sonnet-4-5
    """

    def __init__(self, model_name: str, endpoint_url: str | None) -> None:
        self._model_name = model_name
        self._endpoint_url = endpoint_url

    def make_model(self) -> Model:
        from pydantic_ai.models.bedrock import BedrockConverseModel
        from pydantic_ai.providers.bedrock import BedrockProvider

        provider = BedrockProvider(base_url=self._endpoint_url)
        return BedrockConverseModel(model_name=self._model_name, provider=provider)

    def validate_credentials(self) -> None:
        has_bearer = bool(os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
        has_key_pair = bool(
            os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        if not has_bearer and not has_key_pair:
            # IAM roles and instance profiles are a valid and common deployment pattern;
            # boto3 discovers them automatically but we cannot verify them here without
            # an actual API call. Log at DEBUG so operators can confirm intent without
            # blocking startup for role-based deployments.
            logger.debug(
                "bedrock_no_explicit_credentials",
                model=self._model_name,
                note="no AWS_BEARER_TOKEN_BEDROCK or key pair found; "
                "relying on IAM role / instance profile / ~/.aws/credentials",
            )


class GoogleGeminiLLMProvider(LLMProvider):
    """Google Gemini provider via PydanticAI's google (AI Studio) support.

    Reads GOOGLE_API_KEY from the environment. Use model strings of the form
    'google:gemini-2.0-flash' or 'google-vertex:<model>' for Vertex AI.
    Free-tier models: gemini-2.0-flash, gemini-1.5-flash.
    """

    def __init__(self, model_str: str) -> None:
        self._model_str = model_str

    def make_model(self) -> str:
        return self._model_str

    def validate_credentials(self) -> None:
        if not os.environ.get("GOOGLE_API_KEY"):
            raise LLMProviderError(
                f"GOOGLE_API_KEY is not set (required for model '{self._model_str}')"
            )


class _UnknownLLMProvider(LLMProvider):
    """Passthrough for unrecognised model string prefixes.

    The string is forwarded to PydanticAI unchanged; it resolves the provider at
    agent run time. No credential check is performed.
    """

    def __init__(self, model_str: str) -> None:
        self._model_str = model_str

    def make_model(self) -> str:
        return self._model_str

    def validate_credentials(self) -> None:
        logger.debug("unknown_llm_provider_passthrough", model_str=self._model_str)


def create_llm_provider(settings: LLMSettings) -> LLMProvider:
    """Factory: inspect the model string prefix and return the matching LLMProvider.

    Routing:
      groq:*          -> GroqLLMProvider            (GROQ_API_KEY)
      anthropic:*     -> AnthropicLLMProvider       (ANTHROPIC_API_KEY)
      openai:*        -> OpenAICompatibleLLMProvider (NETRA_LLM__BASE_URL + API key)
      bedrock:*       -> BedrockLLMProvider         (AWS credential chain / bearer token)
      google:*        -> GoogleGeminiLLMProvider    (GOOGLE_API_KEY, AI Studio free tier)
      google-vertex:* -> GoogleGeminiLLMProvider    (GOOGLE_API_KEY or ADC, Vertex AI)
      <other>         -> _UnknownLLMProvider        (passthrough, no credential check)
    """
    model_str = settings.model

    if ":" not in model_str:
        return _UnknownLLMProvider(model_str)

    prefix, model_name = model_str.split(":", 1)

    if prefix == "groq":
        return GroqLLMProvider(model_str)
    if prefix == "anthropic":
        return AnthropicLLMProvider(model_str)
    if prefix == "openai":
        base_url = settings.base_url or "https://api.openai.com/v1"
        api_key = settings.api_key.get_secret_value() if settings.api_key else None
        return OpenAICompatibleLLMProvider(
            model_name=model_name, base_url=base_url, api_key=api_key
        )
    if prefix == "bedrock":
        return BedrockLLMProvider(model_name=model_name, endpoint_url=settings.base_url or None)
    if prefix in ("google", "google-vertex"):
        return GoogleGeminiLLMProvider(model_str)

    return _UnknownLLMProvider(model_str)
