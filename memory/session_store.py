"""Session state between clarification turns (design doc: SessionStore, Phase 1).

State machine pattern: the clarification loop's states are an explicit Enum and every
transition goes through `SessionState.transition`, which enforces the allowed-transitions
table in one place.

Store phases (design doc): Phase 1 in-process dict with TTL (this module) -> Phase 2
PostgreSQL -> Phase 3 Valkey, all behind `BaseSessionStore`.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.exceptions import StateTransitionError


class ClarificationState(StrEnum):
    """States of the per-session clarification loop."""

    IDLE = "idle"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    RESOLVED = "resolved"
    DISPATCHED = "dispatched"


_ALLOWED_TRANSITIONS: dict[ClarificationState, frozenset[ClarificationState]] = {
    ClarificationState.IDLE: frozenset(
        {
            ClarificationState.AWAITING_CLARIFICATION,
            ClarificationState.RESOLVED,
            ClarificationState.DISPATCHED,
        }
    ),
    ClarificationState.AWAITING_CLARIFICATION: frozenset(
        {
            ClarificationState.AWAITING_CLARIFICATION,
            ClarificationState.RESOLVED,
            ClarificationState.DISPATCHED,
            ClarificationState.IDLE,
        }
    ),
    ClarificationState.RESOLVED: frozenset(
        {
            ClarificationState.AWAITING_CLARIFICATION,
            ClarificationState.DISPATCHED,
        }
    ),
    ClarificationState.DISPATCHED: frozenset(
        {
            ClarificationState.AWAITING_CLARIFICATION,
            ClarificationState.RESOLVED,
            ClarificationState.DISPATCHED,
            ClarificationState.IDLE,
        }
    ),
}


class TermResolution(BaseModel):
    """A resolved interpretation of one ambiguous term, scoped to a session."""

    model_config = ConfigDict(frozen=True)

    term: str
    resolution_key: str
    jql_hint: str
    source: Literal["convention", "clarification"]


class SessionState(BaseModel):
    """Mutable context of one MCP session's clarification loop."""

    session_id: str
    state: ClarificationState = ClarificationState.IDLE
    pending_query: str | None = None
    pending_terms: list[str] = Field(default_factory=list)
    pending_question: str | None = None
    clarification_rounds: int = 0
    resolutions: dict[str, TermResolution] = Field(default_factory=dict)

    def transition(self, new_state: ClarificationState) -> None:
        """Move to `new_state`, enforcing the allowed-transitions table."""
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise StateTransitionError(
                f"session {self.session_id}: illegal transition {self.state} -> {new_state}"
            )
        self.state = new_state


class BaseSessionStore(ABC):
    """Interface for session persistence; swappable per design-doc phases."""

    @abstractmethod
    async def get(self, session_id: str) -> SessionState | None:
        """Return the live session, or None when unknown or expired."""

    @abstractmethod
    async def set(self, session: SessionState) -> None:
        """Persist the session and refresh its TTL."""

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """Remove the session; no-op when absent."""


class InMemorySessionStore(BaseSessionStore):
    """Phase 1: in-process dict with TTL, expired lazily on access.

    The clock is injected so TTL behaviour is testable without sleeping.
    """

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, SessionState]] = {}

    async def get(self, session_id: str) -> SessionState | None:
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        expires_at, session = entry
        if self._clock() >= expires_at:
            del self._entries[session_id]
            return None
        return session

    async def set(self, session: SessionState) -> None:
        self._entries[session.session_id] = (self._clock() + self._ttl, session)

    async def delete(self, session_id: str) -> None:
        self._entries.pop(session_id, None)
