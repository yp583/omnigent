"""Linux procfs process-tree resource sampling.

The benchmark uses PSS as its primary multi-process memory measure. RSS counts
shared pages once per mapping and therefore makes a fork-heavy process tree look
more expensive than it is. ``smaps_rollup`` exposes PSS and private clean/dirty
bytes without walking every individual mapping.

The sampler is deliberately independent of psutil and accepts an alternate
``proc_root``. Unit tests can model PID trees and permission failures without
spawning real subprocesses; production benchmark runs read ``/proc``.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

_KIB = 1024


@dataclass(frozen=True, slots=True)
class ProcessRoot:
    """A benchmark-owned process root and its attribution label.

    ``include_process_group`` captures detached descendants which retain the
    root's process group but have been reparented. Ordinary descendants are
    always included. When roots are nested, a process is attributed to its
    nearest explicit root.
    """

    pid: int
    role: str
    session_id: str | None = None
    include_process_group: bool = True


@dataclass(frozen=True, slots=True)
class ResourceMetrics:
    """Additive resource values for one process or an aggregate.

    A nullable byte/FD field means at least one contributing process could not
    expose that metric. The sampler never turns missing PSS into zero.
    """

    pss_bytes: int | None = None
    rss_bytes: int | None = None
    uss_bytes: int | None = None
    pss_anon_bytes: int | None = None
    pss_file_bytes: int | None = None
    pss_shmem_bytes: int | None = None
    swap_bytes: int | None = None
    cpu_seconds: float = 0.0
    process_count: int = 1
    thread_count: int = 0
    fd_count: int | None = None

    def to_dict(self) -> dict[str, int | float | None]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """One process observed in a tree snapshot."""

    pid: int
    ppid: int
    pgid: int
    start_time_ticks: int
    cpu_ticks: int
    command: str
    role: str
    root_pid: int
    session_id: str | None
    metrics: ResourceMetrics

    @property
    def identity(self) -> tuple[int, int]:
        """Stable-enough identity that rejects PID reuse between samples."""
        return self.pid, self.start_time_ticks

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        payload = asdict(self)
        payload["identity"] = [self.pid, self.start_time_ticks]
        return payload


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    """A point-in-time observation of all declared process roots."""

    captured_at: str
    monotonic_ns: int
    clock_ticks_per_second: int
    roots: tuple[ProcessRoot, ...]
    observations: tuple[ProcessObservation, ...]
    total: ResourceMetrics
    by_role: Mapping[str, ResourceMetrics]
    by_session: Mapping[str, ResourceMetrics]
    missing_root_pids: tuple[int, ...] = ()
    unreadable_pids: tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every root and selected process was readable."""
        return not self.missing_root_pids and not self.unreadable_pids

    def to_dict(self) -> dict[str, object]:
        """Return the versioned raw snapshot shape used in JSON reports."""
        return {
            "captured_at": self.captured_at,
            "monotonic_ns": self.monotonic_ns,
            "clock_ticks_per_second": self.clock_ticks_per_second,
            "complete": self.complete,
            "roots": [asdict(root) for root in self.roots],
            "missing_root_pids": list(self.missing_root_pids),
            "unreadable_pids": list(self.unreadable_pids),
            "total": self.total.to_dict(),
            "by_role": {key: value.to_dict() for key, value in self.by_role.items()},
            "by_session": {key: value.to_dict() for key, value in self.by_session.items()},
            "processes": [process.to_dict() for process in self.observations],
        }


@dataclass(frozen=True, slots=True)
class _Stat:
    pid: int
    command: str
    ppid: int
    pgid: int
    cpu_ticks: int
    start_time_ticks: int


def _parse_stat(text: str) -> _Stat:
    """Parse ``/proc/PID/stat`` while tolerating spaces and ``)`` in comm."""
    open_paren = text.find("(")
    close_paren = text.rfind(")")
    if open_paren <= 0 or close_paren <= open_paren:
        raise ValueError("malformed proc stat")
    pid = int(text[:open_paren].strip())
    command = text[open_paren + 1 : close_paren]
    fields = text[close_paren + 1 :].strip().split()
    # fields[0] is field 3 (state); starttime is field 22.
    if len(fields) < 20:
        raise ValueError("short proc stat")
    return _Stat(
        pid=pid,
        command=command,
        ppid=int(fields[1]),
        pgid=int(fields[2]),
        cpu_ticks=int(fields[11]) + int(fields[12]),
        start_time_ticks=int(fields[19]),
    )


