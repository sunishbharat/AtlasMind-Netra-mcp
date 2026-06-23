"""Tests for ContextExtractor - LRU cache + double-checked lock."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent

from confluence.extraction.extractor import ContextExtractor
from confluence.models.extraction import ContextExtractionOutput
from confluence.models.page import ConfluencePage


def _sample_page(page_id: str = "42") -> ConfluencePage:
    return ConfluencePage(
        page_id=page_id,
        title="Test Page",
        space_key="PROJ",
        url="/pages/42",
        last_modified="2026-06-20T10:00:00.000Z",
        cql_excerpt="test excerpt",
    )


def _sample_output() -> ContextExtractionOutput:
    return ContextExtractionOutput(
        jira_keys_mentioned=["CAR-101"],
        mitigation_owners=["jdoe"],
        severity_signals=["blocked"],
        action_items=["Escalation call 2026-06-25"],
    )


class TestContextExtractorCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm(self) -> None:
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock(return_value=MagicMock(output=_sample_output()))
        extractor = ContextExtractor(agent=agent)

        page = _sample_page()
        sections = {"At Risk": "CAR-101 is blocked."}

        # First call populates cache.
        result1 = await extractor.extract(page, sections)
        assert agent.run.call_count == 1

        # Second call with same page_id + last_modified should hit cache.
        result2 = await extractor.extract(page, sections)
        assert agent.run.call_count == 1  # still 1
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_different_last_modified_triggers_new_extraction(self) -> None:
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock(return_value=MagicMock(output=_sample_output()))
        extractor = ContextExtractor(agent=agent)

        page_v1 = _sample_page()
        page_v2 = ConfluencePage(
            page_id="42",
            title="Test Page",
            space_key="PROJ",
            url="/pages/42",
            last_modified="2026-06-21T10:00:00.000Z",  # updated
            cql_excerpt="test excerpt",
        )
        sections = {"At Risk": "CAR-101 is blocked."}

        await extractor.extract(page_v1, sections)
        await extractor.extract(page_v2, sections)
        assert agent.run.call_count == 2


class TestContextExtractorConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_same_page_extractions_calls_llm_once(self) -> None:
        """Two concurrent extract() calls for the same (page_id, last_modified)
        must result in exactly 1 agent.run() call, not 2."""
        call_count = 0

        async def slow_run(prompt: str) -> MagicMock:
            nonlocal call_count
            await asyncio.sleep(0)  # yield to allow interleaving
            call_count += 1
            result = MagicMock()
            result.output = _sample_output()
            return result

        agent = MagicMock(spec=Agent)
        agent.run = slow_run
        extractor = ContextExtractor(agent=agent)

        page = _sample_page()
        sections = {"At Risk": "CAR-101 blocked."}

        results = await asyncio.gather(
            extractor.extract(page, sections),
            extractor.extract(page, sections),
        )
        assert call_count == 1
        assert results[0] == results[1]

    @pytest.mark.asyncio
    async def test_different_pages_run_concurrently(self) -> None:
        call_count = 0

        async def run(prompt: str) -> MagicMock:
            nonlocal call_count
            await asyncio.sleep(0)
            call_count += 1
            result = MagicMock()
            result.output = _sample_output()
            return result

        agent = MagicMock(spec=Agent)
        agent.run = run
        extractor = ContextExtractor(agent=agent)

        page_a = _sample_page("1")
        page_b = _sample_page("2")
        sections = {"At Risk": "blocked"}

        await asyncio.gather(
            extractor.extract(page_a, sections),
            extractor.extract(page_b, sections),
        )
        assert call_count == 2  # two different pages - both must run


class TestContextExtractorOutput:
    @pytest.mark.asyncio
    async def test_returns_extraction_output(self) -> None:
        expected = _sample_output()
        agent = MagicMock(spec=Agent)
        agent.run = AsyncMock(return_value=MagicMock(output=expected))
        extractor = ContextExtractor(agent=agent)

        result = await extractor.extract(_sample_page(), {"At Risk": "CAR-101 blocked"})
        assert result.jira_keys_mentioned == ["CAR-101"]
        assert result.mitigation_owners == ["jdoe"]
        assert result.severity_signals == ["blocked"]
