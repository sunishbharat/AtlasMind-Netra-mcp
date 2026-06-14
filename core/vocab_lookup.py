"""Disambiguation vocabulary lookup (design doc: VocabLookup)."""

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from core.exceptions import ConfigError
from models.vocab import VocabEntry


class VocabLookup:
    """Loads config/clarification_vocab.json and serves entries by term.

    Validated through pydantic at startup; malformed config fails fast with an error
    naming the file and the offending term.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries = self._load(path)

    @staticmethod
    def _load(path: Path) -> dict[str, VocabEntry]:
        if not path.is_file():
            raise ConfigError(f"{path}: vocabulary file not found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: cannot read vocabulary: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a JSON object of term -> entry")
        entries: dict[str, VocabEntry] = {}
        for term, value in raw.items():
            try:
                entries[term.lower()] = VocabEntry.model_validate(value)
            except ValidationError as exc:
                raise ConfigError(f"{path}: invalid entry for term '{term}': {exc}") from exc
        return entries

    @property
    def terms(self) -> list[str]:
        """All known ambiguous terms, lowercased."""
        return list(self._entries)

    def get(self, term: str) -> VocabEntry | None:
        return self._entries.get(term.lower())

    def entries_for(self, terms: Iterable[str]) -> dict[str, VocabEntry]:
        """Subset of the vocabulary for the given terms (unknown terms are skipped)."""
        result: dict[str, VocabEntry] = {}
        for term in terms:
            entry = self.get(term)
            if entry is not None:
                result[term.lower()] = entry
        return result
