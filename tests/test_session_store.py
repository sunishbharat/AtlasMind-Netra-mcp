"""InMemorySessionStore TTL behaviour and the clarification state machine."""

import pytest

from core.exceptions import StateTransitionError
from memory.session_store import (
    _ALLOWED_TRANSITIONS,
    ClarificationState,
    InMemorySessionStore,
    SessionState,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_set_get_roundtrip() -> None:
    store = InMemorySessionStore(ttl_seconds=60)
    session = SessionState(session_id="s1", pending_query="q")
    await store.set(session)
    loaded = await store.get("s1")
    assert loaded is not None
    assert loaded.pending_query == "q"


async def test_unknown_session_is_none() -> None:
    store = InMemorySessionStore(ttl_seconds=60)
    assert await store.get("nope") is None


async def test_expires_after_ttl() -> None:
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=10, clock=clock)
    await store.set(SessionState(session_id="s1"))
    clock.now = 9.9
    assert await store.get("s1") is not None
    clock.now = 10.0
    assert await store.get("s1") is None


async def test_set_refreshes_ttl() -> None:
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=10, clock=clock)
    await store.set(SessionState(session_id="s1"))
    clock.now = 8.0
    await store.set(SessionState(session_id="s1"))
    clock.now = 12.0
    assert await store.get("s1") is not None


async def test_delete_is_idempotent() -> None:
    store = InMemorySessionStore(ttl_seconds=60)
    await store.set(SessionState(session_id="s1"))
    await store.delete("s1")
    await store.delete("s1")
    assert await store.get("s1") is None


def test_every_allowed_transition() -> None:
    # 100% transition coverage: every edge in the allowed-transitions table works.
    for source, targets in _ALLOWED_TRANSITIONS.items():
        for target in targets:
            session = SessionState(session_id="s", state=source)
            session.transition(target)
            assert session.state is target


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ClarificationState.IDLE, ClarificationState.IDLE),
        (ClarificationState.RESOLVED, ClarificationState.IDLE),
        (ClarificationState.RESOLVED, ClarificationState.RESOLVED),
    ],
)
def test_illegal_transitions_raise(source: ClarificationState, target: ClarificationState) -> None:
    session = SessionState(session_id="s", state=source)
    with pytest.raises(StateTransitionError, match="illegal transition"):
        session.transition(target)
