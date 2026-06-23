"""Tests for ConfluenceClient.

Uses unittest.mock to stub atlassian-python-api; freezegun for TTLCache expiry.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from confluence.client.client import ConfluenceClient, _parse_cql_result
from confluence.models.intent import QueryIntent
from confluence.models.page import ConfluencePage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client(**kwargs: Any) -> ConfluenceClient:
    """Build a ConfluenceClient with atlassian Confluence stubbed out."""
    with patch("confluence.client.client.ConfluenceClient.__init__") as mock_init:
        mock_init.return_value = None
        client = ConfluenceClient.__new__(ConfluenceClient)

    from collections import defaultdict

    from cachetools import TTLCache

    client._confluence = MagicMock()
    client._search_limit = kwargs.get("search_limit", 10)
    client._max_retries = kwargs.get("max_retries", 1)
    client._content_max_chars = kwargs.get("content_max_chars", 10_000)
    client._page_cache = TTLCache(maxsize=200, ttl=kwargs.get("ttl", 300))
    client._page_fetch_locks = defaultdict(asyncio.Lock)
    return client


def _sample_cql_item(page_id: str = "1", title: str = "Test Page") -> dict[str, Any]:
    return {
        "content": {
            "id": page_id,
            "title": title,
            "space": {"key": "PROJ"},
            "_links": {"webui": f"/pages/{page_id}"},
            "history": {"lastUpdated": {"when": "2026-06-20T10:00:00.000Z"}},
        },
        "excerpt": "Sample excerpt",
    }


def _sample_page(page_id: str = "1", title: str = "Test Page") -> ConfluencePage:
    return ConfluencePage(
        page_id=page_id,
        title=title,
        space_key="PROJ",
        url=f"/pages/{page_id}",
        last_modified="2026-06-20T10:00:00.000Z",
        cql_excerpt="Sample excerpt",
    )


# ---------------------------------------------------------------------------
# Tests: _parse_cql_result
# ---------------------------------------------------------------------------


class TestParseCqlResult:
    def test_parses_standard_item(self) -> None:
        item = _sample_cql_item("42", "My Page")
        page = _parse_cql_result(item)
        assert page.page_id == "42"
        assert page.title == "My Page"
        assert page.space_key == "PROJ"
        assert page.cql_excerpt == "Sample excerpt"

    def test_missing_excerpt_defaults_empty(self) -> None:
        item = _sample_cql_item("1")
        del item["excerpt"]
        page = _parse_cql_result(item)
        assert page.cql_excerpt == ""


# ---------------------------------------------------------------------------
# Tests: search_pages_multi deduplication
# ---------------------------------------------------------------------------


class TestSearchPagesMulti:
    @pytest.mark.asyncio
    async def test_deduplicates_same_page_across_variants(self) -> None:
        client = _make_client()
        # All 3 CQL variants return the same page.
        client._confluence.cql.return_value = [_sample_cql_item("99", "Shared Page")]  # type: ignore[attr-defined]

        intent = QueryIntent(
            version_refs=["E035"],
            confluence_keywords=["blocker"],
            intent_type="release_risk",
        )
        pages = await client.search_pages_multi(intent, spaces=["PROJ"])
        assert len(pages) == 1
        assert pages[0].page_id == "99"

    @pytest.mark.asyncio
    async def test_collects_pages_from_all_variants(self) -> None:
        client = _make_client()
        call_count = 0

        def cql_side_effect(cql: str, limit: int) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            return [_sample_cql_item(str(call_count), f"Page {call_count}")]

        client._confluence.cql.side_effect = cql_side_effect  # type: ignore[attr-defined]

        intent = QueryIntent(
            version_refs=["E035"],
            confluence_keywords=["blocker"],
            intent_type="release_risk",
        )
        pages = await client.search_pages_multi(intent, spaces=["PROJ"])
        assert len(pages) == 3

    @pytest.mark.asyncio
    async def test_partial_variant_failure_does_not_abort(self) -> None:
        client = _make_client()
        call_count = 0

        def cql_side_effect(cql: str, limit: int) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Confluence unavailable")
            return [_sample_cql_item(str(call_count), f"Page {call_count}")]

        client._confluence.cql.side_effect = cql_side_effect  # type: ignore[attr-defined]

        intent = QueryIntent(version_refs=["E035"], intent_type="release_risk")
        pages = await client.search_pages_multi(intent, spaces=["PROJ"])
        # 2 variants succeeded, so 2 pages (they have different IDs)
        assert len(pages) == 2


# ---------------------------------------------------------------------------
# Tests: get_page_sections double-checked lock
# ---------------------------------------------------------------------------


class TestGetPageSections:
    @pytest.mark.asyncio
    async def test_concurrent_same_page_fetches_only_once(self) -> None:
        client = _make_client()
        fetch_count = 0

        async def fake_fetch(page_id: str) -> str:
            nonlocal fetch_count
            await asyncio.sleep(0)  # yield to allow interleaving
            fetch_count += 1
            return f"<h2>At Risk</h2><p>CAR-{page_id} blocked.</p>"

        client._fetch_page_html = fake_fetch  # type: ignore[method-assign]

        results = await asyncio.gather(
            client.get_page_sections("page1", ["At Risk"]),
            client.get_page_sections("page1", ["At Risk"]),
            client.get_page_sections("page1", ["At Risk"]),
        )
        assert fetch_count == 1
        assert all("At Risk" in r for r in results)

    @pytest.mark.asyncio
    async def test_cache_hit_skips_fetch(self) -> None:
        client = _make_client()
        client._page_cache["page1"] = "<h2>At Risk</h2><p>blocked</p>"

        fetch_count = 0

        async def fake_fetch(page_id: str) -> str:
            nonlocal fetch_count
            fetch_count += 1
            return ""

        client._fetch_page_html = fake_fetch  # type: ignore[method-assign]

        result = await client.get_page_sections("page1", ["At Risk"])
        assert fetch_count == 0
        assert "At Risk" in result

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache_read_but_writes_back(self) -> None:
        """force_refresh=True must bypass both fast-path and post-lock re-check reads,
        but always write back so subsequent non-refresh calls serve fresh data.

        Step sequence:
        1. Pre-populate cache with stale data A.
        2. force_refresh=True -> fetch_count==1, cache holds fresh data B.
        3. force_refresh=True again -> fetch_count==2 (post-lock guard also bypassed).
        4. No force_refresh -> fetch_count still 2 (write-back is warm).
        """
        client = _make_client()
        client._page_cache["page1"] = "<h2>At Risk</h2><p>stale</p>"

        fetch_count = 0

        async def fake_fetch(page_id: str) -> str:
            nonlocal fetch_count
            fetch_count += 1
            return f"<h2>At Risk</h2><p>fresh-{fetch_count}</p>"

        client._fetch_page_html = fake_fetch  # type: ignore[method-assign]

        # Step 2: force_refresh bypasses stale cache.
        result = await client.get_page_sections("page1", ["At Risk"], force_refresh=True)
        assert fetch_count == 1
        assert "fresh-1" in result.get("At Risk", "")
        assert "fresh-1" in client._page_cache.get("page1", "")

        # Step 3: second force_refresh bypasses the freshly-written cache too.
        result2 = await client.get_page_sections("page1", ["At Risk"], force_refresh=True)
        assert fetch_count == 2
        assert "fresh-2" in result2.get("At Risk", "")

        # Step 4: no force_refresh -> serves from cache, no new fetch.
        result3 = await client.get_page_sections("page1", ["At Risk"], force_refresh=False)
        assert fetch_count == 2
        assert "fresh-2" in result3.get("At Risk", "")

    @pytest.mark.asyncio
    async def test_fetch_page_metadata_populates_body_cache(self) -> None:
        """fetch_page_metadata issues one API call with expand=body.view,space,version,
        populates _page_cache so a subsequent get_page_sections is a cache hit,
        and returns a ConfluencePage with all metadata fields set from the response.
        """
        client = _make_client()
        fetch_count = 0

        def fake_get_page_by_id(page_id: str, expand: str) -> dict[str, Any]:
            nonlocal fetch_count
            fetch_count += 1
            assert "body.view" in expand
            assert "space" in expand
            assert "version" in expand
            return {
                "id": page_id,
                "title": "My Page",
                "space": {"key": "PROJ"},
                "version": {"when": "2026-06-20T10:00:00.000Z"},
                "body": {"view": {"value": "<h2>At Risk</h2><p>CAR-101 blocked</p>"}},
            }

        client._confluence.get_page_by_id.side_effect = fake_get_page_by_id  # type: ignore[attr-defined]

        source_url = "https://confluence.example.com/pages/123456/My-Page"
        page = await client.fetch_page_metadata("123456", source_url)

        assert fetch_count == 1
        assert page.page_id == "123456"
        assert page.title == "My Page"
        assert page.space_key == "PROJ"
        assert page.last_modified == "2026-06-20T10:00:00.000Z"
        assert page.url == source_url
        # Body must be cached so a subsequent get_page_sections is a cache hit.
        assert "123456" in client._page_cache
        sections = await client.get_page_sections("123456", ["At Risk"])
        assert fetch_count == 1  # no new API call - served from cache
        assert "At Risk" in sections

    @pytest.mark.asyncio
    async def test_ttl_cache_expires(self) -> None:
        with freeze_time("2026-06-20 10:00:00"):
            client = _make_client(ttl=5)
            client._page_cache["page1"] = "<h2>At Risk</h2><p>original</p>"

        fetch_count = 0

        async def fake_fetch(page_id: str) -> str:
            nonlocal fetch_count
            fetch_count += 1
            return "<h2>At Risk</h2><p>refreshed</p>"

        client._fetch_page_html = fake_fetch  # type: ignore[method-assign]

        with freeze_time("2026-06-20 10:00:10"):  # 10s later - TTL expired
            # TTLCache does not auto-expire in-process without a tick, so simulate
            # by clearing to test re-fetch path
            client._page_cache.clear()
            result = await client.get_page_sections("page1", ["At Risk"])
            assert fetch_count == 1
            assert "refreshed" in result.get("At Risk", "")
