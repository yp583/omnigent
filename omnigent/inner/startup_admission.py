"""Cross-process admission for expensive vendor-runtime cold starts.

Runner and harness processes cannot coordinate with an in-memory semaphore.
On POSIX, this module uses a small set of advisory-lock files so unrelated
Omnigent workers owned by the same user share one startup budget. A crashed
worker releases its slot when the kernel closes the file descriptor.

This is the tactical bridge to the host-level weighted resource governor in
``docs/harness-performance-architecture.md``. It intentionally governs only
cold-start handshakes, not active turns or resident session count.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import itertools
import logging
import os
import re
import stat
import tempfile
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SAFE_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_DEFAULT_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True, slots=True)
class StartupPermit:
    """Metadata for an acquired vendor-start slot."""

    wait_seconds: float
    slot: int | None
    cross_process: bool


class StartupAdmission:
    """Bound simultaneous cold starts across Omnigent processes."""

    def __init__(
        self,
        namespace: str,
        capacity: int,
        *,
        lock_root: Path | None = None,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        if not _SAFE_NAMESPACE.fullmatch(namespace):
            raise ValueError("startup admission namespace must be lowercase ASCII")
        if capacity < 0:
            raise ValueError("startup admission capacity must be non-negative")
        if poll_interval_s <= 0:
            raise ValueError("startup admission poll interval must be positive")
        self.namespace = namespace
        self.capacity = capacity
        self.poll_interval_s = poll_interval_s
        self._lock_root = lock_root or self._default_lock_root()
        self._tickets = itertools.count()
        self._local_semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()

    @staticmethod
    def _default_lock_root() -> Path:
        user_id = str(os.getuid()) if hasattr(os, "getuid") else str(os.getpid())
        return Path(tempfile.gettempdir()) / f"omnigent-{user_id}" / "startup-admission"

    def _prepare_lock_root(self) -> None:
        self._lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self._lock_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError(f"unsafe startup admission directory: {self._lock_root}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise OSError(f"startup admission directory has a different owner: {self._lock_root}")
        # Tighten a pre-existing directory owned by this user. Lock files never
        # contain data, but another user must not be able to replace them.
        if stat.S_IMODE(info.st_mode) != 0o700:
            self._lock_root.chmod(0o700)

    def _try_lock(self, slot: int) -> int | None:
        if fcntl is None:
            return None
        path = self._lock_root / f"{self.namespace}-{slot}.lock"
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                return None
            raise
        return fd

    async def _acquire_file_slot(self) -> tuple[int, int]:
        self._prepare_lock_root()
        ticket = next(self._tickets)
        first_slot = (os.getpid() + ticket) % self.capacity
        while True:
            for offset in range(self.capacity):
                slot = (first_slot + offset) % self.capacity
                fd = self._try_lock(slot)
                if fd is not None:
                    return slot, fd
            # A tiny PID-derived offset prevents every waiting process from
            # retrying on the same scheduler tick.
            jitter = (os.getpid() % 11) / 1000
            await asyncio.sleep(self.poll_interval_s + jitter)

    def _local_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._local_semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.capacity)
            self._local_semaphores[loop] = semaphore
        return semaphore

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[StartupPermit]:
        """Wait for a cold-start slot and release it on every exit path."""
        started_at = time.monotonic()
        if self.capacity == 0:
            yield StartupPermit(wait_seconds=0.0, slot=None, cross_process=False)
            return

        if fcntl is None:  # pragma: no cover - Windows fallback
            semaphore = self._local_semaphore()
            async with semaphore:
                yield StartupPermit(
                    wait_seconds=time.monotonic() - started_at,
                    slot=None,
                    cross_process=False,
                )
            return

        try:
            slot, fd = await self._acquire_file_slot()
        except OSError as exc:
            # A broken temp/runtime directory should not make every harness
            # unusable. Retain process-local protection and make the degraded
            # coordination visible.
            logger.warning(
                "Cross-process startup admission unavailable for %s: %s; "
                "falling back to process-local admission",
                self.namespace,
                exc,
            )
            semaphore = self._local_semaphore()
            async with semaphore:
                yield StartupPermit(
                    wait_seconds=time.monotonic() - started_at,
                    slot=None,
                    cross_process=False,
                )
            return

        try:
            yield StartupPermit(
                wait_seconds=time.monotonic() - started_at,
                slot=slot,
                cross_process=True,
            )
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def resolve_startup_capacity(env_name: str, default: int) -> int:
    """Read a non-negative startup capacity, warning on invalid overrides."""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        logger.warning("Ignoring invalid %s=%r; using %d", env_name, raw, default)
        return default
    return value


def scoped_admission_namespace(namespace: str, scope: str) -> str:
    """Return a safe, non-identifying lock namespace for one resource root.

    Runner ids and session ids can contain characters that are unsuitable for
    lock filenames. Hashing also keeps those identifiers out of the shared temp
    directory while ensuring every descendant of a root resolves the same
    admission lane.
    """
    if not _SAFE_NAMESPACE.fullmatch(namespace):
        raise ValueError("startup admission namespace must be lowercase ASCII")
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    prefix = namespace[: 63 - len(digest) - 1]
    return f"{prefix}-{digest}"
