"""AtlasMindLiteClient against the contract, via httpx.MockTransport (no live network)."""

import json
from collections.abc import Callable

import httpx
import pytest

from config.settings import LiteSettings
from core.atlasmind_lite_client import AtlasMindLiteClient
from core.exceptions import LiteBackendError

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
