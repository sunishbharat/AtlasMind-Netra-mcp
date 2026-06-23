"""One integration test per MCP tool over FastMCP's in-memory transport."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from config.settings import Settings
from confluence.models.response import ConfluenceContextResponse
from core.orchestrator import ElicitFn
from models.responses import BriefingResponse, QueryResponse, ReportResponse
from server import create_server


class FakeOrchestrator:
    """Implements QueryHandler; echoes what the tool layer passed through."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "query": query,
                "session_id": session_id,
                "clarification_answer": clarification_answer,
                "limit": limit,
                "show_in_ui": show_in_ui,
            }
        )
        return QueryResponse(session_id=session_id, jql="project = CAR", total=1, shown=1)


class FakeBriefingOrchestrator:
    """Implements BriefingHandler; returns canned responses."""

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    async def generate_briefing(
        self,
        *,
        agenda_text: str,
        session_id: str,
        projects: list[str] | None = None,
        clarification_answer: str | None = None,
        elicit: Any = None,
    ) -> BriefingResponse:
        self.generate_calls.append(
            {"agenda_text": agenda_text, "session_id": session_id, "projects": projects}
        )
        return BriefingResponse(report_id="r_test", session_id=session_id)

    async def get_briefing_report(self, report_id: str, session_id: str) -> ReportResponse:
        self.get_calls.append(report_id)
        return ReportResponse(report_id=report_id)

    async def search_context(
        self,
        query: str,
        spaces: list[str] | None = None,
        recency_days: int | None = None,
        limit: int = 5,
    ) -> ConfluenceContextResponse:
        return ConfluenceContextResponse()


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def fake_briefing_orchestrator() -> FakeBriefingOrchestrator:
    return FakeBriefingOrchestrator()


@pytest.fixture
def server(
    settings: Settings,
    fake_orchestrator: FakeOrchestrator,
    fake_briefing_orchestrator: FakeBriefingOrchestrator,
) -> Any:
    return create_server(
        settings=settings,
        orchestrator=fake_orchestrator,
        briefing_orchestrator=fake_briefing_orchestrator,
    )


async def test_exposes_exactly_the_five_design_doc_tools(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert tools == {
        "query_jira",
        "generate_briefing",
        "get_report",
        "get_jira_context",
        "search_context",
    }


async def test_query_jira_round_trip(server: Any, fake_orchestrator: FakeOrchestrator) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "query_jira",
            {
                "query": "list bugs in project CAR",
                "session_id": "s1",
                "limit": 10,
                "show_in_ui": True,
            },
        )
    payload = result.structured_content or {}
    payload = payload.get("result", payload)
    assert payload["jql"] == "project = CAR"
    assert payload["session_id"] == "s1"
    assert fake_orchestrator.calls[0]["limit"] == 10
    assert fake_orchestrator.calls[0]["show_in_ui"] is True


async def test_generate_briefing_routes_to_briefing_orchestrator(
    server: Any, fake_briefing_orchestrator: FakeBriefingOrchestrator
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "generate_briefing",
            {"agenda_text": "daily standup", "session_id": "s1"},
        )
    payload = result.structured_content or {}
    payload = payload.get("result", payload)
    assert payload["report_id"] == "r_test"
    assert fake_briefing_orchestrator.generate_calls[0]["agenda_text"] == "daily standup"


async def test_generate_briefing_passes_projects(
    server: Any, fake_briefing_orchestrator: FakeBriefingOrchestrator
) -> None:
    async with Client(server) as client:
        await client.call_tool(
            "generate_briefing",
            {"agenda_text": "daily", "session_id": "s2", "projects": ["CAR", "BOM"]},
        )
    assert fake_briefing_orchestrator.generate_calls[0]["projects"] == ["CAR", "BOM"]


async def test_get_report_routes_to_briefing_orchestrator(
    server: Any, fake_briefing_orchestrator: FakeBriefingOrchestrator
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_report", {"report_id": "r42", "session_id": "s1"})
    payload = result.structured_content or {}
    payload = payload.get("result", payload)
    assert payload["report_id"] == "r42"
    assert fake_briefing_orchestrator.get_calls == ["r42"]


async def test_get_jira_context_is_stub(server: Any) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match="not implemented"):
            await client.call_tool("get_jira_context", {})
