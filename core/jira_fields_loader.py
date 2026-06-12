"""Reads the backend's cached Jira metadata from disk (design doc: JiraFieldsLoader).

The clarification engine uses real field names so its questions reference the live
instance. Missing files degrade gracefully: the clarifier still works, just without
field-name grounding (logged as WARNING, not an error).
"""

import json
from pathlib import Path

import structlog
from pydantic import ValidationError

from models.jira import JiraField

logger = structlog.get_logger(__name__)


class JiraFieldsLoader:
    """Loads jira_fields.json and jira_allowed_values.json; caches after first read."""

    def __init__(self, fields_path: Path | None, allowed_values_path: Path | None) -> None:
        self._fields_path = fields_path
        self._allowed_values_path = allowed_values_path
        self._fields: list[JiraField] | None = None
        self._allowed_values: dict[str, list[str]] | None = None

    def load_fields(self) -> list[JiraField]:
        """Field metadata, or [] when the file is unset, missing, or unreadable."""
        if self._fields is None:
            self._fields = self._read_fields()
        return self._fields

    def load_allowed_values(self) -> dict[str, list[str]]:
        """Allowed values per field id, or {} when unavailable."""
        if self._allowed_values is None:
            self._allowed_values = self._read_allowed_values()
        return self._allowed_values

    def _read_fields(self) -> list[JiraField]:
        path = self._fields_path
        if path is None or not path.is_file():
            logger.warning("jira_fields_unavailable", path=str(path))
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Contract: a bare array, or a {"fields": [...]} wrapper.
            entries = raw.get("fields", []) if isinstance(raw, dict) else raw
            return [JiraField.model_validate(entry) for entry in entries]
        except (OSError, json.JSONDecodeError, ValidationError, AttributeError) as exc:
            logger.warning("jira_fields_unreadable", path=str(path), error=str(exc))
            return []

    def _read_allowed_values(self) -> dict[str, list[str]]:
        path = self._allowed_values_path
        if path is None or not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("expected a JSON object of field id -> values")
            return {str(k): [str(v) for v in values] for k, values in raw.items()}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("jira_allowed_values_unreadable", path=str(path), error=str(exc))
            return {}
