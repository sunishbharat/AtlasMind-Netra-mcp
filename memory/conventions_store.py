"""Learned team conventions, keyed by project + term (design doc: ConventionsStore).

Repository pattern: persistence is hidden behind `BaseConventionsStore`; callers never see
storage details. Conventions are inspectable (list_all) and resettable (delete) because one
wrongly learned convention would corrupt every future query invisibly.

Lookup fallback chain (design doc v2): project -> instance default -> ask.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config.settings import INSTANCE_DEFAULT_PROJECT
from core.exceptions import ConfigError


class Convention(BaseModel):
    """One learned interpretation: in `project`, `term` means `jql_hint`."""

    model_config = ConfigDict(frozen=True)

    project: str = Field(
        description=f"Jira project key, or '{INSTANCE_DEFAULT_PROJECT}' for the instance level."
    )
    term: str
    resolution_key: str
    jql_hint: str
    learned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BaseConventionsStore(ABC):
    """Interface for convention persistence (dict/JSON now, Postgres in future)."""

    @abstractmethod
    async def get(self, project: str, term: str) -> Convention | None:
        """Return the convention for (project, term), falling back to the instance default."""

    @abstractmethod
    async def set(self, convention: Convention) -> None:
        """Persist (upsert) a convention."""

    @abstractmethod
    async def list_all(self) -> list[Convention]:
        """All stored conventions, for inspection (maintenance command, briefing footer)."""

    @abstractmethod
    async def delete(self, project: str, term: str) -> bool:
        """Remove one convention (exact key, no fallback). Returns False when absent."""


class JsonFileConventionsStore(BaseConventionsStore):
    """Phase 1: conventions in a JSON file under data/ (path from settings)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: dict[tuple[str, str], Convention] = self._load(path)

    @staticmethod
    def _key(project: str, term: str) -> tuple[str, str]:
        return project.lower(), term.lower()

    @staticmethod
    def _load(path: Path) -> dict[tuple[str, str], Convention]:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            conventions = [Convention.model_validate(item) for item in raw]
        except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ConfigError(f"{path}: cannot load conventions: {exc}") from exc
        return {JsonFileConventionsStore._key(c.project, c.term): c for c in conventions}

    def _persist(self) -> None:
        """Atomic write: temp file in the same directory, then replace."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([c.model_dump(mode="json") for c in self._items.values()], indent=2)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self._path)

    async def get(self, project: str, term: str) -> Convention | None:
        exact = self._items.get(self._key(project, term))
        if exact is not None:
            return exact
        return self._items.get(self._key(INSTANCE_DEFAULT_PROJECT, term))

    async def set(self, convention: Convention) -> None:
        self._items[self._key(convention.project, convention.term)] = convention
        # File IO off the event loop (Rule 5: no blocking calls in async paths).
        await asyncio.to_thread(self._persist)

    async def list_all(self) -> list[Convention]:
        return list(self._items.values())

    async def delete(self, project: str, term: str) -> bool:
        removed = self._items.pop(self._key(project, term), None) is not None
        if removed:
            await asyncio.to_thread(self._persist)
        return removed
