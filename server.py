"""FastMCP entrypoint and composition root.

The entire object graph is wired in build_orchestrator/build_briefing_orchestrator and
nowhere else (dependency injection). Four MCP tools are exposed.
"""

import asyncio
import logging
import re
import uuid
from pathlib import Path

import httpx
import structlog
import structlog.contextvars
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic_ai.models import Model
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from briefings.delivery import build_delivery_channel
from config.settings import LogSettings, Settings
from confluence.models.response import ConfluenceContextResponse
from core.agenda_decomposer import load_agenda_decomposer
from core.atlasmind_lite_client import AtlasMindLiteClient
from core.briefing_orchestrator import BriefingHandler, BriefingOrchestrator
from core.clarifier import Clarifier
from core.frontend_bridge_client import FrontendBridgeClient
from core.intent_classifier import IntentClassifier
from core.issue_analyser import build_issue_analyser
from core.jira_fields_loader import JiraFieldsLoader
from core.llm_provider import create_llm_provider
from core.orchestrator import ElicitFn, Orchestrator, QueryHandler
from core.ranking_engine import RankingEngine, load_ranking_rule
from core.report_renderer import ReportRenderer
from core.report_synthesiser import ReportSynthesiser
from core.vocab_lookup import VocabLookup
from memory.briefing_session_store import BaseBriefingSessionStore, InMemoryBriefingSessionStore
from memory.conventions_store import JsonFileConventionsStore
from memory.session_store import BaseSessionStore, InMemorySessionStore
from memory.store_factory import create_briefing_session_store, create_session_store
from models.responses import (
    BriefingResponse,
    JiraContextResponse,
    QueryResponse,
    ReportResponse,
)

logger = structlog.get_logger(__name__)


def configure_logging(settings: LogSettings) -> None:
    """structlog: console output for dev, JSON lines for production (Rule 5)."""
    level = logging.getLevelNamesMapping().get(settings.level.upper(), logging.INFO)
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.json_logs
        else structlog.dev.ConsoleRenderer()
    )
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
    json_renderer = structlog.processors.JSONRenderer()
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, json_renderer],
    )
    # Bridge stdlib logging (httpx, asyncio, etc.) through structlog.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.root.addHandler(console_handler)
    # Always write JSON lines to the log file for later review.
    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        file_handler.setFormatter(file_formatter)
        logging.root.addHandler(file_handler)
    logging.root.setLevel(level)


def build_orchestrator(
    settings: Settings,
    llm_model: Model | str | None = None,
    session_store: BaseSessionStore | None = None,
    renderer: ReportRenderer | None = None,
) -> tuple[Orchestrator, AtlasMindLiteClient]:
    """Wire the query_jira object graph from settings.

    Returns the orchestrator and its lite client so the caller can share the client with
    the briefing pipeline (avoids a second connection pool to the same backend).
    Accepts an optional pre-built llm_model to avoid a second provider instantiation when
    both orchestrators are created together in create_server.
    Accepts an optional session_store so create_server can inject the configured backend;
    falls back to InMemorySessionStore when called directly (e.g. from tests).
    Accepts an optional renderer so create_server can share it with the HTTP route handler.
    """
    vocab = VocabLookup(settings.clarification.vocab_path)
    http = httpx.AsyncClient(
        base_url=settings.lite.base_url,
        timeout=settings.lite.timeout_seconds,
    )
    frontend_http = httpx.AsyncClient(
        base_url=settings.frontend.base_url,
        timeout=settings.frontend.timeout_seconds,
    )
    lite_client = AtlasMindLiteClient(http, settings.lite)
    _model = llm_model if llm_model is not None else create_llm_provider(settings.llm).make_model()
    return Orchestrator(
        session_store=session_store
        or InMemorySessionStore(ttl_seconds=settings.session.ttl_seconds),
        conventions_store=JsonFileConventionsStore(settings.clarification.conventions_path),
        intent_classifier=IntentClassifier(vocab),
        clarifier=Clarifier(
            model=_model,
            prompt_path=settings.clarification.prompt_path,
            retries=settings.llm.retries,
        ),
        lite_client=lite_client,
        frontend_client=FrontendBridgeClient(frontend_http, settings.frontend),
        fields_loader=JiraFieldsLoader(
            settings.clarification.jira_fields_path,
            settings.clarification.allowed_values_path,
        ),
        vocab=vocab,
        report_synthesiser=ReportSynthesiser(max_issues=settings.delivery.max_issues),
        delivery_channel=build_delivery_channel(settings.delivery),
        settings=settings,
        renderer=renderer,
    ), lite_client


