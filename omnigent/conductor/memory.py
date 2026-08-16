"""Provider-neutral Conductor memory backed by regular Markdown documents."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from builtins import list as builtin_list
from pathlib import PurePosixPath

from omnigent.entities import MemoryDocument, MemoryRevision
from omnigent.stores.artifact_store import ArtifactStore
from omnigent.stores.memory_store import MemoryStore

MAX_MEMORY_DOCUMENT_BYTES = 512 * 1024
DEFAULT_MEMORY_DOCUMENTS = {
    "MEMORY.md": "# Memory\n\nDurable context for the Conductor.\n",
    "profile/preferences.md": "# Preferences\n\n",
    "skills/observations.md": "# Skill observations\n\n",
}


def validate_memory_path(path: str) -> str:
    """Return a canonical safe Markdown path or raise ``ValueError``."""
    if not isinstance(path, str):
        raise ValueError("memory path must be a string")
    candidate = path.strip()
    if not candidate or len(candidate) > 1024:
        raise ValueError("memory path must be between 1 and 1024 characters")
    if "\\" in candidate or any(ord(char) < 32 for char in candidate):
        raise ValueError("memory path contains unsupported characters")
    parsed = PurePosixPath(candidate)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("memory path must be a relative canonical path")
    canonical = parsed.as_posix()
    if canonical != candidate or parsed.suffix.lower() != ".md":
        raise ValueError("memory path must be a canonical relative .md file")
    return canonical


class MemoryProvider(ABC):
    """Storage-neutral interface exposed to the Conductor and clients."""

    name: str

    @abstractmethod
    def list(self, user_id: str, *, prefix: str | None = None) -> list[MemoryDocument]:
        """List current documents."""
        ...

    @abstractmethod
    def read(self, user_id: str, path: str) -> tuple[MemoryDocument, str] | None:
        """Read current metadata and UTF-8 Markdown content."""
        ...

    @abstractmethod
    def write(
        self,
        user_id: str,
        path: str,
        content: str,
        *,
        expected_revision: int | None = None,
    ) -> MemoryDocument:
        """Write one immutable revision and advance the document."""
        ...

    @abstractmethod
    def history(self, user_id: str, path: str) -> builtin_list[MemoryRevision]:
        """List document revisions newest first."""
        ...

    @abstractmethod
    def read_revision(
        self, user_id: str, path: str, revision: int
    ) -> tuple[MemoryRevision, str] | None:
        """Read one immutable historical revision."""
        ...

    @abstractmethod
    def delete(self, user_id: str, path: str, *, expected_revision: int | None = None) -> bool:
        """Soft-delete the current document."""
        ...


class MarkdownArtifactMemoryProvider(MemoryProvider):
    """Markdown provider using ArtifactStore blobs and a SQL manifest."""

    name = "markdown"

    def __init__(self, memory_store: MemoryStore, artifact_store: ArtifactStore) -> None:
        self._memory_store = memory_store
        self._artifact_store = artifact_store

    def list(self, user_id: str, *, prefix: str | None = None) -> list[MemoryDocument]:
        canonical_prefix = None
        if prefix:
            stripped = prefix.strip().strip("/")
            if not stripped or ".." in PurePosixPath(stripped).parts or "\\" in stripped:
                raise ValueError("memory prefix must be a safe relative path")
            canonical_prefix = stripped + ("/" if not stripped.endswith(".md") else "")
        return self._memory_store.list(user_id, prefix=canonical_prefix)

    def read(self, user_id: str, path: str) -> tuple[MemoryDocument, str] | None:
        canonical = validate_memory_path(path)
        document = self._memory_store.get(user_id, canonical)
        if document is None:
            return None
        return document, self._read_blob(document.artifact_key)

    def write(
        self,
        user_id: str,
        path: str,
        content: str,
        *,
        expected_revision: int | None = None,
    ) -> MemoryDocument:
        canonical = validate_memory_path(path)
        if not isinstance(content, str):
            raise ValueError("memory content must be text")
        data = content.encode("utf-8")
        if len(data) > MAX_MEMORY_DOCUMENT_BYTES:
            raise ValueError(f"memory document exceeds {MAX_MEMORY_DOCUMENT_BYTES} UTF-8 bytes")
        checksum = hashlib.sha256(data).hexdigest()
        owner_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        path_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        artifact_key = f"conductor-memory/{owner_hash}/{path_hash}/{checksum}.md"
        if not self._artifact_store.exists(artifact_key):
            self._artifact_store.put(artifact_key, data)
        return self._memory_store.write(
            user_id,
            canonical,
            checksum=checksum,
            artifact_key=artifact_key,
            expected_revision=expected_revision,
        )

    def history(self, user_id: str, path: str) -> builtin_list[MemoryRevision]:
        return self._memory_store.history(user_id, validate_memory_path(path))

    def read_revision(
        self, user_id: str, path: str, revision: int
    ) -> tuple[MemoryRevision, str] | None:
        if revision < 1:
            raise ValueError("revision must be at least 1")
        revisions = self.history(user_id, path)
        match = next((item for item in revisions if item.revision == revision), None)
        if match is None:
            return None
        return match, self._read_blob(match.artifact_key)

    def delete(self, user_id: str, path: str, *, expected_revision: int | None = None) -> bool:
        return self._memory_store.delete(
            user_id,
            validate_memory_path(path),
            expected_revision=expected_revision,
        )

    def ensure_defaults(self, user_id: str) -> None:
        """Create the small canonical memory tree without overwriting user data."""
        for path, content in DEFAULT_MEMORY_DOCUMENTS.items():
            if self._memory_store.get(user_id, path) is None:
                self.write(user_id, path, content, expected_revision=0)

    def _read_blob(self, artifact_key: str) -> str:
        raw = self._artifact_store.get(artifact_key)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("stored Conductor memory is not valid UTF-8") from exc


class MemoryProviderRegistry:
    """Explicit provider registry; adding a backend does not change API contracts."""

    def __init__(self, providers: list[MemoryProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    def get(self, name: str) -> MemoryProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"unknown memory provider: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._providers)
