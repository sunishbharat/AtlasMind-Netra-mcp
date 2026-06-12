"""FrontendBridgeClient: best-effort inject, never raises (httpx.MockTransport)."""

import json
from collections.abc import Callable

import httpx
from pydantic import SecretStr

from config.settings import FrontendSettings
from core.frontend_bridge_client import FrontendBridgeClient


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    api_key: str | None = None,
) -> FrontendBridgeClient:
    settings = FrontendSettings(api_key=SecretStr(api_key) if api_key else None)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=settings.base_url)
    return FrontendBridgeClient(http, settings)


async def test_accepted_injection() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"accepted": True, "request_id": "abc-123"})

    ack = await make_client(handler).inject("project = CAR /raw")

    assert ack.accepted is True
    body = json.loads(seen[0].content)
    assert body["query"] == "project = CAR /raw"
    assert body["request_id"]  # generated when not supplied
    assert "X-API-Key" not in seen[0].headers  # no key configured -> no header


async def test_api_key_forwarded_when_configured() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"accepted": True})

    await make_client(handler, api_key="secret-key").inject("project = CAR /raw")
    assert seen[0].headers["X-API-Key"] == "secret-key"


async def test_ui_busy_returns_rejected_ack() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"accepted": False, "detail": "A query is already in progress."}
        )

    ack = await make_client(handler).inject("project = CAR /raw")
    assert ack.accepted is False
    assert ack.detail is not None and "already in progress" in ack.detail


async def test_no_ui_session_returns_rejected_ack() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"accepted": False, "detail": "No active UI session."})

    ack = await make_client(handler).inject("project = CAR /raw")
    assert ack.accepted is False
    assert ack.detail == "No active UI session."


async def test_bridge_down_degrades_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    ack = await make_client(handler).inject("project = CAR /raw")
    assert ack.accepted is False
    assert ack.detail is not None and "bridge unreachable" in ack.detail


async def test_forbidden_degrades_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    ack = await make_client(handler).inject("project = CAR /raw")
    assert ack.accepted is False
    assert ack.detail == "bridge returned HTTP 403"
