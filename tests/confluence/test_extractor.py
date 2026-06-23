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


class TestContextExtractorForceRefresh:
    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache_read_but_writes_back(self) -> None:
        """force_refresh=True must bypass both fast-path and post-lock re-check reads,
        but always write back so subsequent non-refresh calls serve fresh data.

        Step sequence:
        1. Pre-populate cache with stale output A.
        2. force_refresh=True -> call_count==1, cache holds fresh output B.
        3. force_refresh=True again -> call_count==2 (post-lock guard also bypassed).
        4. No force_refresh -> call_count still 2 (write-back is warm).
        """
        call_count = 0

        stale_output = ContextExtractionOutput(
            jira_keys_mentioned=["OLD-1"],
            mitigation_owners=[],
            severity_signals=[],
            action_items=[],
        )

        def make_fresh_output(n: int) -> ContextExtractionOutput:
            return ContextExtractionOutput(
                jira_keys_mentioned=[f"NEW-{n}"],
                mitigation_owners=[],
                severity_signals=[],
                action_items=[],
            )

        async def run_mock(prompt: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.output = make_fresh_output(call_count)
            return result

        agent = MagicMock(spec=Agent)
        agent.run = run_mock
        extractor = ContextExtractor(agent=agent)

        page = _sample_page()
        cache_key = (page.page_id, page.last_modified)
        extractor._cache[cache_key] = stale_output  # pre-populate with stale

        sections = {"At Risk": "content"}

        # Step 2: force_refresh bypasses stale cache.
        result = await extractor.extract(page, sections, force_refresh=True)
        assert call_count == 1
        assert result.jira_keys_mentioned == ["NEW-1"]
        assert extractor._cache[cache_key].jira_keys_mentioned == ["NEW-1"]

        # Step 3: second force_refresh bypasses the freshly-written cache too.
        result2 = await extractor.extract(page, sections, force_refresh=True)
        assert call_count == 2
        assert result2.jira_keys_mentioned == ["NEW-2"]

        # Step 4: no force_refresh -> serves from cache, no new LLM call.
        result3 = await extractor.extract(page, sections, force_refresh=False)
        assert call_count == 2
        assert result3.jira_keys_mentioned == ["NEW-2"]


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
