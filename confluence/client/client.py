"""ConfluenceClient - async wrapper around atlassian-python-api (synchronous).

All atlassian-python-api calls are dispatched via asyncio.to_thread() and
retried with the same tenacity policy as AtlasMindLiteClient (transport errors
and 503 retried with exponential backoff + jitter; 4xx not retried).

Caching:
  TTLCache (cachetools, MIT) on page HTML - avoids re-fetching within the same
  process lifetime.

Concurrency:
  Per-page asyncio.Lock (double-checked pattern) prevents the 3-CQL fan-out
  from fetching the same page concurrently and wasting network calls.
  Do NOT pop locks after use - popping orphans queued waiters that hold a
  reference to the same lock object.

TODO M4 AUTH DECISION REQUIRED:
  If per-user Confluence tokens are used (OAuth 3LO), TTLCache key must include
  a hash of the user's permission scope to prevent cross-user data leaks:
    cache_key = (page_id, _hash_scope(user_token))
  If a service-level token is used (all users share one Confluence identity),
  the current key is safe - document this in ConfluenceSettings.
  See: plan_confluence_research.md M4 deferral table, row 1.
"""

import asyncio
from collections import defaultdict
from typing import Any

import structlog
from cachetools import TTLCache
from requests.exceptions import ConnectionError as _ReqConnectionError
from requests.exceptions import Timeout as _ReqTimeout
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from confluence.client.cql_builder import CqlVariants, build_cql_variants
from confluence.client.html_extractor import extract_sections
from confluence.models.intent import QueryIntent
from confluence.models.page import ConfluencePage

# Retry only on transient network failures; 4xx, CQL syntax errors, and auth
# failures are not transient and must not be retried (they would loop forever).
_RETRYABLE: tuple[type[Exception], ...] = (_ReqConnectionError, _ReqTimeout)

logger = structlog.get_logger(__name__)

_TARGET_HEADINGS = [
    "At Risk",
    "Blocked",
    "Blocker",
    "Mitigation",
    "Action Items",
    "Actions",
    "Risk",
    "Escalation",
    "Open Items",
    "Status",
]


