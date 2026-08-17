"""SQLAlchemy manifest store for immutable Conductor memory blobs."""

from __future__ import annotations

import hashlib
from builtins import list as builtin_list

from sqlalchemy import asc, desc, select

from omnigent.db.db_models import (
    SqlMemoryDocument,
    SqlMemoryRevision,
    current_workspace_id,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities import MemoryDocument, MemoryRevision
from omnigent.stores.memory_store import MemoryConflictError, MemoryStore


def _path_hash(path: str) -> bytes:
    return hashlib.sha256(path.encode("utf-8")).digest()


def _document_entity(row: SqlMemoryDocument) -> MemoryDocument:
    return MemoryDocument(
        user_id=row.user_id,
        path=row.path,
        revision=row.current_revision,
        checksum=row.checksum,
        artifact_key=row.artifact_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _revision_entity(row: SqlMemoryRevision) -> MemoryRevision:
    return MemoryRevision(
        user_id=row.user_id,
        path=row.path,
        revision=row.revision,
        checksum=row.checksum,
        artifact_key=row.artifact_key,
        created_at=row.created_at,
    )


class SqlAlchemyMemoryStore(MemoryStore):
    """Owner-scoped current manifest plus immutable revision history."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            engine, query_name_prefix="omnigent.memory_store"
        )

    def list(self, user_id: str, *, prefix: str | None = None) -> list[MemoryDocument]:
        with self._session("list_memory_documents") as session:
            stmt = (
                select(SqlMemoryDocument)
                .where(SqlMemoryDocument.workspace_id == current_workspace_id())
                .where(SqlMemoryDocument.user_id == user_id)
                .where(SqlMemoryDocument.deleted_at.is_(None))
                .order_by(asc(SqlMemoryDocument.path))
            )
            if prefix:
                stmt = stmt.where(SqlMemoryDocument.path.like(f"{prefix}%"))
            return [_document_entity(row) for row in session.execute(stmt).scalars().all()]

    def get(self, user_id: str, path: str) -> MemoryDocument | None:
        with self._session("get_memory_document") as session:
            row = session.get(
                SqlMemoryDocument,
                (current_workspace_id(), user_id, _path_hash(path)),
            )
            if row is None or row.path != path or row.deleted_at is not None:
                return None
            return _document_entity(row)

    def write(
        self,
        user_id: str,
        path: str,
        *,
        checksum: str,
        artifact_key: str,
        expected_revision: int | None,
    ) -> MemoryDocument:
        with self._session("write_memory_document") as session:
            key = (current_workspace_id(), user_id, _path_hash(path))
            row = session.get(SqlMemoryDocument, key)
            active_revision = (
                0 if row is None or row.deleted_at is not None else row.current_revision
            )
            if expected_revision is not None and expected_revision != active_revision:
                raise MemoryConflictError(
                    f"memory revision conflict for {path!r}: "
                    f"expected {expected_revision}, current {active_revision}"
                )
            revision = 1 if row is None else row.current_revision + 1
            timestamp = now_epoch()
            if row is None:
                row = SqlMemoryDocument(
                    user_id=user_id,
                    path_hash=key[2],
                    path=path,
                    current_revision=revision,
                    checksum=checksum,
                    artifact_key=artifact_key,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(row)
            else:
                if row.path != path:
                    raise RuntimeError("SHA-256 collision in memory document path")
                row.current_revision = revision
                row.checksum = checksum
                row.artifact_key = artifact_key
                row.updated_at = timestamp
                row.deleted_at = None
            session.add(
                SqlMemoryRevision(
                    user_id=user_id,
                    path_hash=key[2],
                    path=path,
                    revision=revision,
                    checksum=checksum,
                    artifact_key=artifact_key,
                    created_at=timestamp,
                )
            )
            session.flush()
            return _document_entity(row)

    def history(self, user_id: str, path: str) -> builtin_list[MemoryRevision]:
        with self._session("list_memory_revisions") as session:
            stmt = (
                select(SqlMemoryRevision)
                .where(SqlMemoryRevision.workspace_id == current_workspace_id())
                .where(SqlMemoryRevision.user_id == user_id)
                .where(SqlMemoryRevision.path_hash == _path_hash(path))
                .where(SqlMemoryRevision.path == path)
                .order_by(desc(SqlMemoryRevision.revision))
            )
            return [_revision_entity(row) for row in session.execute(stmt).scalars().all()]

    def delete(self, user_id: str, path: str, *, expected_revision: int | None) -> bool:
        with self._session("delete_memory_document") as session:
            row = session.get(
                SqlMemoryDocument,
                (current_workspace_id(), user_id, _path_hash(path)),
            )
            if row is None or row.path != path or row.deleted_at is not None:
                return False
            if expected_revision is not None and expected_revision != row.current_revision:
                raise MemoryConflictError(
                    f"memory revision conflict for {path!r}: "
                    f"expected {expected_revision}, current {row.current_revision}"
                )
            deleted_at = now_epoch()
            row.deleted_at = deleted_at
            row.updated_at = deleted_at
            return True
