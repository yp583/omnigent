"""Persistence contract for each user's singleton Conductor session."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities import Conductor


class ConductorStore(ABC):
    """Owner-scoped Conductor identity and configuration persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def get(self, user_id: str) -> Conductor | None:
        """Return the user's Conductor binding, if it exists."""
        ...

    @abstractmethod
    def create(
        self,
        user_id: str,
        conversation_id: str,
        *,
        memory_provider: str = "markdown",
        config: dict[str, Any] | None = None,
    ) -> Conductor:
        """Create the user's singleton Conductor binding."""
        ...

    @abstractmethod
    def update(
        self,
        user_id: str,
        *,
        conversation_id: str | None = None,
        memory_provider: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Conductor | None:
        """Update binding/provider/config fields, or return ``None`` if absent.

        ``conversation_id`` is an internal repair primitive. The HTTP PATCH
        surface never exposes transcript replacement; the Conductor route uses
        it only to recover a legacy binding that does not point at a real
        Conductor-agent session.
        """
        ...
