"""Safe in-process backends for official harness adapters."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from omnigent.claude_native_bridge import BRIDGE_DIR_ENV_VAR, REQUEST_SESSION_ID_ENV_VAR
from omnigent.codex_native_bridge import (
    CODEX_NATIVE_BRIDGE_DIR_ENV_VAR,
    CODEX_NATIVE_REQUEST_SESSION_ID_ENV_VAR,
)
from omnigent.inner.claude_native_executor import ClaudeNativeExecutor
from omnigent.inner.codex_harness import create_app as create_codex_harness_app
from omnigent.inner.codex_native_executor import CodexNativeExecutor
from omnigent.inner.executor import Executor
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter

IN_PROCESS_NATIVE_HARNESSES = frozenset({"claude-native", "codex-native"})
IN_PROCESS_HARNESSES = IN_PROCESS_NATIVE_HARNESSES | {"codex"}


@dataclass
class InProcessHarnessRuntime:
    """One harness ASGI app and its explicitly-managed lifespan."""

    app: FastAPI
    _lifespan: AbstractAsyncContextManager[Any] | None = None

    async def start(self) -> None:
        if self._lifespan is not None:
            return
        lifespan = self.app.router.lifespan_context(self.app)
        await lifespan.__aenter__()
        self._lifespan = lifespan

    async def close(self) -> None:
        lifespan = self._lifespan
        if lifespan is None:
            return
        self._lifespan = None
        await lifespan.__aexit__(None, None, None)


def _required_bridge_dir(env: dict[str, str], key: str, harness: str) -> Path:
    raw = env.get(key, "").strip()
    if not raw:
        raise RuntimeError(f"{key} is required for in-process {harness}")
    return Path(raw)


def build_in_process_runtime(
    harness: str,
    conversation_id: str,
    env: dict[str, str],
) -> InProcessHarnessRuntime:
    """Build an allowlisted adapter without mutating process environment."""
    if harness == "codex":
        app = create_codex_harness_app(
            env=dict(env),
            session_key=conversation_id,
        )
    else:
        executor_factory: Callable[[], Executor]
        harness_label: str
        if harness == "codex-native":
            bridge_dir = _required_bridge_dir(
                env,
                CODEX_NATIVE_BRIDGE_DIR_ENV_VAR,
                harness,
            )
            request_session_id = (
                env.get(CODEX_NATIVE_REQUEST_SESSION_ID_ENV_VAR, "").strip() or conversation_id
            )
            executor_factory = partial(
                CodexNativeExecutor,
                bridge_dir,
                request_session_id=request_session_id,
            )
            harness_label = "Codex"
        elif harness == "claude-native":
            bridge_dir = _required_bridge_dir(env, BRIDGE_DIR_ENV_VAR, harness)
            request_session_id = env.get(REQUEST_SESSION_ID_ENV_VAR, "").strip() or conversation_id
            executor_factory = partial(
                ClaudeNativeExecutor,
                bridge_dir,
                request_session_id=request_session_id,
            )
            harness_label = "Claude"
        else:
            raise ValueError(f"harness {harness!r} has no safe in-process backend")
        adapter = ExecutorAdapter(
            executor_factory=executor_factory,
            session_key=conversation_id,
            harness_label=harness_label,
        )
        app = adapter.build()
    # Mirror the metadata the subprocess runner binds after create_app().
    # Route handlers use it as the authoritative session boundary.
    app.state.conversation_id = conversation_id
    app.state.harness = harness
    app.state.harness_auth_token = None
    return InProcessHarnessRuntime(app=app)


def build_in_process_native_runtime(
    harness: str,
    conversation_id: str,
    env: dict[str, str],
) -> InProcessHarnessRuntime:
    """Compatibility alias for callers limited to native adapters."""
    if harness not in IN_PROCESS_NATIVE_HARNESSES:
        raise ValueError(f"harness {harness!r} is not a native in-process adapter")
    return build_in_process_runtime(harness, conversation_id, env)


__all__ = [
    "IN_PROCESS_HARNESSES",
    "IN_PROCESS_NATIVE_HARNESSES",
    "InProcessHarnessRuntime",
    "build_in_process_native_runtime",
    "build_in_process_runtime",
]
