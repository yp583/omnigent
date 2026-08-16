"""Persistence and provider tests for owner-private Conductor memory."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from omnigent.conductor import MarkdownArtifactMemoryProvider, validate_memory_path
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conductor_store.sqlalchemy_store import SqlAlchemyConductorStore
from omnigent.stores.memory_store import MemoryConflictError
from omnigent.stores.memory_store.sqlalchemy_store import SqlAlchemyMemoryStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_conductor_store_is_singleton_and_owner_scoped(db_uri: str) -> None:
    store = SqlAlchemyConductorStore(db_uri)
    alice = store.create("alice@example.com", _uid("alice-conductor"))
    bob = store.create("bob@example.com", _uid("bob-conductor"), config={"digest": "daily"})

    assert store.get("alice@example.com") == alice
    assert store.get("bob@example.com") == bob
    assert store.get("carol@example.com") is None


def test_markdown_provider_revisions_conflicts_and_delete(db_uri: str, tmp_path: Path) -> None:
    provider = MarkdownArtifactMemoryProvider(
        SqlAlchemyMemoryStore(db_uri),
        LocalArtifactStore(str(tmp_path / "artifacts")),
    )

    first = provider.write(
        "alice@example.com", "projects/demo/overview.md", "# Demo\n", expected_revision=0
    )
    second = provider.write(
        "alice@example.com",
        "projects/demo/overview.md",
        "# Demo\n\nReady.\n",
        expected_revision=1,
    )

    assert first.revision == 1
    assert second.revision == 2
    assert provider.read("alice@example.com", second.path) == (second, "# Demo\n\nReady.\n")
    assert [item.revision for item in provider.history("alice@example.com", second.path)] == [
        2,
        1,
    ]
    old = provider.read_revision("alice@example.com", second.path, 1)
    assert old is not None and old[1] == "# Demo\n"

    with pytest.raises(MemoryConflictError):
        provider.write(
            "alice@example.com",
            second.path,
            "stale",
            expected_revision=1,
        )

    assert provider.read("bob@example.com", second.path) is None
    assert provider.delete("alice@example.com", second.path, expected_revision=2)
    assert provider.read("alice@example.com", second.path) is None
    assert len(provider.history("alice@example.com", second.path)) == 2


@pytest.mark.parametrize(
    "path",
    ["../secret.md", "/absolute.md", "bad\\path.md", "notes.txt", "a/./b.md"],
)
def test_memory_path_rejects_unsafe_or_non_markdown_paths(path: str) -> None:
    with pytest.raises(ValueError):
        validate_memory_path(path)


def test_ensure_defaults_builds_canonical_memory_tree(db_uri: str, tmp_path: Path) -> None:
    provider = MarkdownArtifactMemoryProvider(
        SqlAlchemyMemoryStore(db_uri),
        LocalArtifactStore(str(tmp_path / "artifacts")),
    )

    provider.ensure_defaults("local")
    provider.ensure_defaults("local")

    assert [item.path for item in provider.list("local")] == [
        "MEMORY.md",
        "profile/preferences.md",
        "skills/observations.md",
    ]
