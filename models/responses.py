"""Response models for the four MCP tools (design doc: models/responses.py).

QueryResponse is fully used in Milestone 1. BriefingResponse, ReportResponse, and
JiraContextResponse are the declared schemas of the stub tools; they gain fields when
Milestones 2 and 3 implement those tools.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    applied_conventions: list[AppliedConvention] = Field(default_factory=list)
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal problems; a degraded answer is still returned (Rule 5: "
        "partial failure is a designed state).",
    )


class BriefingResponse(BaseModel):
    """Placeholder schema for generate_briefing (implemented in Milestone 3)."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    view_url: str | None = None


class ReportResponse(BaseModel):
    """Placeholder schema for get_report (implemented in Milestone 3)."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    view_url: str | None = None


class JiraContextResponse(BaseModel):
    """Placeholder schema for get_jira_context (implemented after Milestone 1)."""

    model_config = ConfigDict(frozen=True)

    projects: list[str] = Field(default_factory=list)
    fields: list[JiraField] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)
    issue_types: list[str] = Field(default_factory=list)