def build_briefing_orchestrator(
    settings: Settings,
    query_handler: QueryHandler,
    lite_client: AtlasMindLiteClient | None = None,
    llm_model: Model | str | None = None,
    briefing_session_store: BaseBriefingSessionStore | None = None,
) -> BriefingOrchestrator:
    """Wire the generate_briefing object graph from settings.

    Accepts a shared lite_client to avoid opening a second connection pool to the same backend.
    Accepts an optional pre-built llm_model to avoid a second provider instantiation when
    both orchestrators are created together in create_server.
    Accepts an optional briefing_session_store so create_server can inject the configured
    backend; falls back to InMemoryBriefingSessionStore when called directly (e.g. tests).
    """
    if lite_client is None:
        http = httpx.AsyncClient(
            base_url=settings.lite.base_url,
            timeout=settings.lite.timeout_seconds,
        )
        lite_client = AtlasMindLiteClient(http, settings.lite)
    _model = llm_model if llm_model is not None else create_llm_provider(settings.llm).make_model()
    rule = load_ranking_rule(settings.analysis.ranking_rule_path)

    intent_analyser = None
    confluence_client = None
    context_extractor = None
    if settings.confluence.base_url and settings.confluence.api_token:
        from confluence.client.client import ConfluenceClient
        from confluence.extraction.extractor import build_context_extractor
        from core.query_intent_analyser import build_query_intent_analyser
        intent_analyser = build_query_intent_analyser(
            _model, settings.confluence, retries=settings.llm.retries
        )
        confluence_client = ConfluenceClient(
            base_url=settings.confluence.base_url,
            api_token=settings.confluence.api_token.get_secret_value(),
            email=settings.confluence.email,
            search_limit=settings.confluence.search_limit,
            page_cache_ttl_seconds=settings.confluence.page_cache_ttl_seconds,
            content_max_chars=settings.confluence.content_max_chars,
        )
        context_extractor = build_context_extractor(
            _model, settings.confluence, retries=settings.llm.retries
        )
        logger.info(
            "confluence_enabled",
            base_url=settings.confluence.base_url,
            default_spaces=settings.confluence.default_spaces,
        )

    return BriefingOrchestrator(
        decomposer=load_agenda_decomposer(
            _model,
            settings.analysis.agenda_prompt_path,
            retries=settings.llm.retries,
            max_topics=settings.briefing.max_topics,
        ),
        query_handler=query_handler,
        lite_client=lite_client,
        issue_analyser=build_issue_analyser(
            _model, settings.analysis, retries=settings.llm.retries
        ),
        ranking_engine=RankingEngine(rule),
        report_synthesiser=ReportSynthesiser(max_issues=settings.delivery.max_issues),
        delivery_channel=build_delivery_channel(settings.delivery),
        briefing_sessions=briefing_session_store
        or InMemoryBriefingSessionStore(ttl_seconds=settings.session.ttl_seconds),
        settings=settings,
        intent_analyser=intent_analyser,
        confluence=confluence_client,
        context_extractor=context_extractor,
    )


