"""AtlasMindLiteClient against the contract, via httpx.MockTransport (no live network)."""

import json
from collections.abc import Callable

import httpx
import pytest

from config.settings import LiteSettings
from core.atlasmind_lite_client import AtlasMindLiteClient
from core.exceptions import LiteBackendError
from models.lite import LiteQueryResult

OK_ISSUE_DETAILS_PAYLOAD = {
    "issues": [
        {
            "key": "CAR-101",
            "summary": "Engine stall on cold start",
            "priority": "Critical",
            "assignee": "jdoe",
            "due_date": "2026-07-15",
            "fix_versions": ["v2.3"],
            "flagged": True,
            "comments": [
                {
                    "id": "10042",
                    "author": "jdoe",
                    "body": "Still waiting on vendor response.",
                    "created": "2026-06-10T08:30:00.000+0000",
                    "updated": "2026-06-10T08:30:00.000+0000",
                }
            ],
            "links": [
                {
                    "type": "blocks",
                    "direction": "outward",
                    "linked_issue_key": "CAR-205",
                    "linked_issue_summary": "Engine sign-off",
                }
            ],
            "changelog": [
                {
                    "field": "status",
                    "from_value": "In Progress",
                    "to_value": "Blocked",
                    "author": "jdoe",
                    "timestamp": "2026-05-25T10:00:00.000+0000",
                }
            ],
            "extra_future_field": "must be tolerated",
        }
    ],
    "not_found": [],
    "error": None,
}

OK_PAYLOAD = {
    "type": "jql",
    "profile": "work",
    "jira_base_url": "https://jira.example.com",
    "jira_type": "server",
    "answer": "Found 1 result(s).",
    "jql": "project = CAR AND labels = escalation",
    "total": 1,
    "shown": 1,
    "examined": 1,
    "display_fields": ["Key", "Summary"],
    "issues": [{"key": "CAR-1", "summary": "Brakes"}],
    "chart_spec": None,
    "filters": {"status": ["Open"]},
    "meta": {"model_name": "Groq: llama", "llm_backend": "groq", "llm_timeout": 300},
    "token_usage": {"total_tokens": 100},
    "some_future_field": "must be tolerated",
}


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], max_retries: int = 3
) -> AtlasMindLiteClient:
    settings = LiteSettings(
        max_retries=max_retries, retry_initial_seconds=0.0, retry_max_seconds=0.0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=settings.base_url)
    return AtlasMindLiteClient(http, settings)


async def test_query_success_parses_contract_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_PAYLOAD)

    result = await make_client(handler).query("show escalations", jira_token="pat-123")

    assert result.jql == "project = CAR AND labels = escalation"
    assert result.total == 1
    assert result.issues[0]["key"] == "CAR-1"
    body = json.loads(seen[0].content)
    assert body["query"] == "show escalations"
    assert body["limit"] == 50  # default_limit applied
    assert body["request_id"]  # uuid generated per call
    assert seen[0].headers["X-Jira-Token"] == "pat-123"
    assert "X-Jira-Email" not in seen[0].headers  # absent inputs send no header


async def test_503_is_retried_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"detail": "model not initialised"})
        return httpx.Response(200, json=OK_PAYLOAD)

    result = await make_client(handler).query("q")
    assert result.total == 1
    assert calls["n"] == 2


async def test_persistent_503_raises_after_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"detail": "still booting"})

    with pytest.raises(LiteBackendError, match="unreachable after retries"):
        await make_client(handler, max_retries=3).query("q")
    assert calls["n"] == 3


async def test_transport_error_is_retried_then_raises() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LiteBackendError, match="unreachable after retries"):
        await make_client(handler, max_retries=2).query("q")
    assert calls["n"] == 2


async def test_4xx_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={"detail": "bad body"})

    with pytest.raises(LiteBackendError, match="HTTP 422"):
        await make_client(handler).query("q")
    assert calls["n"] == 1


async def test_in_band_error_answer_raises() -> None:
    payload = {"type": "general", "answer": "Error: Jira connection failed: timeout"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(LiteBackendError, match="Jira connection failed"):
        await make_client(handler).query("q")


async def test_contract_violation_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "bogus-kind"})

    with pytest.raises(LiteBackendError, match="does not match contract"):
        await make_client(handler).query("q")


