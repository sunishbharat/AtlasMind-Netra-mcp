"""Main agent loop (design doc: orchestrator.py).

Wires the clarification state machine: detect ambiguous terms -> resolve from session or
learned conventions -> ask one targeted question (MCP elicitation first, session round-trip
fallback) -> learn the answer -> dispatch the disambiguated query to the atlasMind backend.

Dependency injection: every collaborator arrives via the constructor; the object graph is
assembled once in server.py. The clarifier and backend client are typed as Protocols so
tests fake the LLM and network seams without subclassing.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import structlog

from briefings.delivery import BaseDeliveryChannel
from config.settings import INSTANCE_DEFAULT_PROJECT, Settings
from core.exceptions import ClarificationError, LiteBackendError, NetraError
from core.intent_classifier import IntentClassifier
from core.jira_fields_loader import JiraFieldsLoader
from core.report_renderer import ReportRenderer
from core.report_synthesiser import ReportSynthesiser
from core.vocab_lookup import VocabLookup
from memory.conventions_store import BaseConventionsStore, Convention
from memory.session_store import (
    BaseSessionStore,
    ClarificationState,
    SessionState,
    TermResolution,
)
from models.clarification import ClarificationNeeded, ResolvedTerms
from models.frontend import InjectAck
from models.jira import JiraField
from models.lite import IssueDetailsResponse, LiteQueryResult
from models.responses import AppliedConvention, QueryResponse
from models.vocab import VocabEntry

logger = structlog.get_logger(__name__)

ElicitFn = Callable[[str], Awaitable[str | None]]
"""Asks the user one question mid-call (MCP elicitation); None = host cannot or user declined."""


class ClarifierPort(Protocol):
    """LLM seam of the orchestrator (implemented by core.clarifier.Clarifier)."""

    async def formulate_question(
        self,
        *,
        query: str,
        terms: list[str],
        vocab: dict[str, VocabEntry],
        conventions: list[Convention],
        fields: list[JiraField],
    ) -> ClarificationNeeded: ...

    async def resolve_answer(
        self,
        *,
        query: str,
        terms: list[str],
        answer: str,
        vocab: dict[str, VocabEntry],
    ) -> ResolvedTerms: ...


class LiteClientPort(Protocol):
    """Backend seam of the orchestrator (implemented by AtlasMindLiteClient)."""

    async def query(
        self,
        text: str,
        *,
        limit: int | None = None,
        jira_token: str | None = None,
        jira_email: str | None = None,
        jira_url: str | None = None,
    ) -> LiteQueryResult: ...

    async def get_issue_details(
        self,
        issue_keys: list[str],
        *,
        comments_limit: int | None = None,
        request_id: str | None = None,
    ) -> IssueDetailsResponse: ...


class FrontendPort(Protocol):
    """UI bridge seam of the orchestrator (implemented by FrontendBridgeClient)."""

    async def inject(self, query: str, request_id: str | None = None) -> InjectAck: ...


class QueryHandler(Protocol):
    """What server.py needs from the orchestrator (lets tests inject a fake)."""

    async def handle_query(
        self,
        *,
        query: str,
        session_id: str,
        clarification_answer: str | None = None,
        elicit: ElicitFn | None = None,
        limit: int | None = None,
        show_in_ui: bool = False,
    ) -> QueryResponse: ...


class Orchestrator:
    """Runs the clarification loop for query_jira (state machine in SessionState)."""

    def __init__(
        self,
        *,
        session_store: BaseSessionStore,
        conventions_store: BaseConventionsStore,
        intent_classifier: IntentClassifier,
        clarifier: ClarifierPort,
        lite_client: LiteClientPort,
        frontend_client: FrontendPort,
        fields_loader: JiraFieldsLoader,
        vocab: VocabLookup,
        report_synthesiser: ReportSynthesiser,
        delivery_channel: BaseDeliveryChannel,
        settings: Settings,
        renderer: ReportRenderer | None = None,
    ) -> None:
        self._sessions = session_store
        self._conventions = conventions_store
        self._classifier = intent_classifier
        self._clarifier = clarifier
        self._lite = lite_client
        self._frontend = frontend_client
        self._fields_loader = fields_loader
        self._vocab = vocab
        self._synthesiser = report_synthesiser
        self._delivery = delivery_channel
        self._settings = settings
        self._renderer = renderer

    async def handle_query(
        self,
        *,
        query: str,
        session_id: str,
        clarification_answer: str | None = None,
        elicit: ElicitFn | None = None,
        limit: int | None = None,
        show_in_ui: bool = False,
    ) -> QueryResponse:
        log = logger.bind(session_id=session_id)
        session = await self._sessions.get(session_id) or SessionState(session_id=session_id)

        if session.state is ClarificationState.AWAITING_CLARIFICATION:
            if clarification_answer:
                query = session.pending_query or query
                await self._resolve_and_learn(
                    session, query, session.pending_terms, clarification_answer, log
                )
                session.transition(ClarificationState.RESOLVED)
            elif query == session.pending_query and session.pending_question:
                # Host repeated the call without an answer: re-issue the cached question.
                await self._sessions.set(session)
                return self._question_response(session_id, session.pending_question)
            else:
                # A different query arrived mid-clarification: the user moved on.
                log.info("clarification_abandoned", new_query=query)
                session = SessionState(session_id=session_id)

        terms = self._classifier.detect(query)
        active, unresolved = await self._split_resolved(session, terms)
        errors: list[str] = []

        if unresolved and session.clarification_rounds >= self._settings.clarification.max_rounds:
            log.warning("clarification_rounds_exhausted", terms=unresolved)
            errors.append(
                "clarification rounds exhausted; dispatched with unresolved terms: "
                + ", ".join(unresolved)
            )
            unresolved = []

        if unresolved:
            try:
                needed = await self._clarifier.formulate_question(
                    query=query,
                    terms=unresolved,
                    vocab=self._vocab.entries_for(unresolved),
                    conventions=await self._conventions.list_all(),
                    fields=self._fields_loader.load_fields()[
                        : self._settings.clarification.max_fields_in_prompt
                    ],
                )
            except ClarificationError as exc:
                # Degrade rather than fail: dispatch the raw query with a warning.
                log.error("clarifier_failed", error=str(exc))
                errors.append(f"clarification unavailable: {exc}")
                return await self._dispatch(session, query, active, limit, errors, show_in_ui, log)

            session.clarification_rounds += 1
            answer = await elicit(needed.question) if elicit is not None else None
            if answer:
                await self._resolve_and_learn(session, query, unresolved, answer, log)
                for term in unresolved:
                    if term in session.resolutions:
                        active[term] = session.resolutions[term]
                session.transition(ClarificationState.RESOLVED)
            else:
                # Elicitation unsupported or declined: fall back to the session round-trip.
                session.transition(ClarificationState.AWAITING_CLARIFICATION)
                session.pending_query = query
                session.pending_terms = unresolved
                session.pending_question = needed.question
                await self._sessions.set(session)
                log.info("clarification_question_issued", terms=unresolved)
                return self._question_response(session_id, needed.question)

        return await self._dispatch(session, query, active, limit, errors, show_in_ui, log)

    async def _split_resolved(
        self, session: SessionState, terms: list[str]
    ) -> tuple[dict[str, TermResolution], list[str]]:
        """Partition detected terms into already-resolved and needing clarification."""
        active: dict[str, TermResolution] = {}
        unresolved: list[str] = []
        for term in terms:
            if term in session.resolutions:
                active[term] = session.resolutions[term]
                continue
            convention = await self._conventions.get(INSTANCE_DEFAULT_PROJECT, term)
            if convention is not None:
                active[term] = TermResolution(
                    term=term,
                    resolution_key=convention.resolution_key,
                    jql_hint=convention.jql_hint,
                    source="convention",
                )
            else:
                unresolved.append(term)
        return active, unresolved

    async def _resolve_and_learn(
        self,
        session: SessionState,
        query: str,
        terms: list[str],
        answer: str,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        """Turn a user answer into term resolutions; remember them for the whole team."""
        resolved = await self._clarifier.resolve_answer(
            query=query,
            terms=terms,
            answer=answer,
            vocab=self._vocab.entries_for(terms),
        )
        for item in resolved.resolutions:
            session.resolutions[item.term.lower()] = TermResolution(
                term=item.term.lower(),
                resolution_key=item.resolution_key,
                jql_hint=item.jql_hint,
                source="clarification",
            )
            # Learned once, never asked again. query_jira has no project scope,
            # so conventions land at the instance-default level.
            await self._conventions.set(
                Convention(
                    project=INSTANCE_DEFAULT_PROJECT,
                    term=item.term.lower(),
                    resolution_key=item.resolution_key,
                    jql_hint=item.jql_hint,
                )
            )
        log.info("conventions_learned", terms=[r.term for r in resolved.resolutions])

    async def _dispatch(
        self,
        session: SessionState,
        query: str,
        active: dict[str, TermResolution],
        limit: int | None,
        errors: list[str],
        show_in_ui: bool,
        log: structlog.stdlib.BoundLogger,
    ) -> QueryResponse:
        """Send the disambiguated query to the backend and assemble the tool response."""
        applied = [
            AppliedConvention(term=r.term, jql_hint=r.jql_hint, source=r.source)
            for r in active.values()
        ]
        enriched = query
        if active:
            hints = "; ".join(f"{r.term} -> {r.jql_hint}" for r in active.values())
            enriched = f"{query} (interpretation hints: {hints})"

        session.transition(ClarificationState.DISPATCHED)
        session.pending_query = None
        session.pending_terms = []
        session.pending_question = None
        session.clarification_rounds = 0
        await self._sessions.set(session)

        try:
            result = await self._lite.query(enriched, limit=limit)
        except LiteBackendError as exc:
            error_msg = str(exc)
            log.error("lite_dispatch_failed", error=error_msg)
            is_transport = error_msg.startswith("backend unreachable after retries")
            if active and not is_transport:
                # Execution error with active term interpretations: the interpretation is
                # likely wrong. Ask the user to correct or rephrase rather than silently
                # failing - a JQL execution failure is a definite mis-interpretation signal.
                hints = "; ".join(f'"{r.term}" as {r.jql_hint}' for r in active.values())
                question = (
                    f"The query could not be executed: {error_msg[:300]}. "
                    f"I applied these interpretations: {hints}. "
                    f"If any are wrong, please clarify or rephrase your query."
                )
                log.info(
                    "dispatch_failed_asking_for_rephrasing",
                    applied_terms=list(active.keys()),
                )
                failure = QueryResponse(
                    session_id=session.session_id,
                    requires_user_input=True,
                    clarification_question=question,
                    applied_conventions=applied,
                    errors=[*errors, error_msg],
                )
            else:
                failure = QueryResponse(
                    session_id=session.session_id,
                    applied_conventions=applied,
                    errors=[*errors, error_msg],
                )
            return await self._with_report(query, failure, log)

        log.info("query_dispatched", jql=result.jql, total=result.total, shown=result.shown)

        if result.total == 0:
            jql_detail = f" (JQL used: `{result.jql}`)" if result.jql else ""
            hints_detail = ""
            if active:
                hints = "; ".join(f'"{r.term}" -> {r.jql_hint}' for r in active.values())
                hints_detail = f" I applied these field interpretations: {hints}."
            question = (
                f"The query returned zero results{jql_detail}.{hints_detail} "
                "This usually means a field name or field value does not match what is "
                "configured on your Jira instance. Could you please verify: "
                "(1) the exact field names available on your Jira instance, "
                "(2) the exact allowed values for those fields (e.g. status names, priority names, "
                "labels, issue types), and (3) the project key? "
                "Please rephrase your query with the correct field names and values, "
                "or let me know which part to correct."
            )
            log.info("zero_results_asking_for_clarification", jql=result.jql)
            zero_response = QueryResponse(
                session_id=session.session_id,
                requires_user_input=True,
                clarification_question=question,
                jql=result.jql,
                total=0,
                shown=0,
                applied_conventions=applied,
                errors=errors,
            )
            return await self._with_report(query, zero_response, log)

        ui_injected = False
        if show_in_ui:
            if result.jql:
                # Best-effort display in the live browser UI: the trailing /raw flag makes
                # the bridge run the already-generated JQL without a second LLM pass.
                ack = await self._frontend.inject(f"{result.jql} /raw")
                ui_injected = ack.accepted
                if not ack.accepted:
                    errors = [*errors, f"chart not shown in UI: {ack.detail or 'rejected'}"]
            else:
                errors = [*errors, "chart not shown in UI: no JQL was generated"]

        response = QueryResponse(
            session_id=session.session_id,
            answer=result.answer,
            jql=result.jql,
            total=result.total,
            shown=result.shown,
            display_fields=result.display_fields,
            issues=result.issues,
            chart_spec=result.chart_spec,
            ui_injected=ui_injected,
            applied_conventions=applied,
            errors=errors,
        )
        return await self._with_report(query, response, log)

    async def _with_report(
        self, query: str, response: QueryResponse, log: structlog.stdlib.BoundLogger
    ) -> QueryResponse:
        """Write the human-verifiable markdown report; best-effort, never fails the query."""
        if not self._settings.delivery.enabled:
            log.info("report_skipped", reason="delivery disabled")
            return response
        session_slug = re.sub(r"[^A-Za-z0-9_-]", "-", response.session_id)[:40]
        report_id = f"{datetime.now(UTC):%Y%m%d_%H%M%S}_{session_slug}_{uuid4().hex[:8]}"
        try:
            content = self._synthesiser.build_query_report(query=query, response=response)
            location = await self._delivery.deliver(report_id=report_id, content=content)
        except (OSError, NetraError) as exc:
            log.warning("report_delivery_failed", error=str(exc))
            return response.model_copy(
                update={"errors": [*response.errors, f"report not written: {exc}"]}
            )
        log.debug("report_written", location=location)
        view_url = self._renderer.build_view_url(report_id) if self._renderer else None
        return response.model_copy(update={"report_path": location, "view_url": view_url})

    @staticmethod
    def _question_response(session_id: str, question: str) -> QueryResponse:
        return QueryResponse(
            session_id=session_id,
            requires_user_input=True,
            clarification_question=question,
        )
