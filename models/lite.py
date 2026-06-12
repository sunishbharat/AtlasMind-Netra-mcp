"""Typed models for the atlasMind backend HTTP API.

Mirrors docs/atlasmind_lite_api_contract.md (consumer view of the backend-owned contract).
Response models use extra="ignore": the contract allows the backend to add fields in minor
versions without a contract bump.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LiteQueryRequest(BaseModel):
    """POST /query request body (backend `QueryRequest`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    request_id: str | None = None
    limit: int | None = Field(default=None, ge=1)


class ChartSpec(BaseModel):
    """Chart specification, passed through untouched to AtlasMind-frontendUI."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str
    x_field: str | None = None
    y_field: str | None = None
    title: str | None = None
    color_field: str | None = None


class ServerMeta(BaseModel):
    """Backend metadata (GET /meta shape, also embedded in QueryResponse.meta)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    model_name: str | None = None
    llm_backend: str | None = None
    llm_timeout: int | None = None


class TokenUsage(BaseModel):
    """Token count breakdown reported by the backend for one request."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    system_tokens: int = 0
    fields_tokens: int = 0
    examples_tokens: int = 0
    total_tokens: int = 0
    retry_tokens: int = 0


class LiteQueryResult(BaseModel):
    """POST /query response body (backend `QueryResponse`).

    Always delivered with HTTP 200; errors are in-band via `answer` starting "Error: "
    (detected by `AtlasMindLiteClient`, raised as `LiteBackendError`).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: Literal["jql", "general", "changelog"] = "general"
    profile: str | None = None
    jira_base_url: str | None = None
    jira_type: Literal["cloud", "server"] | None = None
    answer: str | None = None
    jql: str | None = None
    total: int = 0
    shown: int = 0
    examined: int = 0
    display_fields: list[str] = Field(default_factory=list)
    # Any: issue dicts are backend-defined and passed through to the MCP host untouched;
    # Netra-mcp does not depend on individual issue keys (contract "Issue Object").
    issues: list[dict[str, Any]] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    filters: dict[str, list[str]] | None = None
    meta: ServerMeta | None = None
    token_usage: TokenUsage | None = None
