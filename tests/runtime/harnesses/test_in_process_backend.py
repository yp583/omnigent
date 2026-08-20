"""Tests for opt-in official in-process harness adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import StreamingResponse

from omnigent.codex_native_bridge import (
    CODEX_NATIVE_BRIDGE_DIR_ENV_VAR,
    CODEX_NATIVE_REQUEST_SESSION_ID_ENV_VAR,
    CodexNativeBridgeState,
    write_bridge_state,
)
from omnigent.inner.executor import MockExecutor
from omnigent.runtime.harnesses._streaming_asgi_transport import StreamingASGITransport
from omnigent.runtime.harnesses.in_process_backend import (
    IN_PROCESS_HARNESSES,
    IN_PROCESS_NATIVE_HARNESSES,
)
from omnigent.runtime.harnesses.process_manager import (
    _IN_PROCESS_HARNESSES_ENV,
    _IN_PROCESS_NATIVE_HARNESSES_ENV,
    HarnessProcessManager,
    _resolve_in_process_harnesses,
    _resolve_in_process_native_harnesses,
)


class _FakeCodexClient:
    requests: list[tuple[str, dict[str, object]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def request(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        type(self).requests.append((method, params))
        return {"result": {"turn": {"id": "turn_1"}}}


def test_in_process_native_backend_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_IN_PROCESS_HARNESSES_ENV, raising=False)
    monkeypatch.delenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, raising=False)
    assert _resolve_in_process_native_harnesses() == frozenset()


def test_in_process_native_backend_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_IN_PROCESS_HARNESSES_ENV, raising=False)
    monkeypatch.setenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, "all")
    assert _resolve_in_process_native_harnesses() == IN_PROCESS_NATIVE_HARNESSES

    monkeypatch.setenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, "codex-native,unknown")
    assert _resolve_in_process_native_harnesses() == frozenset({"codex-native"})


def test_in_process_managed_backend_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, raising=False)
    monkeypatch.setenv(_IN_PROCESS_HARNESSES_ENV, "all")
    assert _resolve_in_process_harnesses() == IN_PROCESS_HARNESSES

    monkeypatch.setenv(_IN_PROCESS_HARNESSES_ENV, "codex,claude-sdk")
    assert _resolve_in_process_harnesses() == frozenset({"codex"})


async def test_streaming_asgi_transport_interleaves_control_requests() -> None:
    """An SSE response must not buffer and block steering/tool-result POSTs."""
    app = FastAPI()
    release = asyncio.Event()

    async def body():  # type: ignore[no-untyped-def]
        yield b"first\n"
        await release.wait()
        yield b"second\n"

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(body())

    @app.post("/release")
    async def release_stream() -> dict[str, bool]:
        release.set()
        return {"ok": True}

    transport = StreamingASGITransport(app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://harness.local",
    ) as client:
        async with client.stream("GET", "/stream") as response:
            chunks = response.aiter_bytes()
            assert await asyncio.wait_for(chunks.__anext__(), timeout=1.0) == b"first\n"
            control = await asyncio.wait_for(client.post("/release"), timeout=1.0)
            assert control.json() == {"ok": True}
            assert await asyncio.wait_for(chunks.__anext__(), timeout=1.0) == b"second\n"


async def test_codex_native_runs_in_process_without_worker_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, "codex-native")
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    write_bridge_state(
        bridge_dir,
        CodexNativeBridgeState(
            session_id="conv_codex",
            socket_path=str(tmp_path / "app-server.sock"),
            thread_id="thread_1",
            codex_home=str(tmp_path / "codex-home"),
        ),
    )
    _FakeCodexClient.requests = []
    monkeypatch.setattr(
        "omnigent.codex_native_app_server.CodexAppServerClient",
        _FakeCodexClient,
    )
    manager = HarnessProcessManager(tmp_parent=tmp_path / "harnesses")

    async def unexpected_spawn(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("codex-native must not spawn a Python harness worker")

    monkeypatch.setattr(manager, "_spawn_harness_process", unexpected_spawn)
    await manager.start()
    try:
        client = await manager.get_client(
            "conv_codex",
            "codex-native",
            env={
                CODEX_NATIVE_BRIDGE_DIR_ENV_VAR: str(bridge_dir),
                CODEX_NATIVE_REQUEST_SESSION_ID_ENV_VAR: "conv_codex",
            },
        )
        health = await client.get("/health")
        assert health.json() == {"status": "ok"}
        body = {
            "type": "message",
            "role": "user",
            "model": "codex-native-ui",
            "content": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
        }
        async with client.stream(
            "POST",
            "/v1/sessions/conv_codex/events",
            json=body,
        ) as response:
            transcript = "".join([chunk async for chunk in response.aiter_text()])
        assert "response.completed" in transcript
        assert _FakeCodexClient.requests == [
            (
                "turn/start",
                {
                    "threadId": "thread_1",
                    "input": [{"type": "text", "text": "hello"}],
                },
            )
        ]
        snapshot = manager.resource_diagnostics()
        assert snapshot["resident_count"] == 1
        assert snapshot["entries"] == [
            {
                "conversation_id": "conv_codex",
                "root_session_id": "conv_codex",
                "is_child": False,
                "harness": "codex-native",
                "pid": None,
                "backend": "in_process",
                "in_flight": False,
                "idle_seconds": pytest.approx(0.0, abs=1.0),
                "startup_queue_seconds": 0.0,
                "idle_timeout_seconds": 3600,
            }
        ]
        await manager.release("conv_codex")
        assert manager.resource_diagnostics()["resident_count"] == 0
    finally:
        await manager.shutdown()


async def test_managed_codex_runs_in_process_without_worker_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The active managed path removes only Omnigent's wrapper process."""
    monkeypatch.setenv(_IN_PROCESS_HARNESSES_ENV, "codex")
    monkeypatch.delenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, raising=False)
    executor = MockExecutor()
    executor.enqueue_response("managed codex response")
    monkeypatch.setattr(
        "omnigent.inner.codex_harness._build_codex_executor",
        lambda _env: executor,
    )
    manager = HarnessProcessManager(tmp_parent=tmp_path / "harnesses")

    async def unexpected_spawn(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("managed codex must not spawn a Python harness worker")

    monkeypatch.setattr(manager, "_spawn_harness_process", unexpected_spawn)
    await manager.start()
    try:
        client = await manager.get_client(
            "conv_managed_codex",
            "codex",
            env={
                "HOME": str(tmp_path / "home"),
                "PATH": "/session/bin",
                "HARNESS_CODEX_MODEL": "session-model",
            },
        )
        body = {
            "type": "message",
            "role": "user",
            "model": "codex",
            "content": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
        }
        async with client.stream(
            "POST",
            "/v1/sessions/conv_managed_codex/events",
            json=body,
        ) as response:
            transcript = "".join([chunk async for chunk in response.aiter_text()])
        assert "response.completed" in transcript
        entry = manager._entries["conv_managed_codex"]
        assert entry.process is None
        assert entry.backend == "in_process"
    finally:
        await manager.shutdown()


async def test_managed_codex_fifteen_session_adapter_smoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fifteen active adapters retain distinct config without workers."""
    monkeypatch.setenv(_IN_PROCESS_HARNESSES_ENV, "codex")
    monkeypatch.delenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, raising=False)
    observed_models: set[str] = set()

    def build_executor(env: dict[str, str]) -> MockExecutor:
        observed_models.add(env["HARNESS_CODEX_MODEL"])
        executor = MockExecutor()
        executor.enqueue_response("ok")
        return executor

    monkeypatch.setattr(
        "omnigent.inner.codex_harness._build_codex_executor",
        build_executor,
    )
    manager = HarnessProcessManager(tmp_parent=tmp_path / "harnesses")

    async def unexpected_spawn(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("optimized active Codex must not spawn wrapper workers")

    monkeypatch.setattr(manager, "_spawn_harness_process", unexpected_spawn)
    await manager.start()
    session_ids = [f"conv_{index}" for index in range(15)]
    try:
        clients = await asyncio.gather(
            *(
                manager.get_client(
                    session_id,
                    "codex",
                    env={"HARNESS_CODEX_MODEL": f"model_{index}"},
                )
                for index, session_id in enumerate(session_ids)
            )
        )

        async def run_turn(client: httpx.AsyncClient, session_id: str) -> None:
            body = {
                "type": "message",
                "role": "user",
                "model": "codex",
                "content": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    }
                ],
            }
            async with client.stream(
                "POST",
                f"/v1/sessions/{session_id}/events",
                json=body,
            ) as response:
                transcript = "".join([chunk async for chunk in response.aiter_text()])
            assert "response.completed" in transcript

        await asyncio.gather(
            *(
                run_turn(client, session_id)
                for client, session_id in zip(clients, session_ids, strict=True)
            )
        )
        assert observed_models == {f"model_{index}" for index in range(15)}
        assert manager.resource_diagnostics()["resident_count"] == 15
        assert all(entry.process is None for entry in manager._entries.values())
    finally:
        await manager.shutdown()


async def test_in_process_initialization_failure_falls_back_to_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(_IN_PROCESS_NATIVE_HARNESSES_ENV, "codex-native")
    manager = HarnessProcessManager(tmp_parent=tmp_path / "harnesses")

    class _FallbackProcess:
        returncode = 0
        pid = 12345

    fallback = _FallbackProcess()

    async def fail_in_process(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("unsupported optimized backend")

    async def fake_worker(*_args: object, **_kwargs: object) -> object:
        return fallback

    monkeypatch.setattr(manager, "_spawn_in_process_entry", fail_in_process)
    monkeypatch.setattr(manager, "_spawn_harness_process", fake_worker)
    monkeypatch.setattr(
        "omnigent.runtime.harnesses.process_manager._wait_for_bind",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        "omnigent.runtime.harnesses.process_manager._HarnessEndpoint.make_transport",
        lambda _self: httpx.MockTransport(lambda _request: httpx.Response(200)),
    )

    await manager.start()
    try:
        client = await manager.get_client(
            "conv_fallback",
            "codex-native",
            env={CODEX_NATIVE_BRIDGE_DIR_ENV_VAR: str(tmp_path / "bridge")},
        )
        assert client is not None
        entry = manager._entries["conv_fallback"]
        assert entry.process is fallback
        assert entry.backend == "subprocess"
    finally:
        await manager.shutdown()
