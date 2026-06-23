"""Response model for the search_context MCP tool."""

from pydantic import BaseModel, ConfigDict, Field

from confluence.models.extraction import ContextSearchResult


class ConfluenceContextResponse(BaseModel):
    """Return type of the search_context MCP tool.

    Returns empty results (no error) when Confluence is not configured,
    so callers never need to special-case the unconfigured state.
    """

    model_config = ConfigDict(frozen=True)

    results: list[ContextSearchResult] = Field(default_factory=list)
    total_pages_found: int = 0
    cql_used: list[str] = Field(
        default_factory=list,
        description="All CQL variant strings used in the search.",
    )
    errors: list[str] = Field(default_factory=list)
