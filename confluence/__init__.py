"""Confluence research domain package.

Public re-exports for the most commonly referenced types.
"""

from confluence.models.extraction import ContextExtractionOutput, ContextSearchResult
from confluence.models.intent import QueryIntent
from confluence.models.page import ConfluencePage, PageSection
from confluence.models.reference import ConfluenceReference
from confluence.models.response import ConfluenceContextResponse

__all__ = [
    "ConfluenceContextResponse",
    "ConfluencePage",
    "ConfluenceReference",
    "ContextExtractionOutput",
    "ContextSearchResult",
    "PageSection",
    "QueryIntent",
]