class ConfluenceClient:
    """Async Confluence client using atlassian-python-api as the transport.

    Strategy pattern: auth mode (Cloud Basic Auth vs Server Bearer) is
    controlled by whether email is passed to the constructor - handled
    transparently by atlassian-python-api.

    Args:
        base_url: Confluence instance URL.
        api_token: API token (Cloud) or Personal Access Token (Server/DC).
        email: Email for Cloud Basic Auth. Omit for Server (Bearer token used).
        search_limit: Max results per CQL variant search.
        page_cache_ttl_seconds: TTL for the page content cache.
        max_retries: Retry attempts for transport errors.
        content_max_chars: Max chars of section text passed to LLM.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        email: str | None = None,
        search_limit: int = 10,
        page_cache_ttl_seconds: int = 300,
        max_retries: int = 3,
        content_max_chars: int = 10_000,
    ) -> None:
        from atlassian import Confluence  # imported here to keep atlassian-python-api optional

        if email:
            self._confluence = Confluence(  # type: ignore[no-untyped-call]
                url=base_url, username=email, password=api_token
            )
        else:
            self._confluence = Confluence(url=base_url, token=api_token)  # type: ignore[no-untyped-call]

        self._search_limit = search_limit
        self._max_retries = max_retries
        self._content_max_chars = content_max_chars

        # TODO M4 AUTH: see module docstring before changing cache key.
        # Cache stores raw HTML (not stripped plain text) so extract_sections can
        # find heading tags. strip_html is applied per-section inside extract_sections.
        self._page_cache: TTLCache[str, str] = TTLCache(
            maxsize=200, ttl=page_cache_ttl_seconds
        )
        # Per-page locks prevent concurrent duplicate fetches (double-checked lock pattern).
        # Do NOT pop entries - popping orphans queued waiters.
        self._page_fetch_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def search_pages_multi(
        self,
        intent: QueryIntent,
        spaces: list[str],
        recency_days: int = 30,
    ) -> list[ConfluencePage]:
        """Run 3 CQL variants in parallel; return deduplicated pages.

        Results are deduplicated by page_id. Variant failures are logged and
        skipped so a single CQL error does not abort the whole search.
        """
        variants: CqlVariants = build_cql_variants(intent, spaces, recency_days)
        logger.debug(
            "confluence_search_start",
            title_review=variants.title_review,
            title_version=variants.title_version,
            text_version=variants.text_version,
        )

        results = await asyncio.gather(
            self._search_one(variants.title_review, "title_review"),
            self._search_one(variants.title_version, "title_version"),
            self._search_one(variants.text_version, "text_version"),
            return_exceptions=True,
        )

        seen: dict[str, ConfluencePage] = {}
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("confluence_variant_search_failed", error=str(result))
                continue
            for page in result:
                if page.page_id not in seen:
                    seen[page.page_id] = page

        logger.info(
            "confluence_search_complete",
            total_pages=len(seen),
        )
        return list(seen.values())

    async def get_page_sections(
        self,
        page_id: str,
        target_headings: list[str],
        force_refresh: bool = False,
    ) -> dict[str, str]:
        """Fetch page body and return extracted plain-text sections by heading.

        Uses double-checked locking to prevent concurrent duplicate fetches for
        the same page_id. Cache hit path acquires no lock.
        force_refresh skips the cache read but always writes back after fetching.
        """
        if not force_refresh and page_id in self._page_cache:
            return extract_sections(
                self._page_cache[page_id], target_headings, self._content_max_chars
            )

        async with self._page_fetch_locks[page_id]:
            if not force_refresh and page_id in self._page_cache:
                return extract_sections(
                    self._page_cache[page_id], target_headings, self._content_max_chars
                )
            html = await self._fetch_page_html(page_id)
            self._page_cache[page_id] = html

        return extract_sections(html, target_headings, self._content_max_chars)

    async def fetch_page_metadata(
        self,
        page_id: str,
        source_url: str,
        force_refresh: bool = False,
    ) -> ConfluencePage:
        """Fetch page metadata + body in one API call; populates _page_cache for get_page_sections.

        Args:
            page_id: Numeric Confluence page ID.
            source_url: Original URL provided by the caller; stored in ConfluencePage.url.
            force_refresh: Skip TTLCache read; still writes back after fetching.

        Returns:
            ConfluencePage with title, space_key, last_modified, and url populated.
        """
        async with self._page_fetch_locks[page_id]:
            raw: dict[str, Any] = {}
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential_jitter(initial=1, max=10),
                retry=retry_if_exception_type(_RETRYABLE),
                reraise=True,
            ):
                with attempt:
                    raw = await asyncio.to_thread(
                        self._confluence.get_page_by_id,
                        page_id,
                        expand="body.view,space,version",
                    )
        # Always write raw HTML to cache - fresh data just fetched should replace stale.
        html: str = raw.get("body", {}).get("view", {}).get("value", "")
        self._page_cache[page_id] = html
        return ConfluencePage(
            page_id=str(raw["id"]),
            title=str(raw.get("title", "")),
            space_key=str((raw.get("space") or {}).get("key", "")),
            url=source_url,
            last_modified=str((raw.get("version") or {}).get("when", "")),
            cql_excerpt="",
        )

    async def find_pages_mentioning_keys(
        self,
        issue_keys: list[str],
        spaces: list[str],
    ) -> dict[str, list[ConfluencePage]]:
        """Reverse lookup: find Confluence pages that mention any of the given issue keys.

        Returns a dict mapping each found issue_key to the pages that mention it.
        Keys with no matching pages are absent from the result.
        """
        if not issue_keys:
            return {}

        # Per-key CQL search: each query is exact so results map to exactly one key.
        # A batched OR-query would assign every result page to every key in the batch,
        # which is a false-positive that crosses the trust boundary (a page mentioning
        # KEY-1 would be attributed as evidence for KEY-2, KEY-3, etc.).
        result: dict[str, list[ConfluencePage]] = {}
        space_part = _space_clause(spaces)

        for key in issue_keys:
            cql = f'text ~ "{key}"{space_part}'
            try:
                pages = await self._search_one(cql, "reverse_lookup")
                if pages:
                    result[key] = pages
            except Exception as exc:  # best-effort: skip this key if search fails
                logger.warning(
                    "confluence_reverse_lookup_failed",
                    error=str(exc),
                    key=key,
                )

        return result

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    async def _search_one(
        self, cql: str, variant_name: str
    ) -> list[ConfluencePage]:
        """Run a single CQL search via atlassian-python-api (sync, in thread pool)."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=1, max=10),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        )
        raw: list[dict[str, Any]] = []
        async for attempt in retrying:
            with attempt:
                raw = await asyncio.to_thread(
                    self._confluence.cql,
                    cql,
                    limit=self._search_limit,
                )

        pages: list[ConfluencePage] = []
        for item in raw or []:
            try:
                pages.append(_parse_cql_result(item))
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "confluence_result_parse_error",
                    variant=variant_name,
                    error=str(exc),
                )
        logger.debug("confluence_variant_result", variant=variant_name, count=len(pages))
        return pages

    async def _fetch_page_html(self, page_id: str) -> str:
        """Fetch body.view HTML and return raw HTML content for caching.

        Returns raw HTML so extract_sections can locate heading tags.
        strip_html is applied per-section inside extract_sections, not here.
        """
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=1, max=10),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        )
        raw: dict[str, Any] = {}
        async for attempt in retrying:
            with attempt:
                raw = await asyncio.to_thread(
                    self._confluence.get_page_by_id,
                    page_id,
                    expand="body.view",
                )

        return str(raw.get("body", {}).get("view", {}).get("value", ""))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_cql_result(item: dict[str, Any]) -> ConfluencePage:
    """Parse one atlassian-python-api CQL result item into a ConfluencePage."""
    content = item.get("content") or item
    return ConfluencePage(
        page_id=str(content["id"]),
        title=str(content.get("title") or item.get("title", "")),
        space_key=str(
            (content.get("space") or {}).get("key")
            or item.get("space", {}).get("key", "")
        ),
        url=str(
            (content.get("_links") or {}).get("webui")
            or item.get("url", "")
        ),
        last_modified=str(
            (content.get("history") or {}).get("lastUpdated", {}).get("when")
            or item.get("lastModified", "")
        ),
        cql_excerpt=str(item.get("excerpt", "")),
    )


def _space_clause(spaces: list[str]) -> str:
    if not spaces:
        return ""
    quoted = ", ".join(f'"{s}"' for s in spaces)
    return f" AND space IN ({quoted})"
