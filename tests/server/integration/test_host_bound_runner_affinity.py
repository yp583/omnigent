"""Host-bound session runner affinity integration coverage."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.host_store import HostStore
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio


async def test_patch_cannot_replace_server_issued_host_runner(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH cannot move a host-bound session to an arbitrary runner."""
    from omnigent.server.routes import sessions as sessions_module

    async def _get_runner_client(_session_id: str, _runner_router: Any) -> None:
        return None

    async def _ensure_runner_relay_ready(
        _session_id: str,
        _runner_id: str,
        _runner_client: None,
        _conversation_store: Any,
    ) -> None:
        return None

    monkeypatch.setattr(
        sessions_module,
        "_registered_runner_id",
        lambda _router, runner_id, **_kwargs: runner_id,
    )
    monkeypatch.setattr(sessions_module, "_get_runner_client", _get_runner_client)
    monkeypatch.setattr(
        sessions_module,
        "_ensure_runner_relay_ready",
        _ensure_runner_relay_ready,
    )

    agent = await create_test_agent(client)
    created = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    host_id = uuid.uuid4().hex
    issued_runner_id = "runner_token_server_issued"
    HostStore(db_uri).upsert_on_connect(host_id, "affinity-host", "local")
    store = SqlAlchemyConversationStore(db_uri)
    assert store.set_runner_id(session_id, issued_runner_id) is True
    assert store.set_host_id(session_id, host_id, "/tmp/affinity-workspace") is not None

    replacement = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": "runner_arbitrary_local"},
    )
    assert replacement.status_code == 409, replacement.text
    assert replacement.json()["error"]["code"] == "conflict"
    after_replacement = store.get_conversation(session_id)
    assert after_replacement is not None
    assert after_replacement.runner_id == issued_runner_id
    assert after_replacement.host_id == host_id

    reaffirm = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": issued_runner_id},
    )
    assert reaffirm.status_code == 200, reaffirm.text
    assert reaffirm.json()["runner_id"] == issued_runner_id

    cleared = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": ""},
    )
    assert cleared.status_code == 200, cleared.text
    after_clear = store.get_conversation(session_id)
    assert after_clear is not None
    assert after_clear.runner_id is None
    assert after_clear.host_id == host_id

    replacement_after_clear = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": "runner_arbitrary_local"},
    )
    assert replacement_after_clear.status_code == 409, replacement_after_clear.text
    final = store.get_conversation(session_id)
    assert final is not None
    assert final.runner_id is None
    assert final.host_id == host_id


async def test_non_empty_patch_loses_atomic_race_to_runner_rotation(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host-launch rotation wins before its host-id write completes."""
    from omnigent.server.routes import sessions as sessions_module

    monkeypatch.setattr(
        sessions_module,
        "_registered_runner_id",
        lambda _router, runner_id, **_kwargs: runner_id,
    )
    agent = await create_test_agent(client)
    created = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    store = SqlAlchemyConversationStore(db_uri)
    assert store.set_runner_id(session_id, "runner_observed_hostless") is True

    original = SqlAlchemyConversationStore.replace_runner_id_if_hostless
    raced = False

    def _host_binds_before_patch_write(
        route_store: SqlAlchemyConversationStore,
        conversation_id: str,
        expected_runner_id: str | None,
        runner_id: str,
    ) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            route_store.replace_runner_id(conversation_id, "runner_server_rotated")
        return original(
            route_store,
            conversation_id,
            expected_runner_id,
            runner_id,
        )

    monkeypatch.setattr(
        SqlAlchemyConversationStore,
        "replace_runner_id_if_hostless",
        _host_binds_before_patch_write,
    )
    response = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": "runner_patch_candidate"},
    )

    assert response.status_code == 409, response.text
    assert raced is True
    final = store.get_conversation(session_id)
    assert final is not None
    assert final.runner_id == "runner_server_rotated"
    assert final.host_id is None


async def test_clear_patch_loses_atomic_race_to_runner_rotation(
    client: httpx.AsyncClient,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clear cannot erase a host runner rotated after the route read."""
    host_id = uuid.uuid4().hex
    HostStore(db_uri).upsert_on_connect(host_id, "clear-race-host", "local")
    agent = await create_test_agent(client)
    created = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    store = SqlAlchemyConversationStore(db_uri)
    assert store.set_runner_id(session_id, "runner_observed") is True
    store.set_host_id(
        session_id,
        host_id,
        workspace="/tmp/affinity-clear-race-workspace",
    )

    original = SqlAlchemyConversationStore.clear_runner_id_if_matches
    raced = False

    def _runner_rotates_before_clear(
        route_store: SqlAlchemyConversationStore,
        conversation_id: str,
        expected_runner_id: str | None,
        expected_host_id: str | None,
    ) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            route_store.replace_runner_id(conversation_id, "runner_fresh")
        return original(
            route_store,
            conversation_id,
            expected_runner_id,
            expected_host_id,
        )

    monkeypatch.setattr(
        SqlAlchemyConversationStore,
        "clear_runner_id_if_matches",
        _runner_rotates_before_clear,
    )
    response = await client.patch(
        f"/v1/sessions/{session_id}",
        json={"runner_id": ""},
    )

    assert response.status_code == 409, response.text
    assert raced is True
    final = store.get_conversation(session_id)
    assert final is not None
    assert final.runner_id == "runner_fresh"
    assert final.host_id == host_id
