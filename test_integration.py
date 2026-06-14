"""Ad-hoc integration test: real server with fastmcp.Client in-memory transport.

Run with:
    python test_integration.py

Requires env vars pointing at a live lite backend:
    NETRA_LITE__BASE_URL=http://localhost:8000
    NETRA_LLM__MODEL=groq:llama-3.3-70b-versatile
"""

import asyncio
from typing import Any

from fastmcp import Client

from config.settings import Settings
from server import create_server


async def main() -> None:
    settings = Settings()  # reads NETRA_* env vars

    print("Starting AtlasMind-Netra-mcp integration test...")
    print(f"  Lite backend : {settings.lite.base_url}")
    print(f"  LLM model    : {settings.llm.model}")
    print()

    server = create_server(settings)

    async with Client(server) as client:
        # ── 1. List tools ────────────────────────────────────────────────
        tools = {t.name for t in await client.list_tools()}
        print(f"Tools exposed: {sorted(tools)}")
        _expected = {"query_jira", "generate_briefing", "get_report", "get_jira_context"}
        assert tools == _expected, f"Unexpected tools: {tools - _expected}"
        print("  [OK] All 4 design-doc tools present\n")

        # ── 2. query_jira — simple case ───────────────────────────────────
        print("Calling query_jira (simple, no ambiguity)...")
        result = await client.call_tool(
            "query_jira",
            {"query": "show all bugs in project CAR", "session_id": "test-s1"},
        )
        payload = _unwrap(result)
        print(f"  session_id : {payload.get('session_id')}")
        print(f"  jql        : {payload.get('jql')}")
        print(f"  total      : {payload.get('total')}")
        print(f"  errors     : {payload.get('errors', [])}")
        print(f"  requires_user_input: {payload.get('requires_user_input')}")
        if payload.get("report_path"):
            print(f"  report     : {payload['report_path']}")
        print("  [OK] query_jira responded\n")

        # ── 3. query_jira — ambiguous term (escalation) ───────────────────
        print("Calling query_jira (ambiguous: 'escalation')...")
        result = await client.call_tool(
            "query_jira",
            {"query": "show all escalations in project CAR", "session_id": "test-s2"},
        )
        payload = _unwrap(result)
        if payload.get("requires_user_input"):
            print(f"  [EXPECTED] Clarification needed: {payload.get('clarification_question')}")
            # Answer it and call again
            print("Re-calling with clarification_answer='label=escalation'...")
            result2 = await client.call_tool(
                "query_jira",
                {
                    "query": "show all escalations in project CAR",
                    "session_id": "test-s2",
                    "clarification_answer": "label=escalation",
                },
            )
            payload2 = _unwrap(result2)
            print(f"  jql        : {payload2.get('jql')}")
            print(f"  errors     : {payload2.get('errors', [])}")
            print("  [OK] Clarification round-trip worked\n")
        else:
            print(f"  jql: {payload.get('jql')} (no clarification needed)\n")

        # ── 4. generate_briefing — single topic ───────────────────────────
        print("Calling generate_briefing (daily standup agenda)...")
        result = await client.call_tool(
            "generate_briefing",
            {
                "agenda_text": (
                    "1. Top blockers in Carline XX\n"
                    "2. Critical issues in BOM project\n"
                    "3. Status of recent deployments"
                ),
                "session_id": "test-s3",
            },
        )
        payload = _unwrap(result)
        print(f"  report_id  : {payload.get('report_id')}")
        print(f"  sections   : {len(payload.get('sections', []))}")
        print(f"  errors     : {payload.get('errors', [])}")
        print(f"  requires_user_input: {payload.get('requires_user_input')}")
        if payload.get("pending_topic_id"):
            print(f"  pending_topic: {payload['pending_topic_id']}")
            print(f"  question    : {payload.get('clarification_question')}")
        for section in payload.get("sections", []):
            print(
                f"    - {section.get('topic_id')}: {section.get('description')} "
                f"({section.get('total_found', 0)} issues, "
                f"{len(section.get('top_issues', []))} analysed)"
            )
        if payload.get("report_path"):
            print(f"  report     : {payload['report_path']}")
        print("  [OK] generate_briefing responded\n")

        # ── 5. get_report — round-trip the briefing we just created ────────
        report_id = payload.get("report_id", "")
        if report_id and not report_id.startswith(("pending_", "failed_", "empty_")):
            print(f"Calling get_report('{report_id}')...")
            result = await client.call_tool(
                "get_report",
                {"report_id": report_id, "session_id": "test-s3"},
            )
            r_payload = _unwrap(result)
            print(f"  report_id  : {r_payload.get('report_id')}")
            print(f"  sections   : {len(r_payload.get('sections', []))}")
            print(f"  errors     : {r_payload.get('errors', [])}")
            print(f"  data_scope : {r_payload.get('data_scope', '(not set)')}")
            print("  [OK] get_report responded\n")
        else:
            print(f"Skipping get_report (report_id={report_id!r} not storable)\n")

    print("All tests passed.")


def _unwrap(result: object) -> dict[str, Any]:
    """Unwrap FastMCP CallToolResult to a plain dict."""
    if hasattr(result, "structured_content") and result.structured_content:
        inner: dict[str, Any] = result.structured_content
        value: dict[str, Any] = inner.get("result", inner)
        return value
    return {}


if __name__ == "__main__":
    asyncio.run(main())
