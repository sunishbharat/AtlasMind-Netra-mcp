"""Tests for valkey_stores.py: ValkeySessionStore and ValkeyBriefingSessionStore.

Uses a FakeValkey client that intercepts calls so tests run without a live valkey server.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from memory.session_store import SessionState
from memory.valkey_stores import (
    _BRIEFING_PREFIX,
    _SESSION_PREFIX,
    ValkeyBriefingSessionStore,
    ValkeySessionStore,
    make_valkey_client,
)

# ---------------------------------------------------------------------------
# FakeValkey: interceptor that records calls and returns programmed responses
# ---------------------------------------------------------------------------


class FakeValkey:
    """Fake valkey client for unit testing without a live server.

    Records every command call in `calls` as (method_name, args, kwargs).
    Set `storage` to pre-load data, or use `return_values` to programme per-call
    responses.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.storage: dict[str, str] = {}
        self.return_values: dict[str, Any] = {}

    async def get(self, key: str) -> str | None:
        self.calls.append(("get", (key,), {}))
        if key in self.return_values:
            return cast("str | None", self.return_values.pop(key))
        return self.storage.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append(("setex", (key, ttl, value), {}))
        self.storage[key] = value

    async def delete(self, key: str) -> int:
        self.calls.append(("delete", (key,), {}))
        return self.storage.pop(key, None) is not None


# ---------------------------------------------------------------------------
# make_valkey_client
# ---------------------------------------------------------------------------


def test_make_valkey_client_with_password() -> None:
    """make_valkey_client wires url and password into Valkey.from_url."""
    from unittest.mock import patch

    from pydantic import SecretStr

    from config.settings import ValkeySettings

    settings = ValkeySettings(url="valkey://localhost:6379/0", password=SecretStr("secret"))
    fake_valkey = MagicMock()

    with patch("memory.valkey_stores.Valkey") as mock_valkey:
        mock_valkey.from_url.return_value = fake_valkey
        result = make_valkey_client(settings)

    mock_valkey.from_url.assert_called_once_with(
        "valkey://localhost:6379/0", password="secret", decode_responses=True
    )
    assert result is fake_valkey


def test_make_valkey_client_without_password() -> None:
    """make_valkey_client works when password is None."""
    from unittest.mock import patch

    from config.settings import ValkeySettings

    settings = ValkeySettings(url="valkey://localhost:6379/0", password=None)
    fake_valkey = MagicMock()

    with patch("memory.valkey_stores.Valkey") as mock_valkey:
        mock_valkey.from_url.return_value = fake_valkey
        result = make_valkey_client(settings)

    mock_valkey.from_url.assert_called_once_with(
        "valkey://localhost:6379/0", password=None, decode_responses=True
    )
    assert result is fake_valkey


# ---------------------------------------------------------------------------
# ValkeySessionStore
# ---------------------------------------------------------------------------


async def test_session_store_set_and_get_roundtrip() -> None:
    """set() stores JSON with TTL; get() retrieves and deserialises it."""
    fake = FakeValkey()
    store = ValkeySessionStore(fake, ttl_seconds=120)  # type: ignore[arg-type]

    session = SessionState(session_id="s1", pending_query="show blockers")
    await store.set(session)

    # Verify setex was called with correct key and TTL
    setex_call = next(c for c in fake.calls if c[0] == "setex")
    key, ttl, value = setex_call[1]
    assert key == f"{_SESSION_PREFIX}s1"
    assert ttl == 120
    # Value must be valid JSON that reconstructs the session
    loaded = SessionState.model_validate_json(value)
    assert loaded.session_id == "s1"
    assert loaded.pending_query == "show blockers"

    # get() returns the session
    fake.storage[f"{_SESSION_PREFIX}s1"] = value
    result = await store.get("s1")
    assert result is not None
    assert result.session_id == "s1"


async def test_session_store_get_unknown_returns_none() -> None:
    """get() with an unknown session_id returns None."""
    fake = FakeValkey()
    store = ValkeySessionStore(fake, ttl_seconds=60)  # type: ignore[arg-type]
    result = await store.get("does-not-exist")
    assert result is None
    get_call = fake.calls[0]
    assert get_call[0] == "get"
    assert get_call[1][0] == f"{_SESSION_PREFIX}does-not-exist"


async def test_session_store_delete_calls_client_delete() -> None:
    """delete() removes the key from valkey."""
    fake = FakeValkey()
    fake.storage[f"{_SESSION_PREFIX}s1"] = '{"session_id":"s1"}'
    store = ValkeySessionStore(fake, ttl_seconds=60)  # type: ignore[arg-type]

    await store.delete("s1")

    delete_call = next(c for c in fake.calls if c[0] == "delete")
    assert delete_call[1][0] == f"{_SESSION_PREFIX}s1"


async def test_session_store_delete_idempotent() -> None:
    """delete() on a non-existent key does not raise."""
    fake = FakeValkey()
    store = ValkeySessionStore(fake, ttl_seconds=60)  # type: ignore[arg-type]
    await store.delete("never-existed")  # must not raise


# ---------------------------------------------------------------------------
# ValkeyBriefingSessionStore
# ---------------------------------------------------------------------------


async def test_briefing_store_set_and_get_roundtrip() -> None:
    """set() stores BriefingPendingState JSON; get() deserialises it back."""
    from memory.briefing_session_store import BriefingPendingState
    from models.responses import AgendaTopic

    fake = FakeValkey()
    store = ValkeyBriefingSessionStore(fake, ttl_seconds=300)  # type: ignore[arg-type]

    state = BriefingPendingState(
        session_id="b1",
        agenda_text="daily standup",
        topics=[
            AgendaTopic(
                topic_id="t1",
                description=" blockers",
                suggested_query="project = X AND labels = blocker",
            )
        ],
    )
    await store.set(state)

    setex_call = next(c for c in fake.calls if c[0] == "setex")
    key, ttl, value = setex_call[1]
    assert key == f"{_BRIEFING_PREFIX}b1"
    assert ttl == 300

    loaded = BriefingPendingState.model_validate_json(value)
    assert loaded.session_id == "b1"
    assert len(loaded.topics) == 1

    # Pre-load storage and verify get roundtrip
    fake.storage[f"{_BRIEFING_PREFIX}b1"] = value
    result = await store.get("b1")
    assert result is not None
    assert result.session_id == "b1"


async def test_briefing_store_get_unknown_returns_none() -> None:
    """get() with an unknown session_id returns None."""
    fake = FakeValkey()
    store = ValkeyBriefingSessionStore(fake, ttl_seconds=60)  # type: ignore[arg-type]
    result = await store.get("no-such-briefing")
    assert result is None


async def test_briefing_store_delete_calls_client_delete() -> None:
    """delete() removes the briefing key."""
    from memory.briefing_session_store import BriefingPendingState

    fake = FakeValkey()
    fake.storage[f"{_BRIEFING_PREFIX}b1"] = BriefingPendingState(
        session_id="b1",
        agenda_text="x",
        topics=[],
    ).model_dump_json()
    store = ValkeyBriefingSessionStore(fake, ttl_seconds=60)  # type: ignore[arg-type]

    await store.delete("b1")

    delete_call = next(c for c in fake.calls if c[0] == "delete")
    assert delete_call[1][0] == f"{_BRIEFING_PREFIX}b1"
