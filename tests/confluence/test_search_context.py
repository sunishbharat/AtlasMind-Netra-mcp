"""Tests for BriefingOrchestrator.search_context - page_urls merge + force_refresh."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from confluence.models.extraction import ContextExtractionOutput
from confluence.models.page import ConfluencePage
from confluence.models.response import ConfluenceContextResponse


def _page(page_id: str, title: str = "Page") -> ConfluencePage:
    return ConfluencePage(
        page_id=page_id,
        title=title,
        space_key="PROJ",
        url=f"https://confluence.example.com/wiki/spaces/PROJ/pages/{page_id}/Title",
        last_modified="2026-06-20T10:00:00.000Z",
        cql_excerpt="",
    )


def _pinned_url(page_id: str) -> str:
    return f"https://confluence.example.com/wiki/spaces/PROJ/pages/{page_id}/Title"


def _extraction(keys: list[str] | None = None) -> ContextExtractionOutput:
    return ContextExtractionOutput(
        jira_keys_mentioned=keys or [],
        mitigation_owners=[],
        severity_signals=[],
        action_items=[],
    )


def _make_orchestrator(
    cql_pages: list[ConfluencePage],
    pinned_page_map: dict[str, ConfluencePage] | None = None,
) -> Any:
    """Build a BriefingOrchestrator with all dependencies stubbed."""
    from unittest.mock import patch

    from core.briefing_orchestrator import BriefingOrchestrator

    confluence = MagicMock()
    extractor = MagicMock()

    # CQL search returns cql_pages.
    confluence.search_pages_multi = AsyncMock(return_value=cql_pages)

    # fetch_page_metadata returns from pinned_page_map keyed by page_id.
    async def fake_fetch_metadata(
        page_id: str, source_url: str, force_refresh: bool = False
    ) -> ConfluencePage:
        if pinned_page_map and page_id in pinned_page_map:
            return pinned_page_map[page_id]
        raise RuntimeError(f"unexpected fetch_page_metadata call for {page_id}")

    confluence.fetch_page_metadata = fake_fetch_metadata

    # get_page_sections returns empty dict (sections aren't the focus here).
    confluence.get_page_sections = AsyncMock(return_value={})

    # extract returns a simple extraction output.
    extractor.extract = AsyncMock(return_value=_extraction(["CAR-101"]))

    # Intent analyser returns a generic intent.
    from confluence.models.intent import QueryIntent

    intent_analyser = MagicMock()
    intent_analyser.analyse = AsyncMock(
        return_value=QueryIntent(
            version_refs=[],
            confluence_keywords=["blocker"],
            intent_type="release_risk",
        )
    )

    settings = MagicMock()
    settings.confluence.default_spaces = ["PROJ"]
    settings.confluence.recency_days = 30
    settings.confluence.confluence_concurrency = 5

    with patch.object(BriefingOrchestrator, "__init__", lambda self, **kw: None):
        orch = BriefingOrchestrator.__new__(BriefingOrchestrator)

    orch._confluence = confluence
    orch._context_extractor = extractor
    orch._intent_analyser = intent_analyser
    orch._settings = settings
    orch._confluence_sem = asyncio.Semaphore(5)

    return orch


class TestSearchContextPageUrls:
    @pytest.mark.asyncio
    async def test_pinned_pages_appear_before_cql_results(self) -> None:
        """page_urls pages appear first in results; CQL pages follow (deduped by page_id)."""
        cql_page = _page("222222", "CQL Result")
        pinned_page = _page("111111", "Pinned Page")
        orch = _make_orchestrator(
            cql_pages=[cql_page],
            pinned_page_map={"111111": pinned_page},
        )

        result: ConfluenceContextResponse = await orch.search_context(
            query="release risk",
            page_urls=[_pinned_url("111111")],
            limit=5,
        )

        assert len(result.results) == 2
        assert result.results[0].page.page_id == "111111"
        assert result.results[1].page.page_id == "222222"

    @pytest.mark.asyncio
    async def test_unrecognised_url_added_to_errors_and_skipped(self) -> None:
        """display-style URLs cannot be resolved; they go to errors, not results."""
        orch = _make_orchestrator(cql_pages=[])

        result = await orch.search_context(
            query="risk",
            page_urls=["https://confluence.example.com/display/PROJ/SomePage"],
            limit=5,
        )

        assert any("cannot extract page ID" in e for e in result.errors)
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_limit_cap_displaces_cql_when_pinned_fills_budget(self) -> None:
        """When pinned pages fill limit, no CQL results are included and intent is not called."""
        cql_pages = [_page(f"90000{i}") for i in range(3)]
        pinned_ids = ["100001", "100002", "100003"]
        pinned_pages_map = {pid: _page(pid, f"Pinned {pid}") for pid in pinned_ids}

        orch = _make_orchestrator(
            cql_pages=cql_pages,
            pinned_page_map=pinned_pages_map,
        )
        pinned_urls = [_pinned_url(pid) for pid in pinned_ids]

        result = await orch.search_context(
            query="risk",
            page_urls=pinned_urls,
            limit=3,
        )

        # All 3 slots filled by pinned pages; CQL results excluded.
        assert len(result.results) == 3
        returned_ids = {r.page.page_id for r in result.results}
        assert returned_ids == set(pinned_ids)
        # Intent analysis + CQL search must be skipped entirely when budget is full.
        orch._intent_analyser.analyse.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_pinned_urls_deduped_by_page_id(self) -> None:
        """The same URL given twice in page_urls must appear once in results."""
        pid = "444444"
        pinned = _page(pid, "Pinned Page")

        orch = _make_orchestrator(
            cql_pages=[],
            pinned_page_map={pid: pinned},
        )

        result = await orch.search_context(
            query="risk",
            page_urls=[_pinned_url(pid), _pinned_url(pid)],
            limit=5,
        )

        # Both URLs resolve to the same page_id; only one result expected.
        assert len(result.results) == 1
        assert result.results[0].page.page_id == pid

    @pytest.mark.asyncio
    async def test_pinned_page_deduped_when_also_in_cql(self) -> None:
        """A page in both page_urls and CQL results appears once (pinned copy kept)."""
        shared_id = "333333"
        shared_cql = _page(shared_id, "Shared CQL Page")
        pinned_version = _page(shared_id, "Pinned Version")

        orch = _make_orchestrator(
            cql_pages=[shared_cql],
            pinned_page_map={shared_id: pinned_version},
        )

        result = await orch.search_context(
            query="risk",
            page_urls=[_pinned_url(shared_id)],
            limit=5,
        )

        assert len(result.results) == 1
        assert result.results[0].page.title == "Pinned Version"
