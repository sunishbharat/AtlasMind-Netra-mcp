"""Application settings - the single source of configuration.

Every constant, default, URL, timeout, and path lives here. Each field is overridable via
environment variable with the ``NETRA_`` prefix and ``__`` as nested delimiter, e.g.
``NETRA_LITE__BASE_URL``. A local ``.env`` file is supported for development; see
``.env.example`` for the complete list.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

INSTANCE_DEFAULT_PROJECT = "_default"
"""Conventions-store project key used when no Jira project scope is known (fallback level)."""

LITE_ERROR_PREFIX = "Error: "
"""In-band error marker in the backend's answer field (docs/atlasmind_lite_api_contract.md)."""


class LLMSettings(BaseModel):
    """Clarifier LLM configuration (PydanticAI)."""

    model: str = Field(
        default="groq:llama-3.3-70b-versatile",
        description="PydanticAI model string, e.g. 'groq:llama-3.3-70b-versatile', "
        "'anthropic:claude-sonnet-4-6', or 'openai:minimax-m2.7' (for OpenAI-compatible "
        "backends). The provider API key is read from the environment by PydanticAI "
        "itself and is never stored in settings. For OpenAI-compatible providers, set "
        "base_url below to override the default OpenAI endpoint.",
    )
    base_url: str | None = Field(
        default=None,
        description="Custom base URL for OpenAI-compatible and Bedrock providers. "
        "For openai: models, overrides the default OpenAI endpoint "
        "(e.g. 'https://integrate.api.nvidia.com/v1' for NVIDIA NIM). "
        "For bedrock: models, sets a custom Bedrock endpoint URL. "
        "Set NETRA_LLM__BASE_URL to override.",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="API key for OpenAI-compatible providers (e.g. NVIDIA NIM nvapi-... key). "
        "Set NETRA_LLM__API_KEY. Only used when model starts with 'openai:'.",
    )
    retries: int = Field(
        default=2, description="PydanticAI retries when the model output fails validation."
    )


class LiteSettings(BaseModel):
    """atlasMind backend connection (docs/atlasmind_lite_api_contract.md)."""

    base_url: str = Field(
        default="http://localhost:8000", description="Backend base URL, no trailing slash."
    )
    query_path: str = Field(default="/query", description="Query endpoint path.")
    health_path: str = Field(default="/health", description="Health endpoint path.")
    timeout_seconds: float = Field(
        default=300.0,
        description="HTTP timeout for backend calls. Generous: the backend runs an LLM "
        "pipeline with its own retry loop before answering.",
    )
    max_retries: int = Field(
        default=3,
        description="Client retry attempts for transport errors and HTTP 503 only; "
        "4xx and in-band 'Error: ...' answers are never retried.",
    )
    retry_initial_seconds: float = Field(
        default=0.5, description="Initial backoff for the retry loop."
    )
    retry_max_seconds: float = Field(default=8.0, description="Backoff cap for the retry loop.")
    default_limit: int | None = Field(
        default=50,
        description="Default max issues requested per query when the caller passes no limit. "
        "The backend additionally enforces its own MAX_JIRA_RESULTS cap.",
    )
    issue_details_path: str = Field(
        default="/issue_details", description="Issue details endpoint path."
    )
    comments_limit_default: int = Field(
        default=20,
        description="Default max comments per issue for /issue_details requests.",
    )


class FrontendSettings(BaseModel):
    """AtlasMind-frontendUI bridge server (docs/frontendui_bridge_contract.md)."""

    base_url: str = Field(
        default="http://localhost:8001", description="Bridge server base URL (main.py)."
    )
    inject_path: str = Field(
        default="/api/mcp/inject", description="Endpoint that pushes a query into the live UI."
    )
    timeout_seconds: float = Field(
        default=10.0, description="HTTP timeout for bridge calls (inject is fire-and-forget)."
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Bridge API key, forwarded as X-API-Key when set. Never logged.",
    )


class SessionSettings(BaseModel):
    """Clarification session store (Phase 1: in-process dict with TTL)."""

    ttl_seconds: float = Field(
        default=1800.0, description="Idle lifetime of a clarification session."
    )


class ClarificationSettings(BaseModel):
    """Clarification engine knowledge sources and limits."""

    vocab_path: Path = Field(
        default=Path("config/clarification_vocab.json"),
        description="Disambiguation vocabulary (ambiguous term -> questions + jql_patterns).",
    )
    prompt_path: Path = Field(
        default=Path("prompts/clarification_prompt.md"),
        description="System prompt for the clarifier LLM.",
    )
    conventions_path: Path = Field(
        default=Path("data/conventions.json"),
        description="Persisted learned team conventions (Phase 1: JSON file).",
    )
    jira_fields_path: Path | None = Field(
        default=None,
        description="Path to the backend's cached jira_fields.json; unset = degrade gracefully.",
    )
    allowed_values_path: Path | None = Field(
        default=None,
        description="Path to the backend's cached jira_allowed_values.json; optional.",
    )
    max_rounds: int = Field(
        default=3,
        description="Max clarification rounds per session before dispatching with a warning.",
    )
    max_fields_in_prompt: int = Field(
        default=30, description="Cap on Jira field entries injected into the clarifier prompt."
    )


