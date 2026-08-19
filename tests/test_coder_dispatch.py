"""Tests for Coder-backed Omni host discovery and ranking."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent import coder_dispatch

_GIB = 1024.0**3


def _workspace(workspace_id: str, name: str, agent_id: str) -> dict[str, Any]:
    """Build the healthy Coder workspace shape used by discovery."""
    return {
        "id": workspace_id,
        "name": name,
        "owner_name": "ypatel",
        "health": {"healthy": True},
        "latest_build": {
            "status": "running",
            "resources": [
                {
                    "agents": [
                        {
                            "id": agent_id,
                            "name": "main",
                            "status": "connected",
                            "lifecycle_state": "ready",
                            "metadata": [],
                        }
                    ]
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_discovery_intersects_hosts_verifies_repo_and_ranks_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only connected Omni hosts rank, with CPU count remaining advisory."""
    monkeypatch.setenv("CODER_URL", "https://coder.example.test")
    monkeypatch.setattr(coder_dispatch, "_coder_token", lambda: "secret-token")

    probes = {
        "ypbox1": {
            "memory_used_bytes": 9 * _GIB,
            "memory_total_bytes": 30 * _GIB,
            "load_1m": 6.0,
            "logical_cpu_count": 4.0,
            "containers": 26.0,
            "workspace_path": "/workspace/omnigent",
            "repository_remote": "github.com/yp583/omnigent",
        },
        "ypbox2": {
            "memory_used_bytes": 2 * _GIB,
            "memory_total_bytes": 16 * _GIB,
            "load_1m": 1.0,
            "logical_cpu_count": 2.0,
            "containers": 4.0,
            "workspace_path": "/workspace/omnigent",
            "repository_remote": "github.com/yp583/omnigent",
        },
    }

    async def _probe(owner: str, workspace: str) -> dict[str, Any]:
        assert owner == "ypatel"
        return probes[workspace]

    monkeypatch.setattr(coder_dispatch, "_ssh_probe", _probe)

    async def _omni_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/hosts"
        return httpx.Response(
            200,
            json={
                "hosts": [
                    {
                        "host_id": "host_1",
                        "name": "ypbox1",
                        "status": "online",
                        "sandbox_provider": None,
                        "coder_workspace_id": "workspace-1",
                    },
                    {
                        "host_id": "host_2",
                        "name": "ypbox2",
                        "status": "online",
                        "sandbox_provider": None,
                        "coder_workspace_id": "workspace-2",
                    },
                ]
            },
        )

    async def _coder_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/workspaces":
            assert request.headers["Coder-Session-Token"] == "secret-token"
            assert "owner:me" in request.url.params["q"]
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        _workspace("workspace-1", "ypbox1", "agent-1"),
                        _workspace("workspace-2", "ypbox2", "agent-2"),
                        _workspace("workspace-3", "not-connected", "agent-3"),
                    ]
                },
            )
        if request.url.path.endswith("/containers"):
            return httpx.Response(200, json={"containers": []})
        return httpx.Response(404)

    original_client = httpx.AsyncClient
    async with original_client(
        transport=httpx.MockTransport(_omni_handler),
        base_url="https://omni.example.test",
    ) as server_client:

        def _coder_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(_coder_handler)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(coder_dispatch.httpx, "AsyncClient", _coder_client)
        result = await coder_dispatch.discover_coder_hosts(
            server_client=server_client,
            repository_remote="git@github.com:yp583/omnigent.git",
        )

    candidates = result["candidates"]
    assert isinstance(candidates, list)
    assert [item["workspace_name"] for item in candidates] == ["ypbox2", "ypbox1"]
    assert all(item["eligible"] is True for item in candidates)
    assert candidates[0]["normalized_load"] == 0.5
    assert candidates[1]["logical_cpu_count"] == 4.0
    assert result["needs_confirmation"] is False
    assert "never cap" in str(result["ranking_note"])
    assert result["excluded"] == [
        {
            "workspace_id": "workspace-3",
            "workspace_name": "not-connected",
            "reason": "no_online_omni_host",
        }
    ]


def test_metadata_must_be_fresh_and_error_free() -> None:
    """Stale or errored Coder dashboard values fall back instead of ranking."""
    metadata = coder_dispatch._metadata_entries(
        {
            "metadata": [
                {
                    "description": {
                        "key": "mem",
                        "display_name": "Memory",
                        "interval": 10,
                    },
                    "result": {"value": "9Gi/30Gi", "age": 91, "error": ""},
                }
            ]
        }
    )
    assert coder_dispatch._fresh_metadata_value(metadata, ("mem",), 60) is None

    metadata["mem"]["result"] = {"value": "9Gi/30Gi", "age": 20, "error": ""}
    assert coder_dispatch._fresh_metadata_value(metadata, ("mem",), 60) == "9Gi/30Gi"

    metadata["mem"]["result"] = {"value": "9Gi/30Gi", "age": 1, "error": "boom"}
    assert coder_dispatch._fresh_metadata_value(metadata, ("mem",), 60) is None


def test_git_remote_normalization_removes_credentials() -> None:
    """HTTPS and SSH origins compare without leaking URL credentials."""
    assert (
        coder_dispatch._normalize_git_remote("https://token-value@github.com/yp583/omnigent.git")
        == "github.com/yp583/omnigent"
    )
    assert (
        coder_dispatch._normalize_git_remote("git@github.com:yp583/omnigent.git")
        == "github.com/yp583/omnigent"
    )
