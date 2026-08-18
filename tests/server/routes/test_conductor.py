"""API tests for Conductor binding, dashboard scope, and Markdown memory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

from omnigent.conductor import MarkdownArtifactMemoryProvider, MemoryProviderRegistry
from omnigent.errors import OmnigentError
from omnigent.server.auth import (
    LEVEL_EDIT,
    LEVEL_OWNER,
    LEVEL_READ,
    RESERVED_USER_LOCAL,
    RESERVED_USER_PUBLIC,
    AuthProvider,
)
from omnigent.server.routes.conductor import create_conductor_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conductor_store.sqlalchemy_store import SqlAlchemyConductorStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.memory_store.sqlalchemy_store import SqlAlchemyMemoryStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ORDINARY_AGENT_ID = "a" * 32
CONDUCTOR_AGENT_ID = "b" * 32


class _HeaderAuth(AuthProvider):
    def get_user_id(self, request: HTTPConnection) -> str | None:
        return request.headers.get("X-Forwarded-Email")


def _client(db_uri: str, tmp_path: Path) -> tuple[TestClient, SqlAlchemyConversationStore]:
    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    agents.create(ORDINARY_AGENT_ID, "ordinary", "bundles/ordinary")
    agents.create(CONDUCTOR_AGENT_ID, "conductor", "bundles/conductor")
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
            agents,
        ),
        prefix="/v1",
    )
    return TestClient(app), conversations


def test_bind_dashboard_and_memory_round_trip(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    ordinary = conversations.create_conversation(agent_id=ORDINARY_AGENT_ID, title="Ordinary")
    conductor = conversations.create_conversation(
        agent_id=CONDUCTOR_AGENT_ID, title="My Conductor"
    )

    initial = client.get("/v1/conductor")
    assert initial.status_code == 200
    assert initial.json()["conductor"] is None
    assert {item["id"] for item in initial.json()["sessions"]} == {
        conductor.id,
        ordinary.id,
    }
    eligibility = {item["id"]: item["conductor_eligible"] for item in initial.json()["sessions"]}
    assert eligibility == {conductor.id: True, ordinary.id: False}

    rejected = client.put("/v1/conductor", json={"conversation_id": ordinary.id})
    assert rejected.status_code == 400
    assert "built-in Conductor agent" in rejected.json()["error"]["message"]

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


def test_ensure_creates_one_hidden_runner_bound_chat(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    anchor = conversations.create_conversation(
        agent_id=ORDINARY_AGENT_ID,
        title="Current work",
        runner_id="runner-local",
    )

    created = client.post("/v1/conductor/ensure")
    repeated = client.post("/v1/conductor/ensure")

    assert created.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["conversation_id"] == created.json()["conversation_id"]
    conductor = conversations.get_conversation(created.json()["conversation_id"])
    assert conductor is not None
    assert conductor.agent_id == CONDUCTOR_AGENT_ID
    assert conductor.runner_id == anchor.runner_id
    assert conductor.labels["omnigent.conductor"] == "true"
    dashboard_ids = {row["id"] for row in client.get("/v1/conductor").json()["sessions"]}
    assert dashboard_ids == {anchor.id}


def test_ensure_requires_an_owned_runner_anchor(db_uri: str, tmp_path: Path) -> None:
    client, _conversations = _client(db_uri, tmp_path)

    response = client.post("/v1/conductor/ensure")

    assert response.status_code == 409
    assert "available runner" in response.json()["error"]["message"]


def test_update_rejects_an_unknown_permission_mode(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    conductor = conversations.create_conversation(agent_id=CONDUCTOR_AGENT_ID)
    assert client.put("/v1/conductor", json={"conversation_id": conductor.id}).status_code == 200

    response = client.patch(
        "/v1/conductor",
        json={"config": {"permission_mode": "unrestricted-ish"}},
    )

    assert response.status_code == 400
    assert "not a supported" in response.json()["error"]["message"]


def test_rejects_binding_subagent_as_conductor(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    parent = conversations.create_conversation(agent_id=ORDINARY_AGENT_ID)
    child = conversations.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        agent_id=CONDUCTOR_AGENT_ID,
        title="worker:worker-1",
    )

    response = client.put("/v1/conductor", json={"conversation_id": child.id})
    assert response.status_code == 404


def test_runtime_authorization_is_bound_and_tree_scoped(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    conductor = conversations.create_conversation(agent_id=CONDUCTOR_AGENT_ID)
    root = conversations.create_conversation(agent_id=ORDINARY_AGENT_ID)
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


def test_legacy_ordinary_binding_is_hidden_and_repaired(db_uri: str, tmp_path: Path) -> None:
    client, conversations = _client(db_uri, tmp_path)
    ordinary = conversations.create_conversation(
        agent_id=ORDINARY_AGENT_ID, title="Existing work transcript"
    )
    conductor = conversations.create_conversation(
        agent_id=CONDUCTOR_AGENT_ID, title="Fresh Conductor"
    )
    store = SqlAlchemyConductorStore(db_uri)
    store.create(RESERVED_USER_LOCAL, ordinary.id)
    conversations.set_labels(ordinary.id, {"omnigent.conductor": "true"})

    dashboard = client.get("/v1/conductor")
    assert dashboard.status_code == 200
    assert dashboard.json()["conductor"] is None

    rebound = client.put("/v1/conductor", json={"conversation_id": conductor.id})
    assert rebound.status_code == 200
    assert rebound.json()["conversation_id"] == conductor.id
    assert store.get(RESERVED_USER_LOCAL).conversation_id == conductor.id  # type: ignore[union-attr]
    assert "omnigent.conductor" not in conversations.get_conversation(ordinary.id).labels  # type: ignore[union-attr]
    assert conversations.get_conversation(conductor.id).labels["omnigent.conductor"] == "true"  # type: ignore[union-attr]


def test_shared_sessions_are_listed_readable_and_steerable_only_with_edit(
    db_uri: str, tmp_path: Path
) -> None:
    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    agents.create(ORDINARY_AGENT_ID, "ordinary", "bundles/ordinary")
    agents.create(CONDUCTOR_AGENT_ID, "conductor", "bundles/conductor")
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
            agents,
            auth_provider=_HeaderAuth(),
            permission_store=permissions,
        ),
        prefix="/v1",
    )
    client = TestClient(app)
    alice = {"X-Forwarded-Email": "alice@example.com"}
    conductor = conversations.create_conversation(agent_id=CONDUCTOR_AGENT_ID)
    owned = conversations.create_conversation(agent_id=ORDINARY_AGENT_ID)
    shared = conversations.create_conversation(agent_id="c" * 32, title="Team review")
    shared_child = conversations.create_conversation(
        kind="sub_agent",
        parent_conversation_id=shared.id,
        agent_id="d" * 32,
        title="worker:shared-child",
    )
    foreign = conversations.create_conversation(agent_id="e" * 32)
    public_only = conversations.create_conversation(agent_id="f" * 32)
    permissions.grant("alice@example.com", conductor.id, LEVEL_OWNER)
    permissions.grant("alice@example.com", owned.id, LEVEL_OWNER)
    permissions.grant("bob@example.com", shared.id, LEVEL_OWNER)
    permissions.grant("alice@example.com", shared.id, LEVEL_READ)
    permissions.grant("bob@example.com", foreign.id, LEVEL_OWNER)
    permissions.grant("bob@example.com", public_only.id, LEVEL_OWNER)
    permissions.grant(RESERVED_USER_PUBLIC, public_only.id, LEVEL_READ)
    assert (
        client.put(
            "/v1/conductor", json={"conversation_id": conductor.id}, headers=alice
        ).status_code
        == 200
    )

    sessions = {
        item["id"]: item for item in client.get("/v1/conductor", headers=alice).json()["sessions"]
    }
    assert set(sessions) == {owned.id, shared.id}
    assert sessions[owned.id]["access_scope"] == "personal"
    assert sessions[owned.id]["owner_user_id"] == "alice@example.com"
    assert sessions[owned.id]["can_steer"] is True
    assert sessions[shared.id]["access_scope"] == "shared"
    assert sessions[shared.id]["owner_user_id"] == "bob@example.com"
    assert sessions[shared.id]["permission_level"] == LEVEL_READ
    assert sessions[shared.id]["can_steer"] is False
    assert sessions[shared.id]["conductor_eligible"] is False

    owned_read = client.get(
        f"/v1/conductor/sessions/{owned.id}/authorization",
        params={"caller_session_id": conductor.id},
        headers=alice,
    )
    shared_read = client.get(
        f"/v1/conductor/sessions/{shared_child.id}/authorization",
        params={"caller_session_id": conductor.id, "action": "read"},
        headers=alice,
    )
    shared_steer = client.get(
        f"/v1/conductor/sessions/{shared.id}/authorization",
        params={"caller_session_id": conductor.id, "action": "steer"},
        headers=alice,
    )
    foreign_read = client.get(
        f"/v1/conductor/sessions/{foreign.id}/authorization",
        params={"caller_session_id": conductor.id},
        headers=alice,
    )
    public_read = client.get(
        f"/v1/conductor/sessions/{public_only.id}/authorization",
        params={"caller_session_id": conductor.id},
        headers=alice,
    )
    assert owned_read.status_code == 200
    assert owned_read.json()["allowed"] is True
    assert shared_read.status_code == 200
    assert shared_read.json()["allowed"] is True
    assert shared_read.json()["access_scope"] == "shared"
    assert shared_read.json()["root_conversation_id"] == shared.id
    assert shared_steer.status_code == 200
    assert shared_steer.json()["allowed"] is False
    assert shared_steer.json()["can_read"] is True
    assert foreign_read.status_code == 404
    assert public_read.status_code == 404

    permissions.grant("alice@example.com", shared.id, LEVEL_EDIT)
    editable = client.get(
        f"/v1/conductor/sessions/{shared.id}/authorization",
        params={"caller_session_id": conductor.id, "action": "steer"},
        headers=alice,
    )
    assert editable.status_code == 200
    assert editable.json()["allowed"] is True
    assert editable.json()["permission_level"] == LEVEL_EDIT

    assert permissions.revoke("alice@example.com", shared.id) is True
    revoked = client.get(
        f"/v1/conductor/sessions/{shared.id}/authorization",
        params={"caller_session_id": conductor.id, "action": "read"},
        headers=alice,
    )
    assert revoked.status_code == 404
    remaining_ids = {
        item["id"] for item in client.get("/v1/conductor", headers=alice).json()["sessions"]
    }
    assert shared.id not in remaining_ids
