"""API tests for Conductor binding, dashboard scope, and Markdown memory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

from omnigent.conductor import MarkdownArtifactMemoryProvider, MemoryProviderRegistry
from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ, AuthProvider
from omnigent.server.routes.conductor import create_conductor_router
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conductor_store.sqlalchemy_store import SqlAlchemyConductorStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.memory_store.sqlalchemy_store import SqlAlchemyMemoryStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore


class _HeaderAuth(AuthProvider):
    def get_user_id(self, request: HTTPConnection) -> str | None:
        return request.headers.get("X-Forwarded-Email")


def _client(db_uri: str, tmp_path: Path) -> tuple[TestClient, SqlAlchemyConversationStore]:
    conversations = SqlAlchemyConversationStore(db_uri)
    memory = MarkdownArtifactMemoryProvider(
        SqlAlchemyMemoryStore(db_uri),
        LocalArtifactStore(str(tmp_path / "artifacts")),
    )
    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(_request: object, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(
        create_conductor_router(
            SqlAlchemyConductorStore(db_uri),
            MemoryProviderRegistry([memory]),
            conversations,
        ),
        prefix="/v1",
    )
    return TestClient(app), conversations


def test_bind_dashboard_and_memory_round_trip(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    ordinary = conversations.create_conversation(agent_id="a" * 32, title="Ordinary")
    conductor = conversations.create_conversation(agent_id="b" * 32, title="My Conductor")

    initial = client.get("/v1/conductor")
    assert initial.status_code == 200
    assert initial.json()["conductor"] is None
    assert {item["id"] for item in initial.json()["sessions"]} == {
        conductor.id,
        ordinary.id,
    }

    bound = client.put(
        "/v1/conductor",
        json={"conversation_id": conductor.id, "memory_provider": "markdown"},
    )
    assert bound.status_code == 200
    assert bound.json()["conversation_id"] == conductor.id

    dashboard = client.get("/v1/conductor").json()
    assert [item["id"] for item in dashboard["sessions"]] == [ordinary.id]
    assert dashboard["memory_providers"] == ["markdown"]

    defaults = client.get("/v1/conductor/memory")
    assert defaults.status_code == 200
    assert len(defaults.json()["data"]) == 3

    written = client.put(
        "/v1/conductor/memory/document",
        json={"path": "projects/demo/overview.md", "content": "# Demo", "expected_revision": 0},
    )
    assert written.status_code == 200
    assert written.json()["revision"] == 1

    read = client.get(
        "/v1/conductor/memory/document", params={"path": "projects/demo/overview.md"}
    )
    assert read.status_code == 200
    assert read.json()["content"] == "# Demo"

    conflict = client.put(
        "/v1/conductor/memory/document",
        json={"path": "projects/demo/overview.md", "content": "stale", "expected_revision": 0},
    )
    assert conflict.status_code == 409


def test_rejects_binding_subagent_as_conductor(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    parent = conversations.create_conversation(agent_id="a" * 32)
    child = conversations.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        agent_id="b" * 32,
        title="worker:worker-1",
    )

    response = client.put("/v1/conductor", json={"conversation_id": child.id})
    assert response.status_code == 404


def test_runtime_authorization_is_bound_and_tree_scoped(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    conductor = conversations.create_conversation(agent_id="a" * 32)
    root = conversations.create_conversation(agent_id="b" * 32)
    child = conversations.create_conversation(
        kind="sub_agent",
        parent_conversation_id=root.id,
        agent_id="c" * 32,
        title="worker:owned-child",
    )
    assert client.put("/v1/conductor", json={"conversation_id": conductor.id}).status_code == 200

    authorized = client.get(
        f"/v1/conductor/sessions/{child.id}/authorization",
        params={"caller_session_id": conductor.id},
    )
    assert authorized.status_code == 200
    assert authorized.json()["root_conversation_id"] == root.id

    wrong_caller = client.get(
        f"/v1/conductor/sessions/{root.id}/authorization",
        params={"caller_session_id": root.id},
    )
    assert wrong_caller.status_code == 403
    self_target = client.get(
        f"/v1/conductor/sessions/{conductor.id}/authorization",
        params={"caller_session_id": conductor.id},
    )
    assert self_target.status_code == 400


def test_runtime_authorization_rejects_shared_and_foreign_sessions(
    db_uri: str, tmp_path: Path
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    memory = MarkdownArtifactMemoryProvider(
        SqlAlchemyMemoryStore(db_uri),
        LocalArtifactStore(str(tmp_path / "artifacts")),
    )
    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(_request: object, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(
        create_conductor_router(
            SqlAlchemyConductorStore(db_uri),
            MemoryProviderRegistry([memory]),
            conversations,
            auth_provider=_HeaderAuth(),
            permission_store=permissions,
        ),
        prefix="/v1",
    )
    client = TestClient(app)
    alice = {"X-Forwarded-Email": "alice@example.com"}
    conductor = conversations.create_conversation(agent_id="a" * 32)
    owned = conversations.create_conversation(agent_id="b" * 32)
    foreign = conversations.create_conversation(agent_id="c" * 32)
    permissions.grant("alice@example.com", conductor.id, LEVEL_OWNER)
    permissions.grant("alice@example.com", owned.id, LEVEL_OWNER)
    permissions.grant("bob@example.com", foreign.id, LEVEL_OWNER)
    permissions.grant("alice@example.com", foreign.id, LEVEL_READ)
    assert (
        client.put(
            "/v1/conductor", json={"conversation_id": conductor.id}, headers=alice
        ).status_code
        == 200
    )

    allowed = client.get(
        f"/v1/conductor/sessions/{owned.id}/authorization",
        params={"caller_session_id": conductor.id},
        headers=alice,
    )
    denied = client.get(
        f"/v1/conductor/sessions/{foreign.id}/authorization",
        params={"caller_session_id": conductor.id},
        headers=alice,
    )
    assert allowed.status_code == 200
    assert denied.status_code == 404
