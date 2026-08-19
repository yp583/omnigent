"""SQLAlchemy-backed Conductor identity store."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import IntegrityError

from omnigent.db.db_models import SqlConductor, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker, now_epoch
from omnigent.entities import Conductor
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.conductor_store import ConductorStore


def _to_entity(row: SqlConductor) -> Conductor:
    config: dict[str, Any] = {}
    if row.config:
        decoded = json.loads(row.config)
        if isinstance(decoded, dict):
            config = decoded
    return Conductor(
        user_id=row.user_id,
        conversation_id=row.conversation_id,
        memory_provider=row.memory_provider,
        config=config,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyConductorStore(ConductorStore):
    """Persist one Conductor binding per ``(workspace, user)``."""

    def __init__(self, storage_location: str) -> None:
        super().__init__(storage_location)
        engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            engine, query_name_prefix="omnigent.conductor_store"
        )

    def get(self, user_id: str) -> Conductor | None:
        with self._session("get_conductor") as session:
            row = session.get(SqlConductor, (current_workspace_id(), user_id))
            return _to_entity(row) if row is not None else None

    def create(
        self,
        user_id: str,
        conversation_id: str,
        *,
        memory_provider: str = "markdown",
        config: dict[str, Any] | None = None,
    ) -> Conductor:
        row = SqlConductor(
            user_id=user_id,
            conversation_id=conversation_id,
            memory_provider=memory_provider,
            config=json.dumps(config, separators=(",", ":")) if config else None,
            created_at=now_epoch(),
        )
        try:
            with self._session("create_conductor") as session:
                session.add(row)
                session.flush()
                return _to_entity(row)
        except IntegrityError as exc:
            raise OmnigentError(
                "A Conductor already exists for this user",
                code=ErrorCode.ALREADY_EXISTS,
            ) from exc

    def update(
        self,
        user_id: str,
        *,
        conversation_id: str | None = None,
        memory_provider: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Conductor | None:
        with self._session("update_conductor") as session:
            row = session.get(SqlConductor, (current_workspace_id(), user_id))
            if row is None:
                return None
            if conversation_id is not None:
                row.conversation_id = conversation_id
            if memory_provider is not None:
                row.memory_provider = memory_provider
            if config is not None:
                row.config = json.dumps(config, separators=(",", ":")) if config else None
            row.updated_at = now_epoch()
            session.flush()
            return _to_entity(row)
