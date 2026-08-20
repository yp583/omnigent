"""Unit coverage for whole-process-tree resource measurement."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from dev.benchmarks.resources.analysis import fit_linear, paired_deltas
from dev.benchmarks.resources.compare import compare_reports
from dev.benchmarks.resources.procfs import (
    ProcessRoot,
    ProcfsSampler,
    _parse_stat,
    cpu_percent_between,
)
from dev.benchmarks.resources.run_command import _render_command
from dev.benchmarks.resources.run_omnigent import (
    _parse_counts,
    _parser,
    _scaling_analysis,
    _settled_pss,
)


def _write_process(
    proc_root: Path,
    *,
    pid: int,
    ppid: int,
    pgid: int,
    start_ticks: int,
    cpu_ticks: int,
    pss_kib: int,
    rss_kib: int,
    private_kib: int,
    threads: int = 1,
    fds: int = 2,
    command: str = "worker",
) -> None:
    """Write the procfs subset consumed by ``ProcfsSampler``."""
    process_dir = proc_root / str(pid)
    process_dir.mkdir(parents=True)
    fields = ["0"] * 50
    fields[0] = "S"
    fields[1] = str(ppid)
    fields[2] = str(pgid)
    fields[11] = str(cpu_ticks // 2)
    fields[12] = str(cpu_ticks - cpu_ticks // 2)
    fields[19] = str(start_ticks)
    (process_dir / "stat").write_text(
        f"{pid} ({command}) " + " ".join(fields) + "\n",
        encoding="utf-8",
    )
    (process_dir / "status").write_text(
        f"VmRSS:\t{rss_kib} kB\nThreads:\t{threads}\nVmSwap:\t0 kB\n",
        encoding="utf-8",
    )
    (process_dir / "smaps_rollup").write_text(
        "\n".join(
            [
                f"Rss: {rss_kib} kB",
                f"Pss: {pss_kib} kB",
                f"Pss_Anon: {pss_kib - 1} kB",
                "Pss_File: 1 kB",
                "Pss_Shmem: 0 kB",
                f"Private_Clean: {private_kib // 2} kB",
                f"Private_Dirty: {private_kib - private_kib // 2} kB",
                "Swap: 0 kB",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (process_dir / "cmdline").write_bytes(f"python\0{command}\0".encode())
    fd_dir = process_dir / "fd"
    fd_dir.mkdir()
    for index in range(fds):
        (fd_dir / str(index)).touch()


def test_parse_stat_tolerates_close_paren_in_command() -> None:
    fields = ["0"] * 25
    fields[0] = "S"
    fields[1] = "1"
    fields[2] = "42"
    fields[11] = "7"
    fields[12] = "5"
    fields[19] = "999"
    stat = _parse_stat("42 (odd) worker) " + " ".join(fields))
    assert stat.pid == 42
    assert stat.command == "odd) worker"
    assert stat.ppid == 1
    assert stat.pgid == 42
    assert stat.cpu_ticks == 12
    assert stat.start_time_ticks == 999


def test_sampler_attributes_nested_roots_and_process_group(tmp_path: Path) -> None:
    _write_process(
        tmp_path,
        pid=100,
        ppid=1,
        pgid=100,
        start_ticks=1000,
        cpu_ticks=10,
        pss_kib=100,
        rss_kib=200,
        private_kib=80,
        threads=2,
        fds=3,
        command="host root",
    )
    _write_process(
        tmp_path,
        pid=110,
        ppid=100,
        pgid=110,
        start_ticks=1100,
        cpu_ticks=20,
        pss_kib=50,
        rss_kib=90,
        private_kib=40,
        threads=3,
        fds=4,
        command="runner",
    )
    _write_process(
        tmp_path,
        pid=111,
        ppid=110,
        pgid=110,
        start_ticks=1110,
        cpu_ticks=30,
        pss_kib=25,
        rss_kib=45,
        private_kib=20,
        command="harness child",
    )
    # Reparented but still in runner's process group.
    _write_process(
        tmp_path,
        pid=112,
        ppid=1,
        pgid=110,
        start_ticks=1120,
        cpu_ticks=40,
        pss_kib=10,
        rss_kib=20,
        private_kib=8,
        command="detached tool",
    )
    # Unrelated process must not leak into totals.
    _write_process(
        tmp_path,
        pid=999,
        ppid=1,
        pgid=999,
        start_ticks=9990,
        cpu_ticks=500,
        pss_kib=999,
        rss_kib=999,
        private_kib=999,
    )

    snapshot = ProcfsSampler(tmp_path, clock_ticks_per_second=100).snapshot(
        [
            ProcessRoot(pid=100, role="host"),
            ProcessRoot(pid=110, role="runner", session_id="session-a"),
        ]
    )

    assert snapshot.complete
    assert [process.pid for process in snapshot.observations] == [100, 110, 111, 112]
    roles = {process.pid: process.role for process in snapshot.observations}
    assert roles == {100: "host", 110: "runner", 111: "runner", 112: "runner"}
    assert snapshot.total.pss_bytes == (100 + 50 + 25 + 10) * 1024
    assert snapshot.total.uss_bytes == (80 + 40 + 20 + 8) * 1024
    assert snapshot.total.process_count == 4
    assert snapshot.total.thread_count == 7
    assert snapshot.total.fd_count == 11
    assert snapshot.by_role["host"].process_count == 1
    assert snapshot.by_role["runner"].process_count == 3
    assert snapshot.by_session["session-a"].process_count == 3
    assert snapshot.to_dict()["complete"] is True


def test_sampler_preserves_missing_pss_as_none(tmp_path: Path) -> None:
    _write_process(
        tmp_path,
        pid=200,
        ppid=1,
        pgid=200,
        start_ticks=2000,
        cpu_ticks=0,
        pss_kib=50,
        rss_kib=60,
        private_kib=40,
    )
    os.unlink(tmp_path / "200" / "smaps_rollup")

    snapshot = ProcfsSampler(tmp_path, clock_ticks_per_second=100).snapshot(
        [ProcessRoot(pid=200, role="server")]
    )

    assert snapshot.complete
    assert snapshot.total.pss_bytes is None
    assert snapshot.total.uss_bytes is None
    assert snapshot.total.rss_bytes == 60 * 1024


def test_sampler_reports_missing_root(tmp_path: Path) -> None:
    snapshot = ProcfsSampler(tmp_path, clock_ticks_per_second=100).snapshot(
        [ProcessRoot(pid=404, role="missing")]
    )
    assert not snapshot.complete
    assert snapshot.missing_root_pids == (404,)
    assert snapshot.total.process_count == 0


def test_cpu_percent_uses_stable_pid_identity(tmp_path: Path, monkeypatch) -> None:
    _write_process(
        tmp_path,
        pid=300,
        ppid=1,
        pgid=300,
        start_ticks=3000,
        cpu_ticks=100,
        pss_kib=10,
        rss_kib=20,
        private_kib=8,
    )
    sampler = ProcfsSampler(tmp_path, clock_ticks_per_second=100)
    monotonic_values = iter([1_000_000_000, 2_000_000_000, 3_000_000_000])
    monkeypatch.setattr(
        "dev.benchmarks.resources.procfs.time.monotonic_ns",
        monotonic_values.__next__,
    )
    first = sampler.snapshot([ProcessRoot(pid=300, role="worker")])

    stat_path = tmp_path / "300" / "stat"
    stat = _parse_stat(stat_path.read_text())
    _write_stat = ["0"] * 50
    _write_stat[0] = "S"
    _write_stat[1] = str(stat.ppid)
    _write_stat[2] = str(stat.pgid)
    _write_stat[11] = "75"
    _write_stat[12] = "75"
    _write_stat[19] = str(stat.start_time_ticks)
    stat_path.write_text("300 (worker) " + " ".join(_write_stat), encoding="utf-8")
    second = sampler.snapshot([ProcessRoot(pid=300, role="worker")])
    assert cpu_percent_between(first, second) == pytest.approx(50.0)

    # Same PID with a different start tick is a new process. Its own 25 ticks
    # are charged, never the 150-tick predecessor baseline.
    _write_stat[11] = "12"
    _write_stat[12] = "13"
    _write_stat[19] = "250"
    stat_path.write_text("300 (replacement) " + " ".join(_write_stat), encoding="utf-8")
    third = sampler.snapshot([ProcessRoot(pid=300, role="worker")])
    assert cpu_percent_between(second, third) == pytest.approx(25.0)


def test_linear_fit_reports_fixed_and_marginal_cost() -> None:
    fit = fit_linear([(0, 100), (1, 110), (2, 120), (5, 150)])
    assert fit.intercept == pytest.approx(100)
    assert fit.slope == pytest.approx(10)
    assert fit.r_squared == pytest.approx(1)


def test_linear_fit_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError, match="at least two points"):
        fit_linear([(1, 1)])
    with pytest.raises(ValueError, match="distinct x"):
        fit_linear([(1, 1), (1, 2)])


def test_paired_deltas_use_medians_at_shared_counts() -> None:
    assert paired_deltas(
        {1: [100, 102, 500], 2: [200], 5: [500]},
        {1: [110, 112, 900], 2: [230], 10: [1000]},
    ) == {1: 10, 2: 30}


def test_parse_counts_sorts_deduplicates_and_rejects_negative() -> None:
    assert _parse_counts("10, 0,2,10") == (0, 2, 10)
    with pytest.raises(argparse.ArgumentTypeError, match="non-negative"):
        _parse_counts("1,-1")


def test_resource_driver_accepts_only_claude_and_codex() -> None:
    parser = _parser()
    assert parser.parse_args(["--output", "report.json"]).harness == "codex"
    assert (
        parser.parse_args(["--harness", "claude-sdk", "--output", "report.json"]).harness
        == "claude-sdk"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--harness", "openai-agents", "--output", "report.json"])


def test_settled_pss_uses_only_complete_integer_samples() -> None:
    samples = [
        {"complete": True, "total": {"pss_bytes": 100}},
        {"complete": False, "total": {"pss_bytes": 10_000}},
        {"complete": True, "total": {"pss_bytes": None}},
        {"complete": True, "total": {"pss_bytes": 120}},
    ]
    assert _settled_pss(samples) == 110


def test_scaling_analysis_fits_run_medians() -> None:
    points = [
        {
            "sessions": 0,
            "settled_pss_bytes": 100,
            "timing": {"warm_ttft_ms": []},
        },
        {
            "sessions": 1,
            "settled_pss_bytes": 110,
            "timing": {"warm_ttft_ms": [10.0, 20.0]},
        },
        {
            "sessions": 2,
            "settled_pss_bytes": 120,
            "timing": {"warm_ttft_ms": [30.0]},
        },
    ]
    analysis = _scaling_analysis(points)
    fit = analysis["pss_fit"]
    assert isinstance(fit, dict)
    assert fit["intercept"] == pytest.approx(100)
    assert fit["slope"] == pytest.approx(10)
    assert analysis["warm_ttft_p95_ms"] == {"1": 20.0, "2": 30.0}


def test_standalone_command_replaces_only_supported_placeholders(tmp_path: Path) -> None:
    assert _render_command(
        ["harness", "--session={session}", "{workspace}", "{unrelated}"],
        session=3,
        workspace=tmp_path,
    ) == ["harness", "--session=3", str(tmp_path), "{unrelated}"]


def test_compare_reports_returns_paired_delta_fit() -> None:
    standalone = {
        "points": [
            {"sessions": 0, "settled_pss_bytes": 100},
            {"sessions": 1, "settled_pss_bytes": 110},
            {"sessions": 2, "settled_pss_bytes": 120},
        ]
    }
    wrapped = {
        "points": [
            {"sessions": 0, "settled_pss_bytes": 150},
            {"sessions": 1, "settled_pss_bytes": 165},
            {"sessions": 2, "settled_pss_bytes": 180},
        ]
    }
    comparison = compare_reports(standalone, wrapped)
    assert comparison["omnigent_delta_pss_bytes"] == {"0": 50, "1": 55, "2": 60}
    fit = comparison["delta_fit"]
    assert isinstance(fit, dict)
    assert fit["intercept"] == pytest.approx(50)
    assert fit["slope"] == pytest.approx(5)
