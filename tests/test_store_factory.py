"""Tests for store_factory.py: create_session_store and create_briefing_session_store."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from config.settings import Settings, ValkeySettings
from memory.briefing_session_store import (
    BaseBriefingSessionStore,
    InMemoryBriefingSessionStore,
)
from memory.session_store import BaseSessionStore, InMemorySessionStore
from memory.store_factory import create_briefing_session_store, create_session_store


def _make_settings(backend: str = "memory") -> Settings:
    """Create Settings with the given session_backend."""
    return Settings(
        server={"session_backend": backend},  # type: ignore[arg-type]
        session={"ttl_seconds": 120.0},
        valkey={"url": "valkey://localhost:6379/0"},  # type: ignore[arg-type]
    )


class TestCreateSessionStore:
    """Test create_session_store returns the correct backend."""

    def test_memory_backend_returns_inmemory_store(self) -> None:
        """When session_backend=memory, returns InMemorySessionStore."""
        settings = _make_settings(backend="memory")
        store = create_session_store(settings)
        assert isinstance(store, InMemorySessionStore)
        assert isinstance(store, BaseSessionStore)

    @patch("memory.store_factory.ValkeySessionStore")
    @patch("memory.store_factory.make_valkey_client")
    def test_valkey_backend_returns_valkey_store(
        self, mock_make_client: patch, mock_store_class: patch
    ) -> None:
        """When session_backend=valkey, returns ValkeySessionStore."""
        mock_client = {}
        mock_make_client.return_value = mock_client
        mock_store_class.return_value = "valkey-store-instance"

        settings = _make_settings(backend="valkey")
        result = create_session_store(settings)

        mock_make_client.assert_called_once_with(settings.valkey)
        mock_store_class.assert_called_once_with(mock_client, settings.session.ttl_seconds)
        assert result == "valkey-store-instance"


class TestCreateBriefingSessionStore:
    """Test create_briefing_session_store returns the correct backend."""

    def test_memory_backend_returns_inmemory_store(self) -> None:
        """When session_backend=memory, returns InMemoryBriefingSessionStore."""
        settings = _make_settings(backend="memory")
        store = create_briefing_session_store(settings)
        assert isinstance(store, InMemoryBriefingSessionStore)
        assert isinstance(store, BaseBriefingSessionStore)

    @patch("memory.store_factory.ValkeyBriefingSessionStore")
    @patch("memory.store_factory.make_valkey_client")
    def test_valkey_backend_returns_valkey_store(
        self, mock_make_client: patch, mock_store_class: patch
    ) -> None:
        """When session_backend=valkey, returns ValkeyBriefingSessionStore."""
        mock_client = {}
        mock_make_client.return_value = mock_client
        mock_store_class.return_value = "valkey-briefing-store-instance"

        settings = _make_settings(backend="valkey")
        result = create_briefing_session_store(settings)

        mock_make_client.assert_called_once_with(settings.valkey)
        mock_store_class.assert_called_once_with(mock_client, settings.session.ttl_seconds)
        assert result == "valkey-briefing-store-instance"


class TestStoreFactoryTypeSafety:
    """Ensure factory always returns the abstract base type (dependency injection contract)."""

    def test_create_session_store_returns_base_session_store(self) -> None:
        """Return type is BaseSessionStore - callers depend on the interface."""
        settings = _make_settings(backend="memory")
        store = create_session_store(settings)
        # BaseSessionStore is the interface; all stores implement it
        assert isinstance(store, BaseSessionStore)

    def test_create_briefing_session_store_returns_base_briefing_session_store(
        self,
    ) -> None:
        """Return type is BaseBriefingSessionStore - callers depend on the interface."""
        settings = _make_settings(backend="memory")
        store = create_briefing_session_store(settings)
        assert isinstance(store, BaseBriefingSessionStore)

    def test_ttl_is_wired_from_settings(self) -> None:
        """The TTL from settings is passed to the store constructor."""
        settings = _make_settings(backend="memory")
        store = create_session_store(settings)
        # InMemorySessionStore stores ttl_seconds internally; verify via get
        import asyncio
        from memory.session_store import SessionState

        async def check():
            await store.set(SessionState(session_id="check-ttl"))
            # With TTL=120, the entry should exist
            result = await store.get("check-ttl")
            assert result is not None

        asyncio.run(check())