async def test_health_true_and_false() -> None:
    assert await make_client(lambda r: httpx.Response(200, json={"status": "ok"})).health()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert not await make_client(down).health()


# ---------------------------------------------------------------------------
# POST /issue_details tests
# ---------------------------------------------------------------------------


async def test_get_issue_details_success_parses_contract_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_ISSUE_DETAILS_PAYLOAD)

    result = await make_client(handler).get_issue_details(["CAR-101"])

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.key == "CAR-101"
    assert issue.priority == "Critical"
    assert issue.flagged is True
    assert len(issue.comments) == 1
    assert issue.comments[0].id == "10042"
    assert len(issue.links) == 1
    assert issue.links[0].linked_issue_key == "CAR-205"
    assert len(issue.changelog) == 1
    assert issue.changelog[0].to_value == "Blocked"
    assert result.not_found == []

    body = json.loads(seen[0].content)
    assert body["issue_keys"] == ["CAR-101"]
    assert body["request_id"]
    assert body["comments_limit"] == 20  # default applied
    assert seen[0].url.path == "/issue_details"


async def test_get_issue_details_not_found_passthrough() -> None:
    payload = {"issues": [], "not_found": ["CAR-999"], "error": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = await make_client(handler).get_issue_details(["CAR-999"])
    assert result.not_found == ["CAR-999"]
    assert result.issues == []


async def test_get_issue_details_in_band_error_raises() -> None:
    payload = {"issues": [], "not_found": [], "error": "Error: Jira connection failed"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(LiteBackendError, match="Jira connection failed"):
        await make_client(handler).get_issue_details(["CAR-101"])


async def test_get_issue_details_503_retried_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"detail": "model not initialised"})
        return httpx.Response(200, json=OK_ISSUE_DETAILS_PAYLOAD)

    result = await make_client(handler).get_issue_details(["CAR-101"])
    assert len(result.issues) == 1
    assert calls["n"] == 2


async def test_get_issue_details_transport_error_raises_after_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LiteBackendError, match="unreachable after retries"):
        await make_client(handler, max_retries=2).get_issue_details(["CAR-101"])
    assert calls["n"] == 2


async def test_get_issue_details_422_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={"detail": "bad body"})

    with pytest.raises(LiteBackendError, match="HTTP 422"):
        await make_client(handler).get_issue_details(["CAR-101"])
    assert calls["n"] == 1


async def test_get_issue_details_batches_large_key_list() -> None:
    """Keys > 50 are split into 50-key batches; results are merged."""
    batch_requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batch_requests.append(body["issue_keys"])
        return httpx.Response(
            200, json={"issues": [], "not_found": body["issue_keys"], "error": None}
        )

    keys = [f"CAR-{i}" for i in range(55)]
    result = await make_client(handler).get_issue_details(keys)

    assert len(batch_requests) == 2
    assert len(batch_requests[0]) + len(batch_requests[1]) == 55
    assert max(len(b) for b in batch_requests) == 50
    assert len(result.not_found) == 55


async def test_get_issue_details_batch_error_propagates() -> None:
    """If any batch returns an HTTP error, LiteBackendError is raised."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad body"})

    with pytest.raises(LiteBackendError, match="HTTP 422"):
        await make_client(handler).get_issue_details([f"CAR-{i}" for i in range(55)])


def test_lite_query_result_normalises_jira_type_enum_prefix() -> None:
    """Backend may return 'JiraAuthType.server' instead of 'server' - validator strips prefix."""
    result = LiteQueryResult.model_validate({"jira_type": "JiraAuthType.server"})
    assert result.jira_type == "server"

    result = LiteQueryResult.model_validate({"jira_type": "JiraAuthType.cloud"})
    assert result.jira_type == "cloud"


def test_lite_query_result_accepts_canonical_jira_type_values() -> None:
    """Plain 'cloud' and 'server' still pass through unchanged."""
    assert LiteQueryResult.model_validate({"jira_type": "server"}).jira_type == "server"
    assert LiteQueryResult.model_validate({"jira_type": "cloud"}).jira_type == "cloud"
    assert LiteQueryResult.model_validate({"jira_type": None}).jira_type is None
