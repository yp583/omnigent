"""Provider-neutral persistence contract for Conductor memory metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod
from builtins import list as builtin_list

from omnigent.entities import MemoryDocument, MemoryRevision


class MemoryConflictError(RuntimeError):
    """A document changed after the caller's expected revision."""


class MemoryStore(ABC):
    """Owner-private manifest for immutable Markdown blob revisions."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def list(self, user_id: str, *, prefix: str | None = None) -> list[MemoryDocument]:
        """List current, non-deleted documents for one owner."""
        ...

    @abstractmethod
    def get(self, user_id: str, path: str) -> MemoryDocument | None:
        """Return the current document metadata, excluding deleted rows."""
        ...

    @abstractmethod
    def write(
        self,
        user_id: str,
        path: str,
        *,
        checksum: str,
        artifact_key: str,
        expected_revision: int | None,
    ) -> MemoryDocument:
        """Append a revision and atomically advance the current manifest."""
        ...

    @abstractmethod
    def history(self, user_id: str, path: str) -> builtin_list[MemoryRevision]:
        """List immutable revisions newest first."""
        ...

    @abstractmethod
    def delete(self, user_id: str, path: str, *, expected_revision: int | None) -> bool:
        """Soft-delete a current document with optional revision checking."""
        ...