def _parse_kib_file(text: str) -> dict[str, int]:
    """Parse procfs ``Name: N kB`` rows into byte values."""
    parsed: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        parts = raw_value.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        parsed[key] = value * _KIB if len(parts) > 1 and parts[1] == "kB" else value
    return parsed


def _read_text(path: Path) -> str:
    """Read a procfs text file without locale-dependent decoding failures."""
    return path.read_text(encoding="utf-8", errors="replace")


def _nullable_sum(values: Iterable[int | None]) -> int | None:
    """Sum values, returning ``None`` if any contributor is unavailable."""
    materialized = tuple(values)
    if any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def _aggregate(observations: Sequence[ProcessObservation]) -> ResourceMetrics:
    """Aggregate additive metrics without hiding missing data."""
    metrics = [observation.metrics for observation in observations]
    if not metrics:
        return ResourceMetrics(process_count=0, fd_count=0)
    return ResourceMetrics(
        pss_bytes=_nullable_sum(metric.pss_bytes for metric in metrics),
        rss_bytes=_nullable_sum(metric.rss_bytes for metric in metrics),
        uss_bytes=_nullable_sum(metric.uss_bytes for metric in metrics),
        pss_anon_bytes=_nullable_sum(metric.pss_anon_bytes for metric in metrics),
        pss_file_bytes=_nullable_sum(metric.pss_file_bytes for metric in metrics),
        pss_shmem_bytes=_nullable_sum(metric.pss_shmem_bytes for metric in metrics),
        swap_bytes=_nullable_sum(metric.swap_bytes for metric in metrics),
        cpu_seconds=sum(metric.cpu_seconds for metric in metrics),
        process_count=len(metrics),
        thread_count=sum(metric.thread_count for metric in metrics),
        fd_count=_nullable_sum(metric.fd_count for metric in metrics),
    )


