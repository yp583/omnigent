"""Owner-private Conductor identity, dashboard, and Markdown-memory API."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from omnigent.conductor import MarkdownArtifactMemoryProvider, MemoryProviderRegistry
from omnigent.entities import Conductor, Conversation, MemoryDocument, MemoryRevision
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import (
    LEVEL_EDIT,
    LEVEL_OWNER,
    LEVEL_READ,
    RESERVED_USER_LOCAL,
    AuthProvider,
)
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.schemas import (
    BindConductorRequest,
    DeleteConductorMemoryRequest,
    UpdateConductorRequest,
    WriteConductorMemoryRequest,
)
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conductor_store import ConductorStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.memory_store import MemoryConflictError
from omnigent.stores.permission_store import PermissionStore

CONDUCTOR_LABEL_KEY = "omnigent.conductor"
CONDUCTOR_LABEL_VALUE = "true"
CONDUCTOR_AGENT_NAME = "conductor"


def _scope_user(user_id: str | None) -> str:
    return user_id if user_id is not None else RESERVED_USER_LOCAL


def _document_dict(document: MemoryDocument, content: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": document.path,
        "revision": document.revision,
        "checksum": document.checksum,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if content is not None:
        result["content"] = content
    return result


def _revision_dict(revision: MemoryRevision, content: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": revision.path,
        "revision": revision.revision,
        "checksum": revision.checksum,
        "created_at": revision.created_at,
    }
    if content is not None:
        result["content"] = content
    return result


def _conductor_dict(conductor: Conductor) -> dict[str, Any]:
    return {
        "conversation_id": conductor.conversation_id,
        "memory_provider": conductor.memory_provider,
        "config": conductor.config,
        "created_at": conductor.created_at,
        "updated_at": conductor.updated_at,
    }


def create_conductor_router(
    conductor_store: ConductorStore,
    memory_providers: MemoryProviderRegistry,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    *,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the Conductor API with permission-aware cross-session scope."""
    router = APIRouter()

    def _provider_for(conductor: Conductor):
        try:
            return memory_providers.get(conductor.memory_provider)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc

    def _is_conductor_session(conversation: Conversation | None) -> bool:
        if (
            conversation is None
            or conversation.parent_conversation_id is not None
            or conversation.agent_id is None
        ):
            return False
        agent = agent_store.get(conversation.agent_id)
        return agent is not None and agent.name == CONDUCTOR_AGENT_NAME

    def _binding_is_valid(conductor: Conductor | None) -> bool:
        if conductor is None:
            return False
        conversation = conversation_store.get_conversation(conductor.conversation_id)
        return _is_conductor_session(conversation)

    def _load(scope_user: str) -> Conductor:
        conductor = conductor_store.get(scope_user)
        if not _binding_is_valid(conductor):
            raise OmnigentError("Conductor is not configured", code=ErrorCode.NOT_FOUND)
        assert conductor is not None
        return conductor

    def _accessible_sessions(
        user_id: str | None, conductor_id: str | None
    ) -> list[dict[str, Any]]:
        scope_user = _scope_user(user_id)
        page = conversation_store.list_conversations(
            limit=200,
            kind="default",
            has_agent_id=True,
            accessible_by=scope_user if permission_store is not None else None,
            include_archived=False,
            order="desc",
            sort_by="updated_at",
        )
        sessions = [session for session in page.data if session.id != conductor_id]
        agent_names = agent_store.get_names(
            list({session.agent_id for session in sessions if session.agent_id is not None})
        )
        grants_by_session = (
            permission_store.list_for_sessions([session.id for session in sessions])
            if permission_store is not None
            else {}
        )
        user_is_admin = (
            permission_store.is_admin(scope_user) if permission_store is not None else False
        )
        rows: list[dict[str, Any]] = []
        for session in sessions:
            if permission_store is None:
                access_scope = "personal"
                owner: str | None = scope_user
                permission_level = LEVEL_OWNER
            else:
                grants = grants_by_session.get(session.id, [])
                owner = next(
                    (grant.user_id for grant in grants if grant.level >= LEVEL_OWNER),
                    None,
                )
                direct_level = next(
                    (grant.level for grant in grants if grant.user_id == scope_user),
                    None,
                )
                permission_level = LEVEL_OWNER if user_is_admin else direct_level or 0
                access_scope = "personal" if owner in (None, scope_user) else "shared"
            agent_name = agent_names.get(session.agent_id or "")
            rows.append(
                {
                    "id": session.id,
                    "title": session.title,
                    "status": session.live_status or "idle",
                    "pending_approval_count": session.pending_elicitation_count or 0,
                    "updated_at": session.updated_at,
                    "created_at": session.created_at,
                    "workspace": session.workspace,
                    "git_branch": session.git_branch,
                    "task_summary": session.task_summary,
                    "agent_name": agent_name,
                    "conductor_eligible": (
                        access_scope == "personal" and agent_name == CONDUCTOR_AGENT_NAME
                    ),
                    "access_scope": access_scope,
                    "owner_user_id": owner,
                    "permission_level": permission_level,
                    "can_steer": permission_level >= LEVEL_EDIT,
                }
            )
        return rows

    def _target_access(
        user_id: str | None, target_id: str
    ) -> tuple[Conversation, str, str | None, int, bool] | None:
        """Resolve current read/steer access to a target's permission root."""
        target = conversation_store.get_conversation(target_id)
        if target is None:
            return None
        if permission_store is None:
            return target, "personal", _scope_user(user_id), LEVEL_OWNER, True
        root_id = target.root_conversation_id or target.id
        scope_user = _scope_user(user_id)
        direct_grant = permission_store.get(scope_user, root_id)
        if direct_grant is None or direct_grant.level < LEVEL_READ:
            # Conductor scope is deliberately narrower than ordinary session
            # reads: public-link access does not count as a teammate sharing a
            # chat directly with this user.
            return None
        owner = conversation_store.get_session_owner(root_id)
        permission_level = (
            LEVEL_OWNER if permission_store.is_admin(scope_user) else direct_grant.level
        )
        access_scope = "personal" if owner in (None, scope_user) else "shared"
        can_steer = permission_level >= LEVEL_EDIT
        return target, access_scope, owner, permission_level, can_steer

    def _require_active_caller(scope_user: str, caller_session_id: str) -> Conductor:
        conductor = _load(scope_user)
        if conductor.conversation_id != caller_session_id:
            raise OmnigentError(
                "Only the active Conductor session may use Conductor runtime tools",
                code=ErrorCode.FORBIDDEN,
            )
        return conductor

    @router.get("/conductor")
    async def get_conductor(request: Request) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        scope_user = _scope_user(user_id)
        conductor = await asyncio.to_thread(conductor_store.get, scope_user)
        if conductor is not None and not await asyncio.to_thread(_binding_is_valid, conductor):
            # Legacy builds allowed any ordinary transcript to be bound. Treat
            # those rows as unconfigured so the UI cannot route into unrelated
            # history; PUT repairs the row once a real Conductor chat is made.
            conductor = None
        sessions = await asyncio.to_thread(
            _accessible_sessions,
            user_id,
            conductor.conversation_id if conductor is not None else None,
        )
        return {
            "object": "conductor.dashboard",
            "conductor": _conductor_dict(conductor) if conductor is not None else None,
            "memory_providers": memory_providers.names(),
            "sessions": sessions,
        }

    @router.put("/conductor")
    async def bind_conductor(request: Request, body: BindConductorRequest) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        scope_user = _scope_user(user_id)
        try:
            provider = memory_providers.get(body.memory_provider)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        conversation = await asyncio.to_thread(
            conversation_store.get_conversation, body.conversation_id
        )
        if conversation is None or conversation.parent_conversation_id is not None:
            raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)
        if not await asyncio.to_thread(_is_conductor_session, conversation):
            raise OmnigentError(
                "Only a session using the built-in Conductor agent can be made Conductor",
                code=ErrorCode.INVALID_INPUT,
            )
        if permission_store is not None:
            owner = await asyncio.to_thread(
                conversation_store.get_session_owner, body.conversation_id
            )
            if user_id is None or owner != user_id:
                raise OmnigentError(
                    "Only a session's owner can make it their Conductor",
                    code=ErrorCode.FORBIDDEN,
                )
            access = await asyncio.to_thread(permission_store.get, user_id, body.conversation_id)
            if access is None or access.level < LEVEL_OWNER:
                raise OmnigentError("Owner permission is required", code=ErrorCode.FORBIDDEN)
        existing = await asyncio.to_thread(conductor_store.get, scope_user)
        if existing is not None:
            if await asyncio.to_thread(_binding_is_valid, existing):
                if existing.conversation_id != body.conversation_id:
                    raise OmnigentError(
                        "A Conductor is already configured; update it instead of "
                        "replacing its transcript",
                        code=ErrorCode.CONFLICT,
                    )
                return _conductor_dict(existing)
            repaired = await asyncio.to_thread(
                conductor_store.update,
                scope_user,
                conversation_id=body.conversation_id,
                memory_provider=body.memory_provider,
            )
            if repaired is None:
                raise OmnigentError(
                    "Conductor binding disappeared during repair",
                    code=ErrorCode.CONFLICT,
                )
            await asyncio.to_thread(
                conversation_store.delete_label,
                existing.conversation_id,
                CONDUCTOR_LABEL_KEY,
            )
            conductor = repaired
        else:
            conductor = await asyncio.to_thread(
                conductor_store.create,
                scope_user,
                body.conversation_id,
                memory_provider=body.memory_provider,
            )
        await asyncio.to_thread(
            conversation_store.set_labels,
            body.conversation_id,
            {CONDUCTOR_LABEL_KEY: CONDUCTOR_LABEL_VALUE},
        )
        if isinstance(provider, MarkdownArtifactMemoryProvider):
            await asyncio.to_thread(provider.ensure_defaults, scope_user)
        return _conductor_dict(conductor)

    @router.patch("/conductor")
    async def update_conductor(request: Request, body: UpdateConductorRequest) -> dict[str, Any]:
        user_id = require_user(request, auth_provider)
        scope_user = _scope_user(user_id)
        if body.memory_provider is not None:
            try:
                memory_providers.get(body.memory_provider)
            except ValueError as exc:
                raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        conductor = await asyncio.to_thread(
            conductor_store.update,
            scope_user,
            memory_provider=body.memory_provider,
            config=body.config,
        )
        if conductor is None:
            raise OmnigentError("Conductor is not configured", code=ErrorCode.NOT_FOUND)
        if body.memory_provider is not None:
            provider = _provider_for(conductor)
            if isinstance(provider, MarkdownArtifactMemoryProvider):
                await asyncio.to_thread(provider.ensure_defaults, scope_user)
        return _conductor_dict(conductor)

    @router.get("/conductor/sessions/{session_id}/authorization")
    async def authorize_conductor_session(
        request: Request,
        session_id: str,
        caller_session_id: str = Query(min_length=1),
        action: Literal["read", "steer"] = Query(default="read"),
    ) -> dict[str, Any]:
        """Authorize a Conductor read or steer against an accessible tree."""
        user_id = require_user(request, auth_provider)
        scope_user = _scope_user(user_id)
        conductor = await asyncio.to_thread(_require_active_caller, scope_user, caller_session_id)
        if session_id == conductor.conversation_id:
            raise OmnigentError("The Conductor cannot target itself", code=ErrorCode.INVALID_INPUT)
        target_access = await asyncio.to_thread(_target_access, user_id, session_id)
        if target_access is None:
            # Do not reveal whether an inaccessible session exists.
            raise OmnigentError("Session not found", code=ErrorCode.NOT_FOUND)
        target, access_scope, owner, permission_level, can_steer = target_access
        return {
            "allowed": action == "read" or can_steer,
            "action": action,
            "can_read": True,
            "can_steer": can_steer,
            "access_scope": access_scope,
            "owner_user_id": owner,
            "permission_level": permission_level,
            "session_id": target.id,
            "root_conversation_id": target.root_conversation_id,
            "parent_session_id": target.parent_conversation_id,
        }

    @router.get("/conductor/memory")
    async def list_memory(
        request: Request, prefix: str | None = Query(default=None)
    ) -> dict[str, Any]:
        scope_user = _scope_user(require_user(request, auth_provider))
        conductor = await asyncio.to_thread(_load, scope_user)
        provider = _provider_for(conductor)
        try:
            documents = await asyncio.to_thread(provider.list, scope_user, prefix=prefix)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        return {"object": "list", "data": [_document_dict(item) for item in documents]}

    @router.get("/conductor/memory/document")
    async def read_memory(request: Request, path: str = Query()) -> dict[str, Any]:
        scope_user = _scope_user(require_user(request, auth_provider))
        conductor = await asyncio.to_thread(_load, scope_user)
        try:
            result = await asyncio.to_thread(_provider_for(conductor).read, scope_user, path)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        if result is None:
            raise OmnigentError("Memory document not found", code=ErrorCode.NOT_FOUND)
        return _document_dict(*result)

    @router.put("/conductor/memory/document")
    async def write_memory(request: Request, body: WriteConductorMemoryRequest) -> dict[str, Any]:
        scope_user = _scope_user(require_user(request, auth_provider))
        conductor = await asyncio.to_thread(_load, scope_user)
        try:
            document = await asyncio.to_thread(
                _provider_for(conductor).write,
                scope_user,
                body.path,
                body.content,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        except MemoryConflictError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.CONFLICT) from exc
        return _document_dict(document, body.content)

    @router.delete("/conductor/memory/document")
    async def delete_memory(
        request: Request, body: DeleteConductorMemoryRequest
    ) -> dict[str, Any]:
        scope_user = _scope_user(require_user(request, auth_provider))
        conductor = await asyncio.to_thread(_load, scope_user)
        try:
            deleted = await asyncio.to_thread(
                _provider_for(conductor).delete,
                scope_user,
                body.path,
                expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        except MemoryConflictError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.CONFLICT) from exc
        if not deleted:
            raise OmnigentError("Memory document not found", code=ErrorCode.NOT_FOUND)
        return {"object": "memory.deleted", "path": body.path, "deleted": True}

    @router.get("/conductor/memory/history")
    async def memory_history(
        request: Request,
        path: str = Query(),
        revision: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        scope_user = _scope_user(require_user(request, auth_provider))
        conductor = await asyncio.to_thread(_load, scope_user)
        provider = _provider_for(conductor)
        try:
            if revision is not None:
                result = await asyncio.to_thread(
                    provider.read_revision, scope_user, path, revision
                )
                if result is None:
                    raise OmnigentError("Memory revision not found", code=ErrorCode.NOT_FOUND)
                return _revision_dict(*result)
            revisions = await asyncio.to_thread(provider.history, scope_user, path)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        return {"object": "list", "data": [_revision_dict(item) for item in revisions]}

    return router
