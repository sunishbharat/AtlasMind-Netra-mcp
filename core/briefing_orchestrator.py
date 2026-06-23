"""generate_briefing pipeline: decompose -> clarify -> fan-out -> analyse -> rank -> report.

Wires AgendaDecomposer, the existing query Orchestrator (for per-topic dispatch with
clarification), IssueAnalyser, RankingEngine, ReportSynthesiser, and DeliveryChannel
into a single generate_briefing call. Per-topic session IDs isolate clarification state
so each topic's rounds are independent of the others.

When ConfluenceClient is configured, _build_sections runs a 3-phase enrichment per topic:
  Phase 1 - QueryIntentAnalyser extracts version refs and intent type
  Phase 2 - Confluence CQL fan-out + LLM extraction (parallel, bounded by _confluence_sem)
  Phase 3 - Reverse lookup: find pages for Jira-found keys not already resolved
Confluence context is then injected into IssueAnalyser prompts (AI SUGGESTION trust boundary).
When ConfluenceClient is None, the pipeline runs unchanged (opt-in design).

Concurrency fixes (from multiuser_concurrency_fixes.md):
  Item 1: self._confluence_sem at instance level (not per-call).
  Item 3: structlog contextvars bound at tool-handler entry (in server.py).
  Item 4: _fetch_and_extract returns values, single-threaded post-gather merge.
  Item 5: Per-page asyncio.Lock in ConfluenceClient (see confluence/client/client.py).
  Item 6: TODO comment at cache definitions (M4 auth decision required).
  Item 7: spaces: list[str] | None param in _run_confluence_research + generate_briefing.

TODO Item 2 (M4 ready): shard conventions store to data/conventions/{session_id}.json.
  Requires Orchestrator to accept a per-session store factory; deferred to M4 auth decision.
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import structlog

from briefings.delivery import BaseDeliveryChannel
from config.settings import Settings
from confluence.models.reference import ConfluenceReference
from core.agenda_decomposer import AgendaDecomposerPort
from core.exceptions import AnalysisError, DecompositionError, LiteBackendError, NetraError
from core.issue_analyser import IssueAnalyserPort
from core.orchestrator import ElicitFn, LiteClientPort, QueryHandler
from core.ranking_engine import RankingEnginePort
from core.report_synthesiser import ReportSynthesiser
from memory.briefing_session_store import BaseBriefingSessionStore, BriefingPendingState
from models.lite import IssueDetailsResponse
from models.responses import (
    AgendaTopic,
    BlockerAnalysis,
    BriefingResponse,
    BriefingSection,
    QueryResponse,
    ReportResponse,
)

if TYPE_CHECKING:
    from confluence.client.base import ConfluenceClientProtocol
    from confluence.extraction.extractor import ContextExtractor
    from confluence.models.extraction import ContextExtractionOutput
    from confluence.models.page import ConfluencePage
    from confluence.models.response import ConfluenceContextResponse
    from core.query_intent_analyser import QueryIntentAnalyserPort

logger = structlog.get_logger(__name__)


class BriefingHandler(Protocol):
    """What server.py needs from the briefing orchestrator (lets tests inject a fake)."""

    async def generate_briefing(
        self,
        *,
        agenda_text: str,
        session_id: str,
        projects: list[str] | None = None,
        clarification_answer: str | None = None,
        elicit: ElicitFn | None = None,
    ) -> BriefingResponse: ...

    async def get_briefing_report(self, report_id: str, session_id: str) -> ReportResponse: ...

    async def search_context(
        self,
        query: str,
        spaces: list[str] | None = None,
        recency_days: int | None = None,
        limit: int = 5,
    ) -> "ConfluenceContextResponse": ...


class BriefingOrchestrator:
    """Runs the generate_briefing pipeline end-to-end."""

    def __init__(
        self,
        *,
        decomposer: AgendaDecomposerPort,
        query_handler: QueryHandler,
        lite_client: LiteClientPort,
        issue_analyser: IssueAnalyserPort,
        ranking_engine: RankingEnginePort,
        report_synthesiser: ReportSynthesiser,
        delivery_channel: BaseDeliveryChannel,
        briefing_sessions: BaseBriefingSessionStore,
        settings: Settings,
        intent_analyser: "QueryIntentAnalyserPort | None" = None,
        confluence: "ConfluenceClientProtocol | None" = None,
        context_extractor: "ContextExtractor | None" = None,
    ) -> None:
        self._decomposer = decomposer
        self._query_handler = query_handler
        self._lite = lite_client
        self._analyser = issue_analyser
        self._ranker = ranking_engine
        self._synthesiser = report_synthesiser
        self._delivery = delivery_channel
        self._briefing_sessions = briefing_sessions
        self._settings = settings
        self._intent_analyser = intent_analyser
        self._confluence = confluence
        self._context_extractor = context_extractor
        # Item 1: semaphore at instance level, shared across all users and topics.
        # No _llm_global_sem: the value would equal the sum of _analysis_sem +
        # _confluence_sem, making it logically inert. Two independent pools are correct.
        self._confluence_sem = asyncio.Semaphore(
            settings.confluence.confluence_concurrency
        )

    async def generate_briefing(
        self,
        *,
        agenda_text: str,
        session_id: str,
        projects: list[str] | None = None,
        clarification_answer: str | None = None,
        elicit: ElicitFn | None = None,
    ) -> BriefingResponse:
        log = logger.bind(session_id=session_id)
        errors: list[str] = []

        pending = await self._briefing_sessions.get(session_id)

        if pending is None:
            try:
                topics = await self._decomposer.decompose(agenda_text, projects)
            except DecompositionError as exc:
                log.error("agenda_decomposition_failed", error=str(exc))
                return BriefingResponse(
                    report_id=f"failed_{session_id}",
                    session_id=session_id,
                    errors=[f"agenda decomposition failed: {exc}"],
                )
            if not topics:
                return BriefingResponse(
                    report_id=f"empty_{session_id}",
                    session_id=session_id,
                    errors=["no data questions could be extracted from the agenda"],
                )
            pending = BriefingPendingState(
                session_id=session_id,
                agenda_text=agenda_text,
                topics=topics,
            )
            log.info("briefing_started", topic_count=len(topics))

        # agenda_text is ignored on continuation; all state flows from the stored pending object.
        topics = pending.topics
        # Shallow copy so we can accumulate results without touching the frozen pending.
        completed: dict[str, QueryResponse] = dict(pending.completed_results)

        for i in range(pending.current_topic_idx, len(topics)):
            topic = topics[i]
            topic_session_id = f"{session_id}__topic_{topic.topic_id}"

            # Forward a clarification answer only to the currently pending topic.
            topic_ca = clarification_answer if i == pending.current_topic_idx else None

            result = await self._query_handler.handle_query(
                query=topic.suggested_query,
                session_id=topic_session_id,
                clarification_answer=topic_ca,
                elicit=elicit,
                limit=self._settings.briefing.issues_per_topic,
            )

            if result.requires_user_input:
                new_pending = pending.model_copy(
                    update={"current_topic_idx": i, "completed_results": completed},
                    deep=True,
                )
                await self._briefing_sessions.set(new_pending)
                log.info("briefing_clarification_needed", topic_id=topic.topic_id)
                return BriefingResponse(
                    report_id=f"pending_{session_id}",
                    session_id=session_id,
                    requires_user_input=True,
                    clarification_question=result.clarification_question,
                    pending_topic_id=topic.topic_id,
                )

            completed[topic.topic_id] = result
            if result.errors:
                errors.extend(f"[{topic.topic_id}] {e}" for e in result.errors)
            log.info("briefing_topic_dispatched", topic_id=topic.topic_id, total=result.total)

        await self._briefing_sessions.delete(session_id)

        # Confluence enrichment (Phase 1-2) runs after all Jira queries complete.
        # Phase 3 (reverse lookup) runs inside _run_confluence_research.
        # TODO: move Phase 2 to before Jira queries to seed JQL (requires storing
        # pre-pass results in BriefingPendingState across clarification round-trips).
        confluence_refs: dict[str, list[ConfluenceReference]] = {}
        if self._confluence is not None:
            all_jira_keys: list[str] = []
            for r in completed.values():
                if r and r.issues:
                    for issue in r.issues:
                        key = issue.get("key") or issue.get("Key", "")
                        if key:
                            all_jira_keys.append(str(key))
            try:
                confluence_refs = await self._run_confluence_research(
                    topics, completed, all_jira_keys, spaces=None
                )
            except Exception as exc:
                log.warning("confluence_research_failed", error=str(exc))

        sections = await self._build_sections(topics, completed, errors, log, confluence_refs)
        report_id = _make_report_id(session_id)

        content = self._synthesiser.build_briefing_report(
            agenda_text=pending.agenda_text,
            sections=sections,
            session_id=session_id,
        )

        report_path: str | None = None
        view_url: str | None = None
        if self._settings.delivery.enabled:
            try:
                report_path = await self._delivery.deliver(report_id=report_id, content=content)
            except (OSError, NetraError) as exc:
                log.warning("briefing_delivery_failed", error=str(exc))
                errors.append(f"report not written: {exc}")

            if report_path is not None:
                # Sidecar written regardless of whether delivery returned a file path;
                # always stored next to the .md in output_dir so get_briefing_report can find it.
                json_path = self._settings.delivery.output_dir / f"{report_id}.json"
                try:
                    await _store_briefing_json(json_path, report_id, sections)
                except OSError as exc:
                    log.warning(
                        "briefing_sidecar_write_failed", path=str(json_path), error=str(exc)
                    )
                    errors.append(f"report JSON sidecar not written: {exc}")

        if self._settings.briefing.view_url_base:
            view_url = f"{self._settings.briefing.view_url_base.rstrip('/')}/{report_id}"

        log.info("briefing_complete", report_id=report_id, sections=len(sections))
        return BriefingResponse(
            report_id=report_id,
            session_id=session_id,
            sections=sections,
            report_path=report_path,
            view_url=view_url,
            errors=errors,
        )

    async def _run_confluence_research(
        self,
        topics: list[AgendaTopic],
        completed: dict[str, QueryResponse],
        all_jira_keys: list[str],
        spaces: list[str] | None = None,  # Item 7: per-call override
    ) -> dict[str, list[ConfluenceReference]]:
        """Run Phase 1 (intent) + Phase 2 (Confluence search/extraction) for all topics.

        Runs intent analysis + Confluence search per topic, then reverse lookup for
        Jira-found keys not already resolved. Returns merged confluence_refs dict.

        spaces: per-call override; falls back to NETRA_CONFLUENCE__DEFAULT_SPACES.
        TODO M4: resolve spaces from user token claims or tenant config.
        """
        assert self._confluence is not None
        assert self._context_extractor is not None

        resolved_spaces = spaces or self._settings.confluence.default_spaces

        # Per-topic Phase 1 + Phase 2 in parallel (bounded by _confluence_sem in _fetch_and_extract)
        topic_tasks = [
            self._research_topic(topic, resolved_spaces)
            for topic in topics
            if self._intent_analyser is not None
        ]
        topic_results = await asyncio.gather(*topic_tasks, return_exceptions=True)

        confluence_refs: dict[str, list[ConfluenceReference]] = {}
        for result in topic_results:
            if isinstance(result, BaseException):
                logger.warning("confluence_topic_research_failed", error=str(result))
                continue
            _keys, page_refs = result
            for key, ref in page_refs.items():
                confluence_refs.setdefault(key, []).append(ref)

        # Phase 3: reverse lookup for Jira-found keys not already in confluence_refs.
        reverse_keys = [k for k in all_jira_keys if k not in confluence_refs]
        if reverse_keys and resolved_spaces:
            try:
                reverse = await self._confluence.find_pages_mentioning_keys(
                    reverse_keys, resolved_spaces
                )
                existing_batch_ids: dict[str, set[str]] = {}
                for key, pages in reverse.items():
                    existing_ids = existing_batch_ids.setdefault(
                        key, {r.page_id for r in confluence_refs.get(key, [])}
                    )
                    for page in pages:
                        if page.page_id not in existing_ids:
                            confluence_refs.setdefault(key, []).append(
                                ConfluenceReference(
                                    page_id=page.page_id,
                                    page_title=page.title,
                                    cql_excerpt=page.cql_excerpt,
                                    relevant_passage="",
                                )
                            )
                            existing_ids.add(page.page_id)
            except Exception as exc:
                logger.warning("confluence_reverse_lookup_failed", error=str(exc))

        logger.info(
            "confluence_research_complete",
            issues_with_refs=len(confluence_refs),
            total_refs=sum(len(v) for v in confluence_refs.values()),
        )
        return confluence_refs

    async def _research_topic(
        self,
        topic: AgendaTopic,
        spaces: list[str],
    ) -> tuple[list[str], dict[str, ConfluenceReference]]:
        """Phase 1 + Phase 2 for one topic: intent -> CQL search -> extraction.

        Returns (all_jira_keys_found, {key: first_reference}).
        """
        assert self._intent_analyser is not None
        assert self._confluence is not None
        assert self._context_extractor is not None

        intent = await self._intent_analyser.analyse(topic.suggested_query)
        if intent.intent_type == "general" or not intent.confluence_keywords:
            return [], {}

        pages = await self._confluence.search_pages_multi(
            intent, spaces, recency_days=self._settings.confluence.recency_days
        )
        pages = pages[: self._settings.confluence.max_pages_total]
        if not pages:
            return [], {}

        # Item 4: _fetch_and_extract returns values - no closure mutation.
        # asyncio.gather() propagates contextvars automatically into subtasks (Python copies
        # context on coroutine spawn). If asyncio.create_task() is ever used instead,
        # pass context=contextvars.copy_context() explicitly.
        results = await asyncio.gather(
            *(
                _fetch_and_extract(
                    page,
                    self._confluence,
                    self._context_extractor,
                    self._confluence_sem,
                )
                for page in pages
            ),
            return_exceptions=True,
        )

        all_keys: list[str] = []
        page_refs: dict[str, ConfluenceReference] = {}
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("confluence_page_extraction_failed", error=str(result))
                continue
            keys, refs = result
            all_keys.extend(keys)
            for key, ref in refs.items():
                if key not in page_refs:  # first page wins per topic
                    page_refs[key] = ref

        return all_keys, page_refs

    async def get_briefing_report(self, report_id: str, session_id: str) -> ReportResponse:
        """Read a stored briefing report by ID. Returns sections from the JSON sidecar.

        session_id is accepted now for M4 ACL enforcement (ownership check); currently unused.

        Raises:
            ValueError: if report_id contains path traversal or other unsafe characters.
        """
        # Validate report_id to prevent path traversal attacks (C-1)
        if not _is_safe_report_id(report_id):
            raise ValueError(f"report_id contains unsafe characters: {report_id!r}")

        log = logger.bind(report_id=report_id, session_id=session_id)
        json_path = self._settings.delivery.output_dir / f"{report_id}.json"
        md_path = self._settings.delivery.output_dir / f"{report_id}.md"

        json_exists = await asyncio.to_thread(json_path.exists)
        md_exists = await asyncio.to_thread(md_path.exists)

        if not json_exists:
            return ReportResponse(
                report_id=report_id,
                report_path=str(md_path) if md_exists else None,
                errors=["report JSON not found; this may be a query_jira report, not a briefing"],
            )

        try:
            raw = await asyncio.to_thread(json_path.read_text, encoding="utf-8")
            data = json.loads(raw)
            sections = [BriefingSection.model_validate(s) for s in data.get("sections", [])]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error("briefing_report_unreadable", report_id=report_id, error=str(exc))
            return ReportResponse(
                report_id=report_id,
                errors=[f"report could not be read: {exc}"],
            )

        view_url: str | None = None
        if self._settings.briefing.view_url_base:
            view_url = f"{self._settings.briefing.view_url_base.rstrip('/')}/{report_id}"

        return ReportResponse(
            report_id=report_id,
            sections=sections,
            report_path=str(md_path) if md_exists else None,
            view_url=view_url,
        )

    async def search_context(
        self,
        query: str,
        spaces: list[str] | None = None,
        recency_days: int | None = None,
        limit: int = 5,
    ) -> "ConfluenceContextResponse":
        """Search Confluence for context relevant to the given query.

        Returns an empty ConfluenceContextResponse when Confluence is not configured.
        Reuses the instance-level semaphore to bound concurrent page fetches.
        """
        from confluence.client.cql_builder import build_cql_variants
        from confluence.models.extraction import ContextExtractionOutput, ContextSearchResult
        from confluence.models.response import ConfluenceContextResponse

        if (
            self._confluence is None
            or self._intent_analyser is None
            or self._context_extractor is None
        ):
            return ConfluenceContextResponse()

        resolved_spaces = spaces or self._settings.confluence.default_spaces
        _recency = (
            recency_days if recency_days is not None else self._settings.confluence.recency_days
        )

        intent = await self._intent_analyser.analyse(query)
        pages = await self._confluence.search_pages_multi(intent, resolved_spaces, _recency)
        pages = pages[:limit]

        variants = build_cql_variants(intent, resolved_spaces, _recency)

        # Concurrent fetch+extract bounded by the shared instance semaphore.
        extractor = self._context_extractor
        confluence = self._confluence
        sem = self._confluence_sem

        async def _fetch_one(p: "ConfluencePage") -> ContextExtractionOutput:
            async with sem:
                secs = await confluence.get_page_sections(p.page_id, _TARGET_HEADINGS)
                return await extractor.extract(p, secs)

        gather_results = await asyncio.gather(
            *(_fetch_one(p) for p in pages),
            return_exceptions=True,
        )

        results: list[ContextSearchResult] = []
        errors: list[str] = []
        for page, gr in zip(pages, gather_results, strict=False):
            if isinstance(gr, BaseException):
                errors.append(f"page {page.page_id}: {gr}")
                continue
            results.append(
                ContextSearchResult(
                    page=page,
                    jira_keys_mentioned=gr.jira_keys_mentioned,
                    extracted_mitigations=gr.action_items,
                    extracted_owners=gr.mitigation_owners,
                )
            )

        return ConfluenceContextResponse(
            results=results,
            total_pages_found=len(pages),
            cql_used=variants.as_list(),
            errors=errors,
        )

    async def _build_sections(
        self,
        topics: list[AgendaTopic],
        completed: dict[str, QueryResponse],
        errors: list[str],
        log: structlog.stdlib.BoundLogger,
        confluence_refs: dict[str, list[ConfluenceReference]] | None = None,
    ) -> list[BriefingSection]:
        all_keys: list[str] = []
        summaries: dict[str, str] = {}
        topic_keys: dict[str, list[str]] = {}

        for topic in topics:
            result = completed.get(topic.topic_id)
            keys: list[str] = []
            if result and result.issues:
                for issue in result.issues:
                    key = issue.get("key") or issue.get("Key", "")
                    if key:
                        keys.append(str(key))
                        summary = issue.get("summary") or issue.get("Summary", "")
                        if summary:
                            summaries[str(key)] = str(summary)
            topic_keys[topic.topic_id] = keys
            all_keys.extend(keys)

        all_keys = list(dict.fromkeys(all_keys))  # dedup across topics, preserve order
        max_cap = self._settings.briefing.max_analysed_issues
        dropped_keys_per_topic: dict[str, int] = {}
        if len(all_keys) > max_cap:
            kept_keys: set[str] = set(all_keys[:max_cap])
            for topic_id, keys in topic_keys.items():
                dropped = sum(1 for k in keys if k not in kept_keys)
                if dropped:
                    dropped_keys_per_topic[topic_id] = dropped
                    if dropped == len(keys):
                        # Surface entirely-skipped topics in the top-level response errors so
                        # callers see the truncation without inspecting each section.
                        errors.append(
                            f"[{topic_id}] all {dropped} issue(s) skipped - "
                            f"max_analysed_issues cap ({max_cap}) reached before this topic."
                        )
            all_keys = all_keys[:max_cap]
            log.warning(
                "briefing_issues_truncated",
                max_analysed_issues=max_cap,
                dropped_per_topic=dropped_keys_per_topic,
            )

        issue_details = IssueDetailsResponse(issues=[])
        if all_keys:
            try:
                issue_details = await self._lite.get_issue_details(
                    all_keys,
                    comments_limit=self._settings.lite.comments_limit_default,
                )
                if issue_details.error:
                    errors.append(f"issue_details error: {issue_details.error}")
                    log.warning("issue_details_error", error=issue_details.error)
            except (LiteBackendError, OSError) as exc:
                errors.append(f"issue_details fetch failed: {exc}")
                log.error("issue_details_failed", error=str(exc))

        analyses: list[BlockerAnalysis] = []
        if all_keys:
            try:
                analyses = await self._analyser.analyse(
                    issue_details,
                    all_keys,
                    summaries or None,
                    confluence_refs or None,
                )
            except AnalysisError as exc:
                errors.append(f"issue analysis failed: {exc}")
                log.error("issue_analysis_failed", error=str(exc))

        analysis_map = {a.issue_key: a for a in analyses}
        top_n = self._settings.briefing.top_n
        sections: list[BriefingSection] = []

        for topic in topics:
            result = completed.get(topic.topic_id)
            keys = topic_keys.get(topic.topic_id, [])
            topic_analyses = [analysis_map[k] for k in keys if k in analysis_map]
            ranked = self._ranker.rank(topic_analyses, top_n=top_n)
            section_errors = list(result.errors) if result else []
            if dropped_keys_per_topic.get(topic.topic_id):
                dropped = dropped_keys_per_topic[topic.topic_id]
                section_errors.append(
                    f"{dropped} issue(s) dropped from this topic due to "
                    f"max_analysed_issues cap ({max_cap}); results are partial."
                )
            sections.append(
                BriefingSection(
                    topic_id=topic.topic_id,
                    description=topic.description,
                    query_used=topic.suggested_query,
                    jql=result.jql if result else None,
                    top_issues=ranked,
                    total_found=result.total if result else 0,
                    errors=section_errors,
                )
            )

        return sections


async def _store_briefing_json(path: Path, report_id: str, sections: list[BriefingSection]) -> None:
    data = {"report_id": report_id, "sections": [s.model_dump() for s in sections]}
    content = json.dumps(data, indent=2)
    await asyncio.to_thread(path.write_text, content, "utf-8")


def _make_report_id(session_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", session_id)[:40]
    return f"briefing_{datetime.now(UTC):%Y%m%d_%H%M%S}_{slug}_{uuid4().hex[:8]}"


_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_-]{0,127}$")


_TARGET_HEADINGS = [
    "At Risk", "Blocked", "Blocker", "Mitigation", "Action Items",
    "Actions", "Risk", "Escalation", "Open Items", "Status",
]


async def _fetch_and_extract(
    page: "ConfluencePage",
    confluence: "ConfluenceClientProtocol",
    extractor: "ContextExtractor",
    sem: asyncio.Semaphore,
) -> tuple[list[str], dict[str, ConfluenceReference]]:
    """Item 4: fetch page sections + run LLM extraction; returns values (no closure mutation).

    Acquires sem once - no nested semaphore acquisition. Single-threaded merge
    post-gather eliminates the non-atomic read-check-write race on confluence_refs.
    """
    async with sem:
        sections = await confluence.get_page_sections(page.page_id, _TARGET_HEADINGS)
        extraction = await extractor.extract(page, sections)

    page_refs: dict[str, ConfluenceReference] = {
        key: ConfluenceReference(
            page_id=page.page_id,
            page_title=page.title,
            cql_excerpt=page.cql_excerpt,
            relevant_passage=_extract_relevant_passage(extraction, key),
        )
        for key in extraction.jira_keys_mentioned
    }
    return extraction.jira_keys_mentioned, page_refs


def _extract_relevant_passage(extraction: "ContextExtractionOutput", key: str) -> str:
    """Find the most relevant passage for a specific issue key from extraction output."""
    # Use the first action item that mentions the key, otherwise severity signals.
    for item in extraction.action_items:
        if key in item:
            return item[:300]
    for signal in extraction.severity_signals:
        if key in signal:
            return signal[:300]
    # Fallback: summarise what was found.
    if extraction.action_items:
        return extraction.action_items[0][:300]
    return ""


def _is_safe_report_id(report_id: str) -> bool:
    """Return True if report_id is safe for use in file paths (no traversal, etc.).

    A safe report_id matches the pattern: alphanumeric + dash/underscore, up to 128 chars.
    Generated report_ids (via _make_report_id) never contain dots, so any . character
    is a traversal attempt.
    """
    if not report_id or len(report_id) > 128:
        return False
    # Reject any report_id with path separators or traversal attempts
    if "/" in report_id or "\\" in report_id or ".." in report_id:
        return False
    return _REPORT_ID_RE.match(report_id) is not None
