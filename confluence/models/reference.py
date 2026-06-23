"""ConfluenceReference - a resolved link from a Jira issue to a Confluence page."""

from pydantic import BaseModel, ConfigDict, Field


class ConfluenceReference(BaseModel):
    """A Confluence page that mentions (or is mentioned by) a specific Jira issue.

    Structural fact stored on BlockerAnalysis; not an AI suggestion.
    relevant_passage is empty string when populated via reverse lookup (no extraction ran).
    """

    model_config = ConfigDict(frozen=True)

    page_id: str
    page_title: str
    cql_excerpt: str = Field(
        default="",
        description="Short CQL result snippet that matched this page.",
    )
    relevant_passage: str = Field(
        default="",
        description="Extracted passage from the page that specifically mentions this issue key. "
        "Empty when the reference was found via reverse lookup only.",
    )
