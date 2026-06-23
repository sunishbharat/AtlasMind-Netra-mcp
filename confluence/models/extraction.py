"""Models for Confluence context extraction results."""

import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from confluence.models.page import ConfluencePage

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*_?[A-Z0-9]+-\d+$")


def _validate_jira_key(v: object) -> str:
    """Pydantic v2 BeforeValidator for Jira issue key strings."""
    if not isinstance(v, str):
        raise ValueError(f"JiraKey must be a string, got {type(v).__name__}")
    if not _JIRA_KEY_RE.match(v):
        raise ValueError(
            f"'{v}' is not a valid Jira issue key (expected PROJECT-123 format)"
        )
    return v


JiraKey = Annotated[str, BeforeValidator(_validate_jira_key)]


class ContextExtractionOutput(BaseModel):
    """PydanticAI output_type for the LLM extraction step on a Confluence page.

    Produced by ContextExtractor.extract(). jira_keys_mentioned seeds the JQL
    builder; the other fields enrich IssueAnalyser prompts.
    """

    model_config = ConfigDict(frozen=True)

    jira_keys_mentioned: list[str] = Field(
        default_factory=list,
        description="Jira issue keys explicitly referenced in the page content.",
    )
    mitigation_owners: list[str] = Field(
        default_factory=list,
        description="Owner names found in mitigation or action-item context.",
    )
    severity_signals: list[str] = Field(
        default_factory=list,
        description="Risk/severity vocabulary: 'blocked', 'escalated', 'at risk', etc.",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="Extracted action items or 'Action:' table rows from the page.",
    )


class ContextSearchResult(BaseModel):
    """One page's contribution to a Confluence search result.

    Aggregates ContextExtractionOutput with the source ConfluencePage metadata.
    """

    model_config = ConfigDict(frozen=True)

    page: ConfluencePage
    jira_keys_mentioned: list[str] = Field(default_factory=list)
    extracted_mitigations: list[str] = Field(default_factory=list)
    extracted_owners: list[str] = Field(default_factory=list)
