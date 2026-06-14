"""Briefing pipeline session state (Milestone 3).

Stores multi-topic dispatch state across generate_briefing turns when MCP elicitation
is unavailable and the host must do session round-trips for per-topic clarification.
Mirrors the Phase 1 in-process pattern of InMemorySessionStore.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from models.responses import AgendaTopic, QueryResponse


class BriefingPendingState(BaseModel):
    """In-flight state of a multi-turn generate_briefing call.

    Tracks which topics have been dispatched and which is pending clarification.
    Immutable; update via model_copy(update={...}).
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    agenda_text: str
    topics: list[AgendaTopic]
    current_topic_idx: int = 0
    completed_results: dict[str, QueryResponse] = Field(default_factory=dict)


class BaseBriefingSessionStore(ABC):
    """Interface for briefing session persistence; mirrors BaseSessionStore shape."""

    @abstractmethod
    async def get(self, session_id: str) -> BriefingPendingState | None:
        """Return the live briefing state, or None when unknown or expired."""

    @abstractmethod
    async def set(self, state: BriefingPendingState) -> None:
        """Persist state and refresh its TTL."""

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """Remove state; no-op when absent."""


class InMemoryBriefingSessionStore(BaseBriefingSessionStore):
    """Phase 1: in-process dict with TTL, expired lazily on access.

    NOTE: sessions are lost on server restart because time.monotonic is per-process.
    The MCP host must not rely on session persistence across restarts for the
    clarification round-trip case.
    """

    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, BriefingPendingState]] = {}

    async def get(self, session_id: str) -> BriefingPendingState | None:
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        expires_at, state = entry
        if self._clock() >= expires_at:
            del self._entries[session_id]
            return None
        return state

    async def set(self, state: BriefingPendingState) -> None:
        self._entries[state.session_id] = (self._clock() + self._ttl, state)

    async def delete(self, session_id: str) -> None:
        self._entries.pop(session_id, None)
