"""Conductor and durable Markdown-memory domain entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Conductor:
    """A user's singleton meta-session and its provider-neutral settings."""

    user_id: str
    conversation_id: str
    memory_provider: str
    created_at: int
    updated_at: int | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryDocument:
    """The current revision of one owner-private Markdown memory document."""

    user_id: str
    path: str
    revision: int
    checksum: str
    artifact_key: str
    created_at: int
    updated_at: int
    deleted_at: int | None = None


@dataclass
class MemoryRevision:
    """One immutable revision in a Markdown memory document's history."""

    user_id: str
    path: str
    revision: int
    checksum: str
    artifact_key: str
    created_at: int
