"""Tests for cross-process vendor startup admission."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omnigent.inner import startup_admission
from omnigent.inner.startup_admission import (
    StartupAdmission,
    resolve_startup_capacity,
    scoped_admission_namespace,
)


@pytest.mark.asyncio
async def test_capacity_one_queues_second_cold_start(tmp_path: Path) -> None:
    """Separate limiter instances coordinate through the same lock file."""
    if startup_admission.fcntl is None:
        pytest.skip("cross-process lock path is POSIX-only")
    first = StartupAdmission("claude-test", 1, lock_root=tmp_path, poll_interval_s=0.005)
    second = StartupAdmission("claude-test", 1, lock_root=tmp_path, poll_interval_s=0.005)
    second_acquired = asyncio.Event()

    async def _wait_for_second() -> float:
        async with second.acquire() as permit:
            second_acquired.set()
            return permit.wait_seconds

    async with first.acquire() as first_permit:
        assert first_permit.cross_process
        waiter = asyncio.create_task(_wait_for_second())
        await asyncio.sleep(0.03)
        assert not second_acquired.is_set()

    wait_seconds = await asyncio.wait_for(waiter, timeout=1)
    assert second_acquired.is_set()
    assert wait_seconds >= 0.02


@pytest.mark.asyncio
async def test_capacity_two_allows_two_simultaneous_starts(tmp_path: Path) -> None:
    if startup_admission.fcntl is None:
        pytest.skip("cross-process lock path is POSIX-only")
    first = StartupAdmission("claude-test", 2, lock_root=tmp_path)
    second = StartupAdmission("claude-test", 2, lock_root=tmp_path)
    async with first.acquire() as permit_a, second.acquire() as permit_b:
        assert permit_a.slot != permit_b.slot
        assert permit_a.cross_process and permit_b.cross_process


@pytest.mark.asyncio
async def test_zero_capacity_disables_admission(tmp_path: Path) -> None:
    admission = StartupAdmission("claude-test", 0, lock_root=tmp_path)
    async with admission.acquire() as permit:
        assert permit.slot is None
        assert not permit.cross_process
        assert permit.wait_seconds == 0


def test_invalid_namespace_and_capacity_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="namespace"):
        StartupAdmission("NOT SAFE", 1, lock_root=tmp_path)
    with pytest.raises(ValueError, match="non-negative"):
        StartupAdmission("safe", -1, lock_root=tmp_path)


def test_capacity_env_override(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setenv("OMNIGENT_TEST_STARTS", "7")
    assert resolve_startup_capacity("OMNIGENT_TEST_STARTS", 4) == 7
    monkeypatch.setenv("OMNIGENT_TEST_STARTS", "many")
    assert resolve_startup_capacity("OMNIGENT_TEST_STARTS", 4) == 4
    assert "Ignoring invalid" in caplog.text


def test_scoped_namespace_is_stable_safe_and_non_identifying() -> None:
    first = scoped_admission_namespace("claude-root", "runner/secret root")
    second = scoped_admission_namespace("claude-root", "runner/secret root")
    other = scoped_admission_namespace("claude-root", "runner/other")

    assert first == second
    assert first != other
    assert "secret" not in first
    assert len(first) <= 63
