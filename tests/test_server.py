"""One integration test per MCP tool over FastMCP's in-memory transport."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from config.settings import Settings
from core.orchestrator import ElicitFn
from models.responses import QueryResponse
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


@pytest.fixture
def fake_orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def server(settings: Settings, fake_orchestrator: FakeOrchestrator) -> Any:
    return create_server(settings=settings, orchestrator=fake_orchestrator)


async def test_exposes_exactly_the_four_design_doc_tools(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert tools == {"query_jira", "generate_briefing", "get_report", "get_jira_context"}


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
    payload = payload.get("result", payload)  # tolerate result-wrapping across fastmcp versions
    assert payload["jql"] == "project = CAR"
    assert payload["session_id"] == "s1"
    assert fake_orchestrator.calls[0]["limit"] == 10
    assert fake_orchestrator.calls[0]["show_in_ui"] is True


async def test_generate_briefing_is_milestone_3_stub(server: Any) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match="not implemented"):
            await client.call_tool(
                "generate_briefing", {"agenda_text": "daily", "session_id": "s1"}
            )


async def test_get_report_is_milestone_3_stub(server: Any) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match="not implemented"):
            await client.call_tool("get_report", {"report_id": "r1", "session_id": "s1"})


async def test_get_jira_context_is_stub(server: Any) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match="not implemented"):
            await client.call_tool("get_jira_context", {})
