"""Response models for the four MCP tools.

QueryResponse is the schema for query_jira results. BriefingResponse, ReportResponse,
and JiraContextResponse are the schemas for the briefing tools.

Citation, IssueAnalysisSuggestions, and BlockerAnalysis are the internal analysis output
models used by IssueAnalyser and RankingEngine.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from confluence.models.reference import ConfluenceReference
from models.jira import JiraField
from models.lite import ChartSpec


class AppliedConvention(BaseModel):
    """One term interpretation applied to a query; listed so wrong assumptions are visible."""

    model_config = ConfigDict(frozen=True)

    term: str
    jql_hint: str
    source: Literal["convention", "clarification"] = Field(
        description="'convention' = previously learned; 'clarification' = answered this session."
    )


class QueryResponse(BaseModel):
    """Result of the query_jira tool.

    When `requires_user_input` is true, the host MUST present `clarification_question` to
    the user verbatim and call query_jira again with their answer (host-relay guard,
    design doc: clarification engine).
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    requires_user_input: bool = False
    clarification_question: str | None = None
    answer: str | None = None
    jql: str | None = None
    total: int = 0
    shown: int = 0
    display_fields: list[str] = Field(default_factory=list)
    # Any: issue dicts are backend-defined passthrough; the MCP host renders them.
    issues: list[dict[str, Any]] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    ui_injected: bool = Field(
        default=False,
        description="True when show_in_ui was requested and the result was pushed to the "
        "live AtlasMind browser UI for rendering.",
    )
    report_path: str | None = Field(
        default=None,
        description="Location of the human-verifiable markdown report written for this "
        "query (None when delivery is disabled or failed).",
    )
    view_url: str | None = Field(
        default=None,
        description="URL to view the report in a browser. Only set when "
        "NETRA_SERVER__PUBLIC_URL is configured.",
    )
    applied_conventions: list[AppliedConvention] = Field(default_factory=list)
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal problems; a degraded answer is still returned (Rule 5: "
        "partial failure is a designed state).",
    )
    data_scope: str = Field(
        default=(
            "issue metadata only (key, summary, status, priority, assignee, dates). "
            "Comment text, issue links, and changelog are not available via query_jira."
        ),
        description=(
            "Machine-readable signal of what data is present in this response. "
            "Always 'issue metadata only ...' for query_jira; generate_briefing "
            "(M3) will carry a different value when comment-grounded analysis is available."
        ),
    )


class BriefingResponse(BaseModel):
    """Result of the generate_briefing tool.

    When `requires_user_input` is true the host MUST present `clarification_question`
    verbatim for the pending topic and call generate_briefing again with the answer.
    `pending_topic_id` identifies which topic triggered the question.
    """

    model_config = ConfigDict(frozen=True)

    report_id: str
    session_id: str
    requires_user_input: bool = False
    clarification_question: str | None = None
    pending_topic_id: str | None = None
    sections: list["BriefingSection"] = Field(default_factory=list)
    report_path: str | None = Field(
        default=None,
        description="Path to the human-verifiable markdown report written for this briefing.",
    )
    view_url: str | None = Field(
        default=None,
        description="URL to the rendered briefing in AtlasMind-frontendUI (M4, when configured).",
    )
    errors: list[str] = Field(default_factory=list)
    data_scope: str = Field(
        default=(
            "issue content (comments, links, changelog) from /issue_details, "
            "analysed and ranked per agenda topic."
        ),
        description="Machine-readable signal of what data is present in this response.",
    )


class ReportResponse(BaseModel):
    """Result of the get_report tool - structured briefing data + view_url."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    sections: list["BriefingSection"] = Field(default_factory=list)
    report_path: str | None = None
    view_url: str | None = None
    errors: list[str] = Field(default_factory=list)
    data_scope: str = Field(
        default=(
            "issue content (comments, links, changelog) from /issue_details, "
            "analysed and ranked per agenda topic."
        ),
        description="Machine-readable signal of what data is present in this response.",
    )


class JiraContextResponse(BaseModel):
    """Schema for get_jira_context."""

    model_config = ConfigDict(frozen=True)

    projects: list[str] = Field(default_factory=list)
    fields: list[JiraField] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)
    issue_types: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Issue analysis output models
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A factual claim's source: the Jira issue (and optionally a specific comment)."""

    model_config = ConfigDict(frozen=True)

    issue_key: str
    comment_id: str | None = None


class IssueAnalysisSuggestions(BaseModel):
    """The three AI-generated fields only.

    This is the PydanticAI output_type for the IssueAnalyser agent. Fact fields
    (blocked_reason, days_blocked, owner, dependent_issues) are computed in pure Python
    before and after the LLM call - they are never generated.
    """

    model_config = ConfigDict(frozen=True)

    suggested_resolution: str
    mitigation: str
    risk_note: str
    evidence: list[Citation] = Field(default_factory=list)


class BlockerAnalysis(BaseModel):
    """Per-issue analysis output from IssueAnalyser, scored by RankingEngine.

    Fields marked FACT are derived from Jira data (computations in pure Python).
    Fields marked AI SUGGESTION are LLM-generated and are structurally separated so
    callers and renderers can label them accordingly (trust rules, design doc).
    """

    model_config = ConfigDict(frozen=True)

    issue_key: str
    summary: str

    # FACT fields - computed from Jira data, never generated
    blocked_reason: str
    days_blocked: int
    owner: str
    priority: str | None = None
    dependent_issues: list[str] = Field(default_factory=list)
    due_date: str | None = None
    flagged: bool = False

    # AI SUGGESTION fields - generated, must be visually separated in the UI
    suggested_resolution: str
    mitigation: str
    risk_note: str
    evidence: list[Citation] = Field(default_factory=list)

    # Set by RankingEngine via model_copy(update={"score": ...})
    score: float = 0.0

    # Confluence pages that mention this issue (structural fact, not AI suggestion).
    # Populated only when NETRA_CONFLUENCE__BASE_URL is configured.
    confluence_refs: list[ConfluenceReference] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Briefing pipeline models
# ---------------------------------------------------------------------------


class AgendaTopic(BaseModel):
    """One data question extracted from an agenda item by AgendaDecomposer."""

    model_config = ConfigDict(frozen=True)

    topic_id: str
    description: str
    suggested_query: str
    projects: list[str] = Field(default_factory=list)


class AgendaDecomposition(BaseModel):
    """PydanticAI output type for AgendaDecomposer: structured list of agenda topics."""

    model_config = ConfigDict(frozen=True)

    topics: list[AgendaTopic]


class BriefingSection(BaseModel):
    """One section of a briefing report: one agenda topic mapped to ranked issues."""

    model_config = ConfigDict(frozen=True)

    topic_id: str
    description: str
    query_used: str | None = None
    jql: str | None = None
    top_issues: list[BlockerAnalysis] = Field(default_factory=list)
    total_found: int = 0
    errors: list[str] = Field(default_factory=list)


# Resolve forward references in BriefingResponse and ReportResponse.
BriefingResponse.model_rebuild()
ReportResponse.model_rebuild()
