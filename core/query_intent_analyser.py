"""QueryIntentAnalyser - extracts structured intent from a natural-language query.

First component to run in the 3-phase Confluence pipeline. One PydanticAI call
produces a QueryIntent which drives CQL variant construction (Phase 2) and
JQL enrichment (Phase 3).

QueryIntent lives in confluence/models/intent.py (not here) to keep the
dependency arrow one-way: core/ imports from confluence/, never the reverse.
"""

from typing import Protocol

import structlog
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models import Model

from config.settings import ConfluenceSettings
from confluence.models.intent import QueryIntent
from core.exceptions import ConfigError

logger = structlog.get_logger(__name__)


class QueryIntentAnalyserPort(Protocol):
    """Analysis seam - tests can inject a stub without a live LLM."""

    async def analyse(self, query: str) -> QueryIntent: ...


class QueryIntentAnalyser:
    """Extracts structured QueryIntent from a natural-language agenda topic.

    The PydanticAI agent is injected already-constructed so tests can pass
    TestModel/FunctionModel without a live LLM (same pattern as IssueAnalyser).
    """

    def __init__(
        self,
        agent: Agent[None, QueryIntent],
    ) -> None:
        self._agent = agent

    async def analyse(self, query: str) -> QueryIntent:
        """Run intent extraction on a natural-language query.

        Returns QueryIntent with intent_type='general' (no Confluence needed)
        as a graceful fallback when the LLM call fails.

        Args:
            query: Natural-language agenda topic or search query.

        Returns:
            QueryIntent with extracted version refs, risk signals, and intent type.
        """
        prompt = f"QUERY: {query}\n\nExtract structured intent."
        try:
            run_result = await self._agent.run(prompt)
            intent: QueryIntent = run_result.output
            logger.debug(
                "query_intent_extracted",
                intent_type=intent.intent_type,
                version_refs=intent.version_refs,
                keywords=intent.confluence_keywords,
            )
            return intent
        except (AgentRunError, UserError) as exc:
            logger.warning(
                "query_intent_extraction_failed",
                query=query[:100],
                error=str(exc),
            )
            # Graceful degradation: return a 'general' intent so Confluence is skipped.
            return QueryIntent(intent_type="general")


def build_query_intent_analyser(
    model: Model | str,
    settings: ConfluenceSettings,
    retries: int = 2,
) -> QueryIntentAnalyser:
    """Factory: construct QueryIntentAnalyser from ConfluenceSettings."""
    from pathlib import Path

    prompt_path = Path(__file__).parent.parent / "confluence" / "prompts" / "query_intent.md"
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read query intent prompt at {prompt_path}: {exc}"
        ) from exc

    agent: Agent[None, QueryIntent] = Agent(
        model,
        output_type=QueryIntent,
        system_prompt=system_prompt,
        retries=retries,
        model_settings=ModelSettings(  # type: ignore[typeddict-unknown-key]
            bedrock_cache_instructions=True,
            anthropic_cache_instructions=True,
        ),
    )
    return QueryIntentAnalyser(agent=agent)