class ProcfsSampler:
    """Sample complete Linux process trees from procfs."""

    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        *,
        clock_ticks_per_second: int | None = None,
    ) -> None:
        self._proc_root = proc_root
        self._clock_ticks = clock_ticks_per_second or int(os.sysconf("SC_CLK_TCK"))

    def _all_stats(self) -> dict[int, _Stat]:
        stats: dict[int, _Stat] = {}
        try:
            entries = self._proc_root.iterdir()
        except OSError as exc:
            raise RuntimeError(f"cannot enumerate procfs at {self._proc_root}: {exc}") from exc
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat = _parse_stat(_read_text(entry / "stat"))
            except (OSError, ValueError):
                # Processes can disappear during the scan. A selected root is
                # reported missing below; unrelated races are irrelevant.
                continue
            stats[stat.pid] = stat
        return stats

    @staticmethod
    def _attribution(
        stats: Mapping[int, _Stat], roots: Sequence[ProcessRoot]
    ) -> dict[int, ProcessRoot]:
        children: dict[int, list[int]] = defaultdict(list)
        for stat in stats.values():
            children[stat.ppid].append(stat.pid)

        # A nested explicit root wins over an ancestor because its breadth-first
        # walk happens at distance zero. Store distance and declaration order to
        # make equal-distance attribution deterministic.
        candidates: dict[int, tuple[int, int, ProcessRoot]] = {}
        for order, root in enumerate(roots):
            if root.pid not in stats:
                continue
            queue: deque[tuple[int, int]] = deque([(root.pid, 0)])
            visited: set[int] = set()
            while queue:
                pid, distance = queue.popleft()
                if pid in visited:
                    continue
                visited.add(pid)
                candidate = (distance, order, root)
                current = candidates.get(pid)
                if current is None or candidate[:2] < current[:2]:
                    candidates[pid] = candidate
                queue.extend((child, distance + 1) for child in children.get(pid, ()))

            if root.include_process_group:
                root_pgid = stats[root.pid].pgid
                for stat in stats.values():
                    if stat.pgid != root_pgid:
                        continue
                    # Process-group membership is weaker than ancestry.
                    candidate = (1_000_000, order, root)
                    current = candidates.get(stat.pid)
                    if current is None or candidate[:2] < current[:2]:
                        candidates[stat.pid] = candidate

        return {pid: candidate[2] for pid, candidate in candidates.items()}

    def _observe(self, stat: _Stat, root: ProcessRoot) -> ProcessObservation:
        proc_dir = self._proc_root / str(stat.pid)
        status = _parse_kib_file(_read_text(proc_dir / "status"))
        try:
            smaps = _parse_kib_file(_read_text(proc_dir / "smaps_rollup"))
        except OSError:
            smaps = {}

        try:
            command_bytes = (proc_dir / "cmdline").read_bytes()
            command = command_bytes.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            command = ""
        if not command:
            command = stat.command

        try:
            fd_count: int | None = sum(1 for _ in (proc_dir / "fd").iterdir())
        except OSError:
            fd_count = None

        private_clean = smaps.get("Private_Clean")
        private_dirty = smaps.get("Private_Dirty")
        uss = (
            private_clean + private_dirty
            if private_clean is not None and private_dirty is not None
            else None
        )
        metrics = ResourceMetrics(
            pss_bytes=smaps.get("Pss"),
            rss_bytes=smaps.get("Rss", status.get("VmRSS")),
            uss_bytes=uss,
            pss_anon_bytes=smaps.get("Pss_Anon"),
            pss_file_bytes=smaps.get("Pss_File"),
            pss_shmem_bytes=smaps.get("Pss_Shmem"),
            swap_bytes=smaps.get("Swap", status.get("VmSwap")),
            cpu_seconds=stat.cpu_ticks / self._clock_ticks,
            thread_count=status.get("Threads", 0),
            fd_count=fd_count,
        )
        return ProcessObservation(
            pid=stat.pid,
            ppid=stat.ppid,
            pgid=stat.pgid,
            start_time_ticks=stat.start_time_ticks,
            cpu_ticks=stat.cpu_ticks,
            command=command,
            role=root.role,
            root_pid=root.pid,
            session_id=root.session_id,
            metrics=metrics,
        )

    def snapshot(self, roots: Sequence[ProcessRoot]) -> TreeSnapshot:
        """Capture and attribute every process beneath ``roots``."""
        if not roots:
            raise ValueError("at least one process root is required")
        if len({root.pid for root in roots}) != len(roots):
            raise ValueError("process root PIDs must be unique")
        if any(root.pid <= 0 for root in roots):
            raise ValueError("process root PIDs must be positive")

        monotonic_ns = time.monotonic_ns()
        captured_at = dt.datetime.now(dt.timezone.utc).isoformat()
        stats = self._all_stats()
        attribution = self._attribution(stats, roots)
        observations: list[ProcessObservation] = []
        unreadable: list[int] = []
        for pid in sorted(attribution):
            try:
                observations.append(self._observe(stats[pid], attribution[pid]))
            except OSError:
                unreadable.append(pid)

        by_role_members: dict[str, list[ProcessObservation]] = defaultdict(list)
        by_session_members: dict[str, list[ProcessObservation]] = defaultdict(list)
        for observation in observations:
            by_role_members[observation.role].append(observation)
            if observation.session_id is not None:
                by_session_members[observation.session_id].append(observation)

        return TreeSnapshot(
            captured_at=captured_at,
            monotonic_ns=monotonic_ns,
            clock_ticks_per_second=self._clock_ticks,
            roots=tuple(roots),
            observations=tuple(observations),
            total=_aggregate(observations),
            by_role={key: _aggregate(value) for key, value in by_role_members.items()},
            by_session={key: _aggregate(value) for key, value in by_session_members.items()},
            missing_root_pids=tuple(sorted(root.pid for root in roots if root.pid not in stats)),
            unreadable_pids=tuple(unreadable),
        )


def cpu_percent_between(previous: TreeSnapshot, current: TreeSnapshot) -> float | None:
    """Return whole-tree CPU percentage between two snapshots.

    The result is normalized to one core, matching common process CPU tools: a
    tree saturating two cores reports roughly 200%. A process created during the
    interval contributes its cumulative ticks; a pre-existing process first
    discovered late does not. PID reuse is rejected through ``(pid,
    start_time_ticks)`` identities.
    """
    elapsed = (current.monotonic_ns - previous.monotonic_ns) / 1_000_000_000
    if elapsed <= 0:
        return None
    previous_ticks = {item.identity: item.cpu_ticks for item in previous.observations}
    previous_monotonic_s = previous.monotonic_ns / 1_000_000_000
    delta_ticks = 0
    for item in current.observations:
        if item.identity in previous_ticks:
            delta_ticks += max(0, item.cpu_ticks - previous_ticks[item.identity])
            continue
        # /proc starttime and CLOCK_MONOTONIC share the Linux boot-relative
        # clock domain. One second of tolerance covers sampling/rounding at the
        # boundary without charging an old process's lifetime CPU.
        started_s = item.start_time_ticks / current.clock_ticks_per_second
        if started_s >= previous_monotonic_s - 1.0:
            delta_ticks += item.cpu_ticks
    return delta_ticks / current.clock_ticks_per_second / elapsed * 100.0
