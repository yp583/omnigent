"""Shared cold-start admission for managed and native Codex app servers."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from omnigent.runner.identity import RUNNER_ID_ENV_VAR

from .startup_admission import (
    StartupAdmission,
    resolve_startup_capacity,
    scoped_admission_namespace,
)

_STARTUP_CONCURRENCY_ENV = "OMNIGENT_CODEX_STARTUP_CONCURRENCY"
_DEFAULT_STARTUP_CONCURRENCY = 4
_STARTUP_ADMISSION = StartupAdmission(
    "codex-app-server",
    resolve_startup_capacity(_STARTUP_CONCURRENCY_ENV, _DEFAULT_STARTUP_CONCURRENCY),
)
_ROOT_ADMISSIONS: dict[str, StartupAdmission] = {}


def _root_admission() -> StartupAdmission | None:
    root = os.environ.get(RUNNER_ID_ENV_VAR, "").strip()
    if not root or _STARTUP_ADMISSION.capacity == 0:
        return None
    admission = _ROOT_ADMISSIONS.get(root)
    if admission is None:
        admission = StartupAdmission(scoped_admission_namespace("codex-root", root), 1)
        _ROOT_ADMISSIONS[root] = admission
    return admission


@asynccontextmanager
async def admit_codex_startup() -> AsyncIterator[float]:
    """Admit one Codex app-server handshake and report queue duration."""
    root_admission = _root_admission()
    if root_admission is None:
        async with _STARTUP_ADMISSION.acquire() as permit:
            yield permit.wait_seconds
        return
    async with (
        root_admission.acquire() as root_permit,
        _STARTUP_ADMISSION.acquire() as permit,
    ):
        yield root_permit.wait_seconds + permit.wait_seconds


__all__ = ["admit_codex_startup"]
