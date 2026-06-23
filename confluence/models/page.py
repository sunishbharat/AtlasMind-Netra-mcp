"""Confluence page models."""

from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, computed_field

ConfluencePageId = NewType("ConfluencePageId", str)


class PageSection(BaseModel):
    """One heading-delimited section of a Confluence page after HTML stripping.

    char_count is a computed field - always consistent with content by construction.
    """

    model_config = ConfigDict(frozen=True)

    heading: str
    content: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.content)


class ConfluencePage(BaseModel):
    """Metadata for a Confluence page returned by a CQL search."""

    model_config = ConfigDict(frozen=True)

    page_id: str
    title: str
    space_key: str
    url: str
    last_modified: str = Field(
        description="ISO 8601 timestamp from the CQL result, e.g. '2026-06-20T10:00:00.000Z'."
    )
    cql_excerpt: str = Field(
        default="",
        description="Short excerpt from the CQL search result snippet.",
    )
