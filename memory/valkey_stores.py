"""Valkey-backed session stores for horizontal scaling.

Drop-in replacements for InMemorySessionStore and InMemoryBriefingSessionStore.
Both implement the same ABC interfaces so no caller code changes when switching backends.

Key format:
  netra:session:{session_id}   -> SessionState JSON
  netra:briefing:{session_id}  -> BriefingPendingState JSON

TTL is refreshed on every write via setex - never use set() without a TTL on session keys.
Valkey connection errors propagate to the caller and are never swallowed here; the load
balancer detects unhealthy instances via failed health checks and routes around them.

"""

from typing import cast

from valkey.asyncio import Valkey

from config.settings import ValkeySettings
from memory.briefing_session_store import BaseBriefingSessionStore, BriefingPendingState
from memory.session_store import BaseSessionStore, SessionState

_SESSION_PREFIX = "netra:session:"
_BRIEFING_PREFIX = "netra:briefing:"


def make_valkey_client(settings: ValkeySettings) -> Valkey:
    """Create an async Valkey client with string decoding enabled.

    Accepts ValkeySettings so the caller never hard-codes connection details.
    decode_responses=True ensures get() returns str | None, not bytes | None.
    """
    password = settings.password.get_secret_value() if settings.password is not None else None
    return cast(
        Valkey,
        Valkey.from_url(settings.url, password=password, decode_responses=True),
    )


class ValkeySessionStore(BaseSessionStore):
    """Valkey-backed clarification session store.

    Replaces InMemorySessionStore for deployments with more than one server instance.
    Sessions survive instance restarts because TTL ownership moves to Valkey.

    Key: netra:session:{session_id}
    """

    def __init__(self, client: Valkey, ttl_seconds: float) -> None:
        self._client = client
        self._ttl = int(ttl_seconds)

    async def get(self, session_id: str) -> SessionState | None:
        raw: str | None = cast(
            "str | None", await self._client.get(f"{_SESSION_PREFIX}{session_id}")
        )
        if raw is None:
            return None
        return SessionState.model_validate_json(raw)

    async def set(self, session: SessionState) -> None:
        await self._client.setex(
            f"{_SESSION_PREFIX}{session.session_id}",
            self._ttl,
            session.model_dump_json(),
        )

    async def delete(self, session_id: str) -> None:
        await self._client.delete(f"{_SESSION_PREFIX}{session_id}")


class ValkeyBriefingSessionStore(BaseBriefingSessionStore):
    """Valkey-backed briefing pipeline session store.

    Replaces InMemoryBriefingSessionStore for deployments with more than one server instance.
    Multi-turn generate_briefing state persists across instance boundaries.

    Key: netra:briefing:{session_id}
    """

    def __init__(self, client: Valkey, ttl_seconds: float) -> None:
        self._client = client
        self._ttl = int(ttl_seconds)

    async def get(self, session_id: str) -> BriefingPendingState | None:
        raw: str | None = cast(
            "str | None", await self._client.get(f"{_BRIEFING_PREFIX}{session_id}")
        )
        if raw is None:
            return None
        return BriefingPendingState.model_validate_json(raw)

    async def set(self, state: BriefingPendingState) -> None:
        await self._client.setex(
            f"{_BRIEFING_PREFIX}{state.session_id}",
            self._ttl,
            state.model_dump_json(),
        )

    async def delete(self, session_id: str) -> None:
        await self._client.delete(f"{_BRIEFING_PREFIX}{session_id}")
