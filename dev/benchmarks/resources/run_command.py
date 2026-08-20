#!/usr/bin/env python3
"""Launch N standalone harness commands and measure their complete trees.

The command follows ``--`` and is executed without a shell. Literal
``{session}`` and ``{workspace}`` placeholders are replaced per process::

    uv run --no-sync -m dev.benchmarks.resources.run_command \
      --name my-harness --sessions 0,1,2,5,10,15 --runs 3 \
      --ready-delay 2 --settle-seconds 4 --output standalone.json -- \
      my-harness serve --workspace {workspace}

Protocol-specific drivers should replace the readiness delay with a real
handshake when added. This generic path is sufficient for resident process and
memory baselines and records the limitation in its config.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import IO

from dev.benchmarks.omnigent.schema import git_branch, git_sha
from dev.benchmarks.resources.analysis import fit_linear
from dev.benchmarks.resources.procfs import ProcessRoot, ProcfsSampler, cpu_percent_between
from dev.benchmarks.resources.run_omnigent import _parse_counts, _settled_pss

_SCHEMA_VERSION = 1


def _render_command(command: list[str], *, session: int, workspace: Path) -> list[str]:
    """Replace the two supported literal command placeholders."""
    return [
        value.replace("{session}", str(session)).replace("{workspace}", str(workspace))
        for value in command
    ]


def _terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _measure_point(
    sessions: int,
    *,
    run_index: int,
    name: str,
    command: list[str],
    ready_delay: float,
    settle_seconds: float,
    interval: float,
) -> dict[str, object]:
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[IO[bytes]] = []
    with tempfile.TemporaryDirectory(prefix="omni-standalone-bench-") as tmp:
        root = Path(tmp)
        started_at = time.perf_counter()
        try:
            for session in range(sessions):
                workspace = root / f"session-{session}"
                workspace.mkdir()
                log = (root / f"session-{session}.log").open("wb")
                logs.append(log)
                processes.append(
                    subprocess.Popen(
                        _render_command(command, session=session, workspace=workspace),
                        cwd=workspace,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
            time.sleep(ready_delay)
            exited = [process.pid for process in processes if process.poll() is not None]
            if exited:
                raise RuntimeError(f"standalone harness exited before readiness: {exited}")
            readiness_ms = (time.perf_counter() - started_at) * 1000

            samples: list[dict[str, object]] = []
            if processes:
                sampler = ProcfsSampler()
                roots = [
                    ProcessRoot(
                        pid=process.pid,
                        role=name,
                        session_id=f"session-{index}",
                        include_process_group=True,
                    )
                    for index, process in enumerate(processes)
                ]
                previous = None
                deadline = time.monotonic() + settle_seconds
                while time.monotonic() < deadline:
                    snapshot = sampler.snapshot(roots)
                    payload = snapshot.to_dict()
                    payload["cpu_percent"] = (
                        cpu_percent_between(previous, snapshot) if previous is not None else None
                    )
                    samples.append(payload)
                    previous = snapshot
                    time.sleep(interval)
            return {
                "sessions": sessions,
                "run": run_index,
                "timing": {"process_ready_all_ms": readiness_ms},
                # The standalone harness has no resident coordinator at N=0.
                # Preserve that real zero so fixed-intercept wrapper deltas can
                # be compared against Omnigent's N=0 control-plane baseline.
                "settled_pss_bytes": 0 if sessions == 0 else _settled_pss(samples),
                "settled_sample_count": len(samples),
                "resources": {
                    "sampler": "linux-procfs",
                    "samples": samples,
                },
            }
        finally:
            _terminate(processes)
            for log in logs:
                log.close()


def _analysis(points: list[dict[str, object]]) -> dict[str, object]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for point in points:
        sessions = point.get("sessions")
        pss = point.get("settled_pss_bytes")
        if isinstance(sessions, int) and isinstance(pss, int):
            grouped[sessions].append(pss)
    medians = {
        sessions: statistics.median(values)
        for sessions, values in sorted(grouped.items())
        if values
    }
    fit_points = [(float(sessions), float(value)) for sessions, value in medians.items()]
    return {
        "pss_fit": fit_linear(fit_points).to_dict() if len(fit_points) >= 2 else None,
        "median_pss_bytes": {str(key): value for key, value in medians.items()},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--sessions", type=_parse_counts, default=(0, 1, 2, 5, 10, 15))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--ready-delay", type=float, default=2.0)
    parser.add_argument("--settle-seconds", type=float, default=4.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        raise SystemExit("standalone resource scaling requires Linux procfs")
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise SystemExit("a command is required after --")
    if min(args.runs, args.ready_delay, args.settle_seconds, args.interval) <= 0:
        raise SystemExit("runs and timing values must be positive")

    points: list[dict[str, object]] = []
    for sessions in args.sessions:
        for run_index in range(1, args.runs + 1):
            print(f"sessions={sessions} run={run_index}/{args.runs}", flush=True)
            points.append(
                _measure_point(
                    sessions,
                    run_index=run_index,
                    name=args.name,
                    command=command,
                    ready_delay=args.ready_delay,
                    settle_seconds=args.settle_seconds,
                    interval=args.interval,
                )
            )
    report = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "git_branch": git_branch(),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "config": {
            "mode": "standalone-command",
            "name": args.name,
            "sessions": list(args.sessions),
            "runs": args.runs,
            "command": command,
            "readiness": {"type": "alive-after-delay", "delay_s": args.ready_delay},
            "settle_seconds": args.settle_seconds,
            "interval_s": args.interval,
        },
        "points": points,
        "analysis": _analysis(points),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
