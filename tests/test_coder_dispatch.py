"""Tests for Coder-backed Omni host discovery and ranking."""

from __future__ import annotations

from pathlib import Path
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
            "repositories": [
                {
                    "workspace_path": "/workspace/omnigent",
                    "repository_remote": "github.com/yp583/omnigent",
                }
            ],
        },
        "ypbox2": {
            "memory_used_bytes": 2 * _GIB,
            "memory_total_bytes": 16 * _GIB,
            "load_1m": 1.0,
            "logical_cpu_count": 2.0,
            "containers": 4.0,
            "repositories": [
                {
                    "workspace_path": "/workspace/omnigent",
                    "repository_remote": "github.com/yp583/omnigent",
                }
            ],
        },
    }

    async def _probe(owner: str, workspace: str) -> dict[str, Any]:
        assert owner == "ypatel"
        return probes[workspace]

    monkeypatch.setattr(coder_dispatch, "_ssh_probe", _probe)
    workspace_queries: list[str] = []

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
            workspace_queries.append(request.url.params["q"])
            if "include_agent_metadata" in request.url.params["q"]:
                return httpx.Response(
                    400,
                    json={
                        "message": "Invalid workspace search query.",
                        "validations": [
                            {
                                "field": "include_agent_metadata",
                                "detail": "unsupported",
                            }
                        ],
                    },
                )
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
    assert candidates[0]["identity_match"] == "coder_workspace_id"
    assert candidates[1]["identity_match"] == "unique_workspace_name"
    assert result["needs_confirmation"] is False
    assert "never cap" in str(result["ranking_note"])
    assert len(workspace_queries) == 2
    assert "include_agent_metadata" in workspace_queries[0]
    assert "include_agent_metadata" not in workspace_queries[1]
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


def test_ssh_probe_output_parses_memory_and_strips_repository_credentials() -> None:
    """The stdin probe shape yields capacity and credential-free checkouts."""
    result = coder_dispatch._parse_ssh_probe_output(
        b"stats\t22660272\t8060296\t4096\t0.56\t8\t26\n"
        b"repo\t/home/ubuntu/silico\t"
        b"https://secret-token@github.com/Altrix-Technologies/silico-prod.git\n"
    )

    assert result is not None
    assert result["memory_total_bytes"] == 8060296 * 4096
    assert result["memory_used_bytes"] == 8060296 * 4096 - 22660272 * 1024
    assert result["load_1m"] == 0.56
    assert result["logical_cpu_count"] == 8.0
    assert result["containers"] == 26.0
    assert result["repositories"] == [
        {
            "workspace_path": "/home/ubuntu/silico",
            "repository_remote": "github.com/Altrix-Technologies/silico-prod",
        }
    ]
    assert "secret-token" not in str(result)
    assert (
        coder_dispatch._normalize_git_remote("git@github.com:yp583/omnigent.git")
        == "github.com/yp583/omnigent"
    )


def test_coder_url_falls_back_to_cli_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Discovery works from an ordinary logged-in CLI without CODER_URL."""
    monkeypatch.delenv("CODER_URL", raising=False)
    monkeypatch.setenv("CODER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "url").write_text("https://coder.example.test/\n", encoding="utf-8")

    assert coder_dispatch._coder_url() == "https://coder.example.test"


def test_legacy_name_match_rejects_ambiguous_online_hosts() -> None:
    """Old-server name fallback never guesses between duplicate host names."""
    hosts = [
        {
            "host_id": "host_1",
            "name": "ypbox1",
            "status": "online",
            "sandbox_provider": None,
        },
        {
            "host_id": "host_2",
            "name": "YPBOX1",
            "status": "online",
            "sandbox_provider": None,
        },
    ]
    by_workspace, by_host_id, by_name = coder_dispatch._available_omni_hosts(hosts)

    assert (
        coder_dispatch._match_omni_host(
            workspace_id="workspace-1",
            workspace_name="ypbox1",
            by_coder_workspace_id=by_workspace,
            by_host_id=by_host_id,
            by_legacy_name=by_name,
        )
        is None
    )
