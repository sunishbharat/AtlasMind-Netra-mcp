"""HTTP client for the frontendUI bridge server (docs/frontendui_bridge_contract.md).

Adapter pattern: wraps the bridge's /api/mcp/inject endpoint behind typed models. Inject is
strictly best-effort display sugar: every failure mode (UI busy, no browser session, bridge
down, bad key) degrades to InjectAck(accepted=False) - it never raises and is never
retried, so a missed render cannot fail or slow down the underlying query.
"""

from uuid import uuid4

import httpx
import structlog

from config.settings import FrontendSettings
from models.frontend import InjectAck, InjectRequest

logger = structlog.get_logger(__name__)


class FrontendBridgeClient:
    """Async client for POST /api/mcp/inject.

    The httpx.AsyncClient is injected (constructor injection, wired in server.py) and must
    be configured with the bridge base_url and timeout from FrontendSettings.
    """

    def __init__(self, http: httpx.AsyncClient, settings: FrontendSettings) -> None:
        self._http = http
        self._settings = settings

    async def inject(self, query: str, request_id: str | None = None) -> InjectAck:
        """Push a query into the live browser chat window; best-effort, never raises."""
        request = InjectRequest(query=query, request_id=request_id or str(uuid4()))
        headers = (
            {"X-API-Key": self._settings.api_key.get_secret_value()}
            if self._settings.api_key is not None
            else {}
        )
        try:
            response = await self._http.post(
                self._settings.inject_path,
                json=request.model_dump(exclude_none=True),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("ui_inject_unreachable", error=str(exc))
            return InjectAck(accepted=False, detail=f"bridge unreachable: {exc}")

        if response.status_code in (202, 200, 409, 503):
            try:
                ack = InjectAck.model_validate(response.json())
            except ValueError:
                ack = InjectAck(accepted=response.status_code in (200, 202))
            if not ack.accepted:
                logger.warning("ui_inject_rejected", detail=ack.detail)
            return ack

        # 403 (bad key), 429 (rate limit), or anything unexpected: log id-free detail only.
        logger.warning("ui_inject_failed", status=response.status_code)
        return InjectAck(accepted=False, detail=f"bridge returned HTTP {response.status_code}")
