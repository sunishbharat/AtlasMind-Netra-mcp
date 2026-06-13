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


# ---------------------------------------------------------------------------
# POST /issue_details models (Milestone 2 - proposed to backend team)
# ---------------------------------------------------------------------------


class IssueComment(BaseModel):
    """One comment on a Jira issue."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    author: str
    body: str
    created: str
    updated: str


class IssueLink(BaseModel):
    """One issue link (blocks, is blocked by, relates to, etc.)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str
    direction: Literal["outward", "inward"]
    linked_issue_key: str
    linked_issue_summary: str | None = None


class ChangelogEntry(BaseModel):
    """One status-field transition from the Jira changelog.

    The backend filters to status transitions only; `field` is always "status" in practice.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    field: str
    from_value: str | None = None
    to_value: str
    author: str
    timestamp: str


class IssueDetail(BaseModel):
    """Per-issue content returned by POST /issue_details.

    `summary` is not in the original contract proposal but is included here because the
    IssueAnalyser prompt requires it for meaningful output. It defaults to None so that
    backend responses omitting it degrade gracefully. Flag this when proposing to the backend.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    key: str
    summary: str | None = None
    priority: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    fix_versions: list[str] = Field(default_factory=list)
    flagged: bool = False
    comments: list[IssueComment] = Field(default_factory=list)
    links: list[IssueLink] = Field(default_factory=list)
    changelog: list[ChangelogEntry] = Field(default_factory=list)


class IssueDetailsRequest(BaseModel):
    """POST /issue_details request body. Max 50 keys per request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_keys: list[str]
    request_id: str | None = None
    comments_limit: int | None = Field(default=None, ge=1)


class IssueDetailsResponse(BaseModel):
    """POST /issue_details response envelope.

    Always HTTP 200; in-band errors arrive in the `error` field.
    Issues not found or inaccessible are listed in `not_found` (not an error).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    issues: list[IssueDetail] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    error: str | None = None
