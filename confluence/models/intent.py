"""QueryIntent - structured output from QueryIntentAnalyser.

Lives here (not in core/) to keep the dependency arrow one-way:
core/ imports from confluence/, never the reverse.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryIntent(BaseModel):
    """Structured intent extracted from a natural-language agenda topic.

    Used by BriefingOrchestrator to drive Phase 2 (Confluence research) and
    to seed the JQL builder in Phase 3.

    intent_type='general' with empty version_refs means Confluence is skipped.
    """

    model_config = ConfigDict(frozen=True)

    version_refs: list[str] = Field(
        default_factory=list,
        description="Release version strings found in the query, e.g. ['ACME_R1.0', 'R1'].",
    )
    project_keys: list[str] = Field(
        default_factory=list,
        description="Jira project keys inferred from the query, e.g. ['PROJ_A', 'CAR'].",
    )
    risk_signals: list[str] = Field(
        default_factory=list,
        description="Risk vocabulary present in the query: blocker, risk, mitigation, etc.",
    )
    confluence_keywords: list[str] = Field(
        default_factory=list,
        description="Terms to use in CQL searches, derived from version refs and risk signals.",
    )
    suggested_spaces: list[str] = Field(
        default_factory=list,
        description="Confluence space keys inferred from version/project patterns. "
        "Falls back to NETRA_CONFLUENCE__DEFAULT_SPACES when empty.",
    )
    intent_type: Literal[
        "release_risk",
        "blocker_analysis",
        "domain_impact",
        "working_group_status",
        "general",
    ] = Field(
        default="general",
        description="Classified intent. 'general' skips Confluence research entirely.",
    )
