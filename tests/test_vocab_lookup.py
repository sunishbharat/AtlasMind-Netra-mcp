"""VocabLookup: loading, validation, fail-fast errors."""

import json
from pathlib import Path

import pytest

from core.exceptions import ConfigError
from core.vocab_lookup import VocabLookup
from tests.conftest import VOCAB_PATH


def test_loads_shipped_vocabulary() -> None:
    vocab = VocabLookup(VOCAB_PATH)
    assert "escalation" in vocab.terms
    entry = vocab.get("Escalation")  # case-insensitive
    assert entry is not None
    assert entry.jql_patterns["label"] == "labels = escalation"


def test_entries_for_skips_unknown_terms() -> None:
    vocab = VocabLookup(VOCAB_PATH)
    entries = vocab.entries_for(["today", "no-such-term"])
    assert list(entries) == ["today"]


def test_missing_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        VocabLookup(tmp_path / "missing.json")


def test_invalid_entry_names_file_and_term(tmp_path: Path) -> None:
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps({"badterm": {"questions": []}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="badterm"):
        VocabLookup(path)


def test_non_object_root_rejected(tmp_path: Path) -> None:
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a JSON object"):
        VocabLookup(path)
