"""FastMCP entrypoint and composition root (design doc: server.py).

The entire object graph is wired in build_orchestrator/create_server and nowhere else
(dependency injection, Coding Guidelines Rule 3). Four MCP tools are exposed; in
Milestone 1 only query_jira is implemented - the others raise a typed not-implemented
error naming their milestone.

TODO(Milestone 3, design doc "Core principle: one renderer"): add REST GET /reports/{id}.
"""

import logging
from pathlib import Path

import httpx
import structlog
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from briefings.delivery import build_delivery_channel
from config.settings import LogSettings, Settings
from core.atlasmind_lite_client import AtlasMindLiteClient
from core.clarifier import Clarifier
from core.frontend_bridge_client import FrontendBridgeClient
from core.intent_classifier import IntentClassifier
from core.jira_fields_loader import JiraFieldsLoader
from core.orchestrator import ElicitFn, Orchestrator, QueryHandler
from core.report_synthesiser import ReportSynthesiser
from core.vocab_lookup import VocabLookup
from memory.conventions_store import JsonFileConventionsStore
from memory.session_store import InMemorySessionStore
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


def build_orchestrator(settings: Settings) -> Orchestrator:
    """Wire the Milestone 1 object graph from settings."""
    vocab = VocabLookup(settings.clarification.vocab_path)
    http = httpx.AsyncClient(
        base_url=settings.lite.base_url,
        timeout=settings.lite.timeout_seconds,
    )
    frontend_http = httpx.AsyncClient(
        base_url=settings.frontend.base_url,
        timeout=settings.frontend.timeout_seconds,
    )
    return Orchestrator(
        session_store=InMemorySessionStore(ttl_seconds=settings.session.ttl_seconds),
        conventions_store=JsonFileConventionsStore(settings.clarification.conventions_path),
        intent_classifier=IntentClassifier(vocab),
        clarifier=Clarifier(
            model=settings.llm.model,
            prompt_path=settings.clarification.prompt_path,
            retries=settings.llm.retries,
        ),
        lite_client=AtlasMindLiteClient(http, settings.lite),
        frontend_client=FrontendBridgeClient(frontend_http, settings.frontend),
        fields_loader=JiraFieldsLoader(
            settings.clarification.jira_fields_path,
            settings.clarification.allowed_values_path,
        ),
        vocab=vocab,
        report_synthesiser=ReportSynthesiser(max_issues=settings.delivery.max_issues),
        delivery_channel=build_delivery_channel(settings.delivery),
        settings=settings,
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
    settings: Settings | None = None, orchestrator: QueryHandler | None = None
) -> FastMCP:
    """Build the FastMCP app. `orchestrator` is injectable for tests."""
    settings = settings or Settings()
    orch = orchestrator or build_orchestrator(settings)
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

        Set `show_in_ui: true` only when the user asks to see the result (table/chart)
        rendered in their AtlasMind browser window; it requires an open browser session
        and must remain opt-in per call.
        """
        elicit = _make_elicit(ctx) if ctx is not None else None
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
    ) -> BriefingResponse:
        """Meeting agenda -> topic decomposition -> per-topic clarification -> multi-query
        fan-out -> issue analysis -> ranked, cited briefing with chart_specs + view_url.

        Decomposition happens SERVER-SIDE so the same agenda produces the same briefing
        structure regardless of which MCP host called it.
        """
        raise ToolError("generate_briefing is not implemented yet (design doc Milestone 3).")

    @mcp.tool
    async def get_report(report_id: str, session_id: str) -> ReportResponse:
        """Retrieve a previously generated report as structured JSON + view_url.

        Rendering and export (PNG/PDF/clipboard) happen in AtlasMind-frontendUI at the
        view_url - this tool never returns rendered binaries.
        """
        raise ToolError("get_report is not implemented yet (design doc Milestone 3).")

    @mcp.tool
    async def get_jira_context(
        include_fields: bool = True, include_projects: bool = True
    ) -> JiraContextResponse:
        """Jira instance metadata: projects, fields, priorities, issue types."""
        raise ToolError("get_jira_context is not implemented yet (planned after Milestone 1).")

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
    ana = settings.analysis
    for attr in ("prompt_path", "ranking_rule_path"):
        p = getattr(ana, attr)
        if not p.is_absolute():
            setattr(ana, attr, root / p)


def main() -> None:
    settings = Settings()
    _resolve_relative_paths(settings)
    configure_logging(settings.log)
    logger.info(
        "server_starting",
        transport=settings.server.transport,
        output_dir=str(settings.delivery.output_dir),
        log_file=str(settings.log.log_file) if settings.log.log_file else None,
    )
    server = create_server(settings)
    if settings.server.transport == "streamable-http":
        server.run(transport="http", host=settings.server.host, port=settings.server.port)
    else:
        server.run()  # stdio (development default)


if __name__ == "__main__":
    main()
