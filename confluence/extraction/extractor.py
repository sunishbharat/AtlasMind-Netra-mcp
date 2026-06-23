"""ContextExtractor - LLM extraction of structured data from Confluence page sections.

Two caching layers:
  1. Prompt caching (PydanticAI model_settings): system prompt is large and
     stable across all page calls in a briefing. Prompt caching amortises the
     upload cost across every page in the batch.
  2. Result cache (LRUCache, cachetools): keyed by (page_id, last_modified).
     LRU is correct here - content identity (not time) determines staleness.
     The TTLCache on ConfluenceClient handles HTTP-level expiry.

Double-checked lock pattern prevents concurrent duplicate LLM calls when the
same page is requested by multiple topics simultaneously. Do NOT pop locks
after use - popping orphans queued waiters that hold a reference to the same
lock object.

TODO M4 AUTH DECISION REQUIRED:
  If per-user tokens are used (OAuth 3LO), LRU cache key must include a hash
  of the user's permission scope to prevent cross-user data leaks.
  See: plan_confluence_research.md M4 deferral table, row 1.
"""

import asyncio
from collections import defaultdict

import structlog
from cachetools import LRUCache
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models import Model

from config.settings import ConfluenceSettings
from confluence.models.extraction import ContextExtractionOutput
from confluence.models.page import ConfluencePage
from core.exceptions import AnalysisError, ConfigError

logger = structlog.get_logger(__name__)


class ContextExtractor:
    """Extracts structured data from Confluence page sections using PydanticAI.

    The agent is injected already-constructed so tests can pass
    TestModel/FunctionModel without a live LLM.
    """

    def __init__(
        self,
        agent: Agent[None, ContextExtractionOutput],
        *,
        cache_max_size: int = 200,
    ) -> None:
        self._agent = agent
        # TODO M4 AUTH: see module docstring before changing cache key.
        self._cache: LRUCache[tuple[str, str], ContextExtractionOutput] = LRUCache(
            maxsize=cache_max_size
        )
        # Per-(page_id, last_modified) locks prevent concurrent duplicate LLM calls.
        # Do NOT pop entries after use - popping orphans queued waiters.
        self._extract_locks: dict[
            tuple[str, str], asyncio.Lock
        ] = defaultdict(asyncio.Lock)

    async def extract(
        self,
        page: ConfluencePage,
        sections: dict[str, str],
    ) -> ContextExtractionOutput:
        """Run LLM extraction on page sections; returns cached result on cache hit.

        Args:
            page: Page metadata (page_id and last_modified are the cache key).
            sections: Plain-text sections extracted by html_extractor, e.g.
                      {'At Risk': '...', 'Mitigation': '...'}.

        Returns:
            ContextExtractionOutput with jira_keys_mentioned, mitigation_owners,
            severity_signals, and action_items.
        """
        cache_key = (page.page_id, page.last_modified)

        # Fast path - no lock needed for read.
        if cache_key in self._cache:
            logger.debug("context_extractor_cache_hit", page_id=page.page_id)
            return self._cache[cache_key]

        # Per-(page_id, last_modified) lock - prevents concurrent duplicate LLM calls.
        async with self._extract_locks[cache_key]:
            # Re-check after acquiring - another coroutine may have extracted while waiting.
            if cache_key in self._cache:
                logger.debug(
                    "context_extractor_cache_hit_post_lock", page_id=page.page_id
                )
                return self._cache[cache_key]

            prompt = _build_extraction_prompt(page, sections)
            try:
                run_result = await self._agent.run(prompt)
                output: ContextExtractionOutput = run_result.output
            except (AgentRunError, UserError) as exc:
                raise AnalysisError(
                    f"context extraction LLM call failed for page {page.page_id}: {exc}"
                ) from exc

            self._cache[cache_key] = output
            logger.info(
                "context_extracted",
                page_id=page.page_id,
                page_title=page.title,
                jira_keys=output.jira_keys_mentioned,
            )
            return output


def build_context_extractor(
    model: Model | str,
    settings: ConfluenceSettings,
    retries: int = 2,
) -> ContextExtractor:
    """Factory: construct ContextExtractor from ConfluenceSettings."""
    try:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "prompts" / "context_extraction.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read Confluence context extraction prompt: {exc}"
        ) from exc

    agent: Agent[None, ContextExtractionOutput] = Agent(
        model,
        output_type=ContextExtractionOutput,
        system_prompt=system_prompt,
        retries=retries,
        model_settings=ModelSettings(  # type: ignore[typeddict-unknown-key]
            bedrock_cache_instructions=True,
            anthropic_cache_instructions=True,
        ),
    )
    return ContextExtractor(agent=agent)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_extraction_prompt(
    page: ConfluencePage, sections: dict[str, str]
) -> str:
    lines = [
        "OPERATION: EXTRACT CONFLUENCE CONTEXT",
        f"PAGE: {page.title}",
        f"SPACE: {page.space_key}",
        f"LAST MODIFIED: {page.last_modified}",
        "",
    ]

    if sections:
        for heading, content in sections.items():
            lines.append(f"=== {heading} ===")
            lines.append(content[:2000])  # per-section guard; total capped by content_max_chars
            lines.append("")
    else:
        lines.append("(no target sections found in this page)")
        lines.append("")

    lines += [
        "-> Extract jira_keys_mentioned: all Jira issue keys (PROJECT-NNN format) found.",
        "-> Extract mitigation_owners: person names in owner / responsible / action columns.",
        "-> Extract severity_signals: words like blocked, escalated, at risk, critical.",
        "-> Extract action_items: explicit action/task lines or table rows with owners/dates.",
        "-> Return empty lists for categories not found. Never fabricate data.",
    ]
    return "\n".join(lines)