class AnalysisSettings(BaseModel):
    """IssueAnalyser, RankingEngine, and AgendaDecomposer configuration."""

    prompt_path: Path = Field(
        default=Path("prompts/issue_analysis_prompt.md"),
        description="System prompt for the issue analyser LLM.",
    )
    agenda_prompt_path: Path = Field(
        default=Path("prompts/agenda_decomposition_prompt.md"),
        description="System prompt for the agenda decomposer LLM.",
    )
    max_concurrency: int = Field(
        default=2,
        description=(
            "Max concurrent per-issue LLM calls in IssueAnalyser. "
            "Keep at 2 for Groq free tier or Anthropic Tier-1 (burst limits); "
            "raise to 5+ for paid/higher-tier accounts or self-hosted models. "
            "Override with NETRA_ANALYSIS__MAX_CONCURRENCY."
        ),
    )
    blocked_statuses: list[str] = Field(
        default_factory=lambda: ["Blocked", "Stalled", "On Hold", "Waiting"],
        description="Status names that count as blocked for days_blocked computation "
        "(case-insensitive). Override to match your Jira workflow.",
    )
    ranking_rule_path: Path = Field(
        default=Path("config/ranking_default.json"),
        description="Path to the ranking weights JSON file.",
    )


class BriefingSettings(BaseModel):
    """generate_briefing pipeline configuration."""

    top_n: int = Field(
        default=5,
        description="Top N ranked issues per briefing section.",
    )
    issues_per_topic: int = Field(
        default=50,
        description="Max issues fetched per topic query (limit forwarded to the backend).",
    )
    max_topics: int = Field(
        default=10,
        description="Max agenda topics AgendaDecomposer will extract from one agenda.",
    )
    max_analysed_issues: int = Field(
        default=30,
        description=(
            "Hard cap on the total number of issues sent to IssueAnalyser per briefing. "
            "Applied after cross-topic deduplication. Prevents timeouts on large result sets. "
            "Issues are taken in order (topic_1 first, then topic_2, etc.). "
            "Override with NETRA_BRIEFING__MAX_ANALYSED_ISSUES."
        ),
    )
    view_url_base: str | None = Field(
        default=None,
        description="Base URL for briefing views in AtlasMind-frontendUI (M4). "
        "e.g. 'http://localhost:3000/briefing' -> view_url = '{base}/{report_id}'.",
    )


class DeliverySettings(BaseModel):
    """Report delivery settings."""

    enabled: bool = Field(
        default=True,
        description="Write a human-verifiable markdown report for every dispatched query.",
    )
    channel: Literal["markdown_file"] = Field(
        default="markdown_file",
        description="Delivery channel; supported values: markdown_file.",
    )
    output_dir: Path = Field(
        default=Path("data/reports"), description="Folder for markdown report files."
    )
    max_issues: int = Field(
        default=50, description="Cap on issue rows embedded in one report file."
    )


class ServerSettings(BaseModel):
    """MCP transport configuration."""

    transport: Literal["stdio", "streamable-http"] = Field(
        default="stdio", description="stdio for local development, streamable-http in production."
    )
    host: str = Field(default="127.0.0.1", description="Bind host for streamable-http.")
    port: int = Field(default=8765, description="Bind port for streamable-http.")


class LogSettings(BaseModel):
    """structlog configuration."""

    level: str = Field(default="INFO", description="Root log level.")
    json_logs: bool = Field(
        default=False, description="Emit JSON log lines (production) instead of console format."
    )
    log_file: Path | None = Field(
        default=Path("data/logs/netra.log"),
        description="Append JSON log lines to this file. Set to empty string to disable.",
    )


class Settings(BaseSettings):
    """Root settings object. Instantiate once in the composition root (server.py)."""

    model_config = SettingsConfigDict(
        env_prefix="NETRA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    lite: LiteSettings = Field(default_factory=LiteSettings)
    frontend: FrontendSettings = Field(default_factory=FrontendSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    clarification: ClarificationSettings = Field(default_factory=ClarificationSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    briefing: BriefingSettings = Field(default_factory=BriefingSettings)
    delivery: DeliverySettings = Field(default_factory=DeliverySettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    log: LogSettings = Field(default_factory=LogSettings)