def _make_elicit(ctx: Context) -> ElicitFn:
    """Adapt FastMCP elicitation to the orchestrator's callback (elicitation-first).

    Returns None on any failure or decline: that triggers the designed fallback, the
    session round-trip (design doc: clarification transport).
    """

    async def _elicit(question: str) -> str | None:
        try:
            # type ignore: fastmcp 3.4 interleaves its elicit() overloads with docstring
            # statements, so mypy only matches the response_type=None overload; the runtime
            # signature accepts type[str] (returns AcceptedElicitation[str]).
            result = await ctx.elicit(question, response_type=str)  # type: ignore[arg-type]
        except Exception as exc:
            # Broad on purpose: host capability errors vary by MCP client, and any failure
            # here means "host cannot elicit" - the fallback path is the designed response.
            logger.warning("elicitation_unsupported", error=str(exc))
            return None
        if getattr(result, "action", None) != "accept":
            return None
        data = getattr(result, "data", None)
        return data if isinstance(data, str) else None

    return _elicit


def create_server(
    settings: Settings | None = None,
    orchestrator: QueryHandler | None = None,
    briefing_orchestrator: BriefingHandler | None = None,
) -> FastMCP:
    """Build the FastMCP app. Orchestrators are injectable for tests."""
    settings = settings or Settings()
    orch: QueryHandler
    shared_lite: AtlasMindLiteClient | None = None
    # Build the LLM model once when either factory needs it; both share it to avoid a
    # second provider instantiation (and a second credential check) for the same settings.
    shared_llm: Model | str | None = None
    if orchestrator is None or briefing_orchestrator is None:
        _provider = create_llm_provider(settings.llm)
        _provider.validate_credentials()
        shared_llm = _provider.make_model()
    renderer = ReportRenderer(settings.server.public_url)
    if orchestrator is None:
        orch, shared_lite = build_orchestrator(
            settings,
            llm_model=shared_llm,
            session_store=create_session_store(settings),
            renderer=renderer,
        )
    else:
        orch = orchestrator
    briefing_orch = briefing_orchestrator or build_briefing_orchestrator(
        settings,
        orch,
        shared_lite,
        llm_model=shared_llm,
        briefing_session_store=create_briefing_session_store(settings),
    )
    mcp: FastMCP = FastMCP(name="AtlasMind-Netra-mcp")

    @mcp.tool
    async def query_jira(
        query: str,
        session_id: str,
        clarification_answer: str | None = None,
        limit: int | None = None,
        show_in_ui: bool = False,
        ctx: Context | None = None,
    ) -> QueryResponse:
        """Natural language -> clarification loop -> JQL -> result + chart_spec.

        This tool returns issue metadata only: key, summary, status, priority,
        assignee, created, updated, due date, and reporter. Comment text, issue
        links, and changelog are NOT included in this response. Do NOT synthesize,
        infer, or speculate about comment content, blocker reasons, or risk details
        beyond the fields present in the returned issue objects.

        IMPORTANT: If the response contains `requires_user_input: true`, present
        `clarification_question` to the user VERBATIM and call this tool again with
        their answer as `clarification_answer`. Do NOT answer the clarification
        question yourself.

        IMPORTANT: When the response contains a non-null `view_url`, always present it to
        the user as a clickable link at the end of your reply, e.g.:
        "View the full report: <view_url>". Do not omit or paraphrase it.

        Set `show_in_ui: true` only when the user asks to see the result (table/chart)
        rendered in their AtlasMind browser window; it requires an open browser session
        and must remain opt-in per call.

        For any multi-topic report - status update, risk review, sprint briefing, or similar -
        use `generate_briefing` instead. It decomposes the agenda automatically, runs all
        queries, and writes a SINGLE combined .md report with per-issue analysis and ranked
        blockers.
        """
        # Item 3: per-invocation log correlation; prevents context leak between requests.
        # contextvars propagate automatically into asyncio.gather() subtasks.
        # TODO M4: bind user_id once per-user token extraction is implemented.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            session_id=session_id,
        )
        _is_stateless = settings.server.transport == "streamable-http"
        elicit = _make_elicit(ctx) if (ctx is not None and not _is_stateless) else None
        return await orch.handle_query(
            query=query,
            session_id=session_id,
            clarification_answer=clarification_answer,
            elicit=elicit,
            limit=limit,
            show_in_ui=show_in_ui,
        )

    @mcp.tool
    async def generate_briefing(
        agenda_text: str,
        session_id: str,
        template_id: str | None = None,
        projects: list[str] | None = None,
        clarification_answer: str | None = None,
        force_refresh: bool = False,
        ctx: Context | None = None,
    ) -> BriefingResponse:
        """Meeting agenda -> topic decomposition -> per-topic clarification -> multi-query
        fan-out -> issue analysis -> ranked, cited briefing with chart_specs + view_url.

        Decomposition happens SERVER-SIDE so the same agenda produces the same briefing
        structure regardless of which MCP host called it.

        This tool returns full issue content (comments, links, changelog) analysed per
        agenda topic. IMPORTANT: if the response contains `requires_user_input: true`,
        present `clarification_question` to the user VERBATIM for the pending topic and
        call this tool again with their answer as `clarification_answer`.

        template_id is reserved for future briefing templates and has no effect yet.

        Set force_refresh=True when the user says "recheck", "try again", "refresh that",
        "I got wrong data", "the data seems off", or otherwise questions the accuracy of a
        previous result. When the response contains a non-empty errors list, surface those
        errors to the user verbatim and offer to retry with force_refresh=True.
        """
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            session_id=session_id,
        )
        _is_stateless = settings.server.transport == "streamable-http"
        elicit = _make_elicit(ctx) if (ctx is not None and not _is_stateless) else None
        return await briefing_orch.generate_briefing(
            agenda_text=agenda_text,
            session_id=session_id,
            projects=projects,
            clarification_answer=clarification_answer,
            elicit=elicit,
            force_refresh=force_refresh,
        )

    @mcp.tool
    async def get_report(report_id: str, session_id: str) -> ReportResponse:
        """Retrieve a previously generated briefing report as structured JSON + view_url.

        Rendering and export (PNG/PDF/clipboard) happen in AtlasMind-frontendUI at the
        view_url - this tool never returns rendered binaries.
        """
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            session_id=session_id,
        )
        try:
            return await briefing_orch.get_briefing_report(report_id, session_id)
        except ValueError as exc:
            # C-1: report_id validation failed (path traversal attempt)
            return ReportResponse(
                report_id=report_id,
                errors=[f"invalid report_id: {exc}"],
            )

    @mcp.tool
    async def search_context(
        query: str,
        session_id: str,
        spaces: list[str] | None = None,
        recency_days: int | None = 30,
        limit: int = 5,
        force_refresh: bool = False,
        page_urls: list[str] | None = None,
    ) -> ConfluenceContextResponse:
        """Search Confluence for pages relevant to a release version, risk topic, or keyword.

        Returns matching pages with Jira issue key mentions, mitigation text, and severity
        signals extracted by LLM. Use for direct Confluence investigation or to find
        relevant context before running generate_briefing.

        generate_briefing also calls this internally when NETRA_CONFLUENCE__BASE_URL is
        configured - you do not need to call it manually before generate_briefing.

        Returns empty result (no error) if Confluence is not configured.

        Set force_refresh=True when the user says "re-search", "refresh that",
        "try again", "the results seem stale", "these pages are outdated", or otherwise
        questions whether the Confluence results are accurate or current.
        When the response contains a non-empty errors list, surface those errors to the
        user and offer to retry with force_refresh=True.

        page_urls: Specific Confluence page URLs to include alongside the keyword search.
          Supports Cloud (.../pages/{id}/...) and Server viewpage (?pageId={id}) formats.
          Unrecognized URL formats are reported in the errors list and skipped gracefully.
          Pinned pages consume from the limit budget first. If len(page_urls) >= limit,
          no CQL keyword-search results are included in the response.
        """
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            session_id=session_id,
        )
        return await briefing_orch.search_context(
            query, spaces, recency_days, limit,
            force_refresh=force_refresh,
            page_urls=page_urls,
        )

    @mcp.tool
    async def get_jira_context(
        include_fields: bool = True, include_projects: bool = True
    ) -> JiraContextResponse:
        """Jira instance metadata: projects, fields, priorities, issue types."""
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        raise ToolError("get_jira_context is not implemented.")

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/report/{report_id}", methods=["GET"])
    async def serve_report(request: Request) -> Response:
        report_id = request.path_params["report_id"]
        # Whitelist only alphanumeric + _ and -; cap at 80 chars (current format is ~35)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}", report_id):
            return Response("Not found", status_code=404)
        md_path = settings.delivery.output_dir / f"{report_id}.md"
        if not await asyncio.to_thread(md_path.exists):
            return Response("Report not found", status_code=404)
        content = await asyncio.to_thread(md_path.read_text, "utf-8")
        return HTMLResponse(
            renderer.render_html(report_id, content),
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    return mcp


def _resolve_relative_paths(settings: Settings) -> None:
    """Anchor all relative paths in settings against the project root.

    Relative defaults work when the server is started from the project root, but break
    in HTTP mode, CF, or any environment where CWD differs. Using __file__ as the anchor
    makes the server startable from any directory.
    """
    root = Path(__file__).parent
    if not settings.delivery.output_dir.is_absolute():
        settings.delivery.output_dir = root / settings.delivery.output_dir
    if settings.log.log_file and not settings.log.log_file.is_absolute():
        settings.log.log_file = root / settings.log.log_file
    clr = settings.clarification
    for attr in ("vocab_path", "prompt_path", "conventions_path"):
        p: Path = getattr(clr, attr)
        if not p.is_absolute():
            setattr(clr, attr, root / p)
    for attr in ("jira_fields_path", "allowed_values_path"):
        p_opt: Path | None = getattr(clr, attr)
        if p_opt is not None and not p_opt.is_absolute():
            setattr(clr, attr, root / p_opt)
    ana = settings.analysis
    for attr in ("prompt_path", "ranking_rule_path", "agenda_prompt_path"):
        p = getattr(ana, attr)
        if not p.is_absolute():
            setattr(ana, attr, root / p)


def _fetch_fields_files(settings: Settings) -> None:
    """Download Jira metadata files from CF static-file URLs to /tmp/ at startup.

    Only runs when the corresponding *_PATH is unset and a *_URL is provided.
    Failures are non-fatal: JiraFieldsLoader degrades gracefully to an empty list.
    """
    clr = settings.clarification
    targets = [
        (clr.jira_fields_url, clr.jira_fields_path, "jira_fields.json", "jira_fields_path"),
        (
            clr.allowed_values_url,
            clr.allowed_values_path,
            "jira_allowed_values.json",
            "allowed_values_path",
        ),
    ]
    for url, existing_path, filename, attr in targets:
        if url and existing_path is None:
            dest = Path("/tmp") / filename
            try:
                resp = httpx.get(url, timeout=15)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                setattr(clr, attr, dest)
                logger.info("jira_fields_fetched", url=url, dest=str(dest))
            except httpx.HTTPError as exc:
                logger.warning("jira_fields_fetch_failed", url=url, error=str(exc))


def main() -> None:
    settings = Settings()
    _resolve_relative_paths(settings)
    _fetch_fields_files(settings)
    configure_logging(settings.log)
    logger.info(
        "server_starting",
        transport=settings.server.transport,
        llm_model=settings.llm.model,
        llm_base_url=settings.llm.base_url,
        output_dir=str(settings.delivery.output_dir),
        log_file=str(settings.log.log_file) if settings.log.log_file else None,
    )
    server = create_server(settings)
    if settings.server.transport == "streamable-http":
        server.run(
            transport="http",
            host=settings.server.host,
            port=settings.server.port,
            stateless_http=True,
            uvicorn_config={"log_config": None},
        )
    else:
        server.run()  # stdio (development default)


if __name__ == "__main__":
    main()
