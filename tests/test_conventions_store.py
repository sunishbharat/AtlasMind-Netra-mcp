"""JsonFileConventionsStore: project+term keying, fallback chain, persistence."""

from pathlib import Path

import pytest

from core.exceptions import ConfigError
from memory.conventions_store import Convention, JsonFileConventionsStore


def make_convention(project: str = "_default", term: str = "escalation") -> Convention:
    return Convention(
        project=project, term=term, resolution_key="label", jql_hint="labels = escalation"
    )


async def test_set_and_get_exact(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    await store.set(make_convention(project="CAR"))
    found = await store.get("CAR", "escalation")
    assert found is not None
    assert found.jql_hint == "labels = escalation"


async def test_falls_back_to_instance_default(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    await store.set(make_convention(project="_default"))
    assert await store.get("CAR", "escalation") is not None


async def test_project_convention_beats_instance_default(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    await store.set(make_convention(project="_default"))
    await store.set(
        Convention(
            project="CAR",
            term="escalation",
            resolution_key="priority",
            jql_hint="priority in (Critical, Blocker)",
        )
    )
    found = await store.get("CAR", "escalation")
    assert found is not None
    assert found.resolution_key == "priority"


async def test_lookup_is_case_insensitive(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    await store.set(make_convention(project="CAR"))
    assert await store.get("car", "Escalation") is not None


async def test_unknown_term_is_none(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    assert await store.get("CAR", "today") is None


async def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "conv.json"
    store = JsonFileConventionsStore(path)
    await store.set(make_convention())
    reloaded = JsonFileConventionsStore(path)
    assert await reloaded.get("_default", "escalation") is not None


async def test_list_and_delete(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "conv.json")
    await store.set(make_convention())
    assert len(await store.list_all()) == 1
    assert await store.delete("_default", "escalation") is True
    assert await store.delete("_default", "escalation") is False
    assert await store.list_all() == []


def test_missing_file_starts_empty(tmp_path: Path) -> None:
    store = JsonFileConventionsStore(tmp_path / "missing.json")
    assert store is not None


def test_corrupt_file_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "conv.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot load conventions"):
        JsonFileConventionsStore(path)
