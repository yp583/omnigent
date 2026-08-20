"""Tests for shared Codex app-server cold-start admission."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from omnigent.inner import codex_startup_admission as admission
from omnigent.inner.startup_admission import StartupPermit


class _FakeAdmission:
    def __init__(self, wait_seconds: float) -> None:
        self.wait_seconds = wait_seconds
        self.entered = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[StartupPermit]:
        self.entered += 1
        yield StartupPermit(
            wait_seconds=self.wait_seconds,
            slot=0,
            cross_process=True,
        )


async def test_codex_startup_admission_uses_machine_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _FakeAdmission(1.25)
    monkeypatch.setattr(admission, "_STARTUP_ADMISSION", machine)
    monkeypatch.setattr(admission, "_root_admission", lambda: None)

    async with admission.admit_codex_startup() as queued_seconds:
        assert queued_seconds == 1.25

    assert machine.entered == 1


async def test_codex_startup_admission_combines_root_and_machine_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _FakeAdmission(0.5)
    machine = _FakeAdmission(1.25)
    monkeypatch.setattr(admission, "_STARTUP_ADMISSION", machine)
    monkeypatch.setattr(admission, "_root_admission", lambda: root)

    async with admission.admit_codex_startup() as queued_seconds:
        assert queued_seconds == 1.75

    assert root.entered == 1
    assert machine.entered == 1
