"""JiraFieldsLoader: tolerant loading of the backend's on-disk metadata."""

import json
from pathlib import Path

from core.jira_fields_loader import JiraFieldsLoader

FIELDS = [
    {
        "id": "customfield_10042",
        "name": "Escalation Flag",
        "custom": True,
        "schema": {"type": "boolean"},
        "description": "Set when escalated",
    },
    {"id": "priority", "name": "Priority"},
]


def test_unset_paths_degrade_to_empty() -> None:
    loader = JiraFieldsLoader(None, None)
    assert loader.load_fields() == []
    assert loader.load_allowed_values() == {}


def test_missing_files_degrade_to_empty(tmp_path: Path) -> None:
    loader = JiraFieldsLoader(tmp_path / "nope.json", tmp_path / "nope2.json")
    assert loader.load_fields() == []
    assert loader.load_allowed_values() == {}


def test_loads_bare_array(tmp_path: Path) -> None:
    path = tmp_path / "jira_fields.json"
    path.write_text(json.dumps(FIELDS), encoding="utf-8")
    fields = JiraFieldsLoader(path, None).load_fields()
    assert [f.id for f in fields] == ["customfield_10042", "priority"]
    assert fields[0].field_schema == {"type": "boolean"}  # populated via the "schema" alias


def test_loads_wrapped_object(tmp_path: Path) -> None:
    path = tmp_path / "jira_fields.json"
    path.write_text(json.dumps({"fields": FIELDS}), encoding="utf-8")
    assert len(JiraFieldsLoader(path, None).load_fields()) == 2


def test_corrupt_fields_file_degrades(tmp_path: Path) -> None:
    path = tmp_path / "jira_fields.json"
    path.write_text("{broken", encoding="utf-8")
    assert JiraFieldsLoader(path, None).load_fields() == []


def test_loads_allowed_values(tmp_path: Path) -> None:
    path = tmp_path / "jira_allowed_values.json"
    path.write_text(json.dumps({"priority": ["Blocker", "Critical"]}), encoding="utf-8")
    loader = JiraFieldsLoader(None, path)
    assert loader.load_allowed_values() == {"priority": ["Blocker", "Critical"]}


def test_non_object_allowed_values_degrades(tmp_path: Path) -> None:
    path = tmp_path / "jira_allowed_values.json"
    path.write_text(json.dumps(["wrong"]), encoding="utf-8")
    assert JiraFieldsLoader(None, path).load_allowed_values() == {}
