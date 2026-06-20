"""Session store factory: selects the correct backend from settings.

Adding a new backend:
  1. Create memory/<backend>_stores.py with concrete BaseSessionStore and
     BaseBriefingSessionStore subclasses.
  2. Add a new branch in create_session_store() and create_briefing_session_store() below.
  3. Add the backend name to ServerSettings.session_backend Literal in config/settings.py.

All callers receive BaseSessionStore / BaseBriefingSessionStore - they never import or
instantiate concrete store classes directly.
"""

from config.settings import Settings
from memory.briefing_session_store import BaseBriefingSessionStore, InMemoryBriefingSessionStore
from memory.session_store import BaseSessionStore, InMemorySessionStore


def create_session_store(settings: Settings) -> BaseSessionStore:
    """Return the configured session store backend.

    'memory'  -> InMemorySessionStore  (default; single-instance only)
    'valkey'  -> ValkeySessionStore    (shared; required for horizontal scaling)
    """
    if settings.server.session_backend == "valkey":
        from memory.valkey_stores import ValkeySessionStore, make_valkey_client

        return ValkeySessionStore(make_valkey_client(settings.valkey), settings.session.ttl_seconds)
    return InMemorySessionStore(ttl_seconds=settings.session.ttl_seconds)


def create_briefing_session_store(settings: Settings) -> BaseBriefingSessionStore:
    """Return the configured briefing session store backend.

    'memory'  -> InMemoryBriefingSessionStore  (default; single-instance only)
    'valkey'  -> ValkeyBriefingSessionStore    (shared; required for horizontal scaling)
    """
    if settings.server.session_backend == "valkey":
        from memory.valkey_stores import ValkeyBriefingSessionStore, make_valkey_client

        return ValkeyBriefingSessionStore(
            make_valkey_client(settings.valkey), settings.session.ttl_seconds
        )
    return InMemoryBriefingSessionStore(ttl_seconds=settings.session.ttl_seconds)
