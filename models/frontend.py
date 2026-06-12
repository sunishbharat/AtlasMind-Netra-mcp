"""Typed models for the frontendUI bridge API (docs/frontendui_bridge_contract.md)."""

from pydantic import BaseModel, ConfigDict


class InjectRequest(BaseModel):
    """POST /api/mcp/inject request body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    request_id: str | None = None


class InjectAck(BaseModel):
    """Inject outcome; accepted=False covers UI-busy, no-session, and transport failures."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    accepted: bool
    request_id: str | None = None
    detail: str | None = None
