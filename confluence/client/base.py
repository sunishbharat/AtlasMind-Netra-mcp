"""ConfluenceClientProtocol - structural interface for Confluence access.

BriefingOrchestrator holds ConfluenceClientProtocol | None so tests can
supply a stub dataclass without any patching. Consistent with the Protocol
already used in core/issue_analyser.py (IssueAnalyserPort).

Imports QueryIntent from confluence.models.intent (not from core/) to keep
the dependency arrow one-way: core/ imports from confluence/, never the reverse.
"""

from typing import Protocol, runtime_checkable

from confluence.models.intent import QueryIntent
from confluence.models.page import ConfluencePage


@runtime_checkable
class ConfluenceClientProtocol(Protocol):
    """Structural interface for Confluence page search and content fetching."""

    async def search_pages_multi(
        self,
        intent: QueryIntent,
        spaces: list[str],
        recency_days: int = 30,
    ) -> list[ConfluencePage]:
        """Run 3 CQL variants in parallel and return deduplicated pages."""
        ...

    async def get_page_sections(
        self,
        page_id: str,
        target_headings: list[str],
    ) -> dict[str, str]:
        """Fetch page HTML and return extracted plain-text sections by heading."""
        ...

    async def find_pages_mentioning_keys(
        self,
        issue_keys: list[str],
        spaces: list[str],
    ) -> dict[str, list[ConfluencePage]]:
        """Reverse lookup: find pages that mention any of the given issue keys.

        Returns a dict mapping each issue_key to the pages that mention it.
        """
        ...
