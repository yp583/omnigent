#!/usr/bin/env python3
"""Measure current hosted-session scaling with a zero-latency provider.

This runner intentionally starts exactly N host-owned runners for each point:
the shared boot runner used by latency journeys is disabled. It measures runner
readiness, first-turn TTFT (including harness cold start), warm TTFT, settled
whole-tree resources, and raw attributed timelines.

The authoritative resource path requires Linux procfs::

    uv run --no-sync dev/benchmarks/resources/run_omnigent.py \
      --sessions 0,1,2,5,10,15 --runs 3 --output omnigent-resources.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dev.benchmarks.omnigent.environment import BenchEnvironment
from dev.benchmarks.omnigent.schema import git_branch, git_sha
from dev.benchmarks.resources.analysis import fit_linear

_SCHEMA_VERSION = 1
_DEFAULT_SESSION_COUNTS = (0, 1, 2, 5, 10, 15)
_SUPPORTED_HARNESSES = ("codex", "claude-sdk")
_DEFAULT_HARNESS = "codex"
_DEFAULT_MODEL = "mock-bench-brain"
_ROLLOUT_ENV_VARS = (
    "OMNIGENT_IN_PROCESS_HARNESSES",
    "OMNIGENT_IN_PROCESS_NATIVE_HARNESSES",
    "OMNIGENT_HARNESS_STARTUP_CONCURRENCY",
    "OMNIGENT_CODEX_STARTUP_CONCURRENCY",
    "OMNIGENT_CLAUDE_STARTUP_CONCURRENCY",
    "OMNIGENT_CLAUDE_CONNECT_TIMEOUT_S",
)


def _parse_counts(value: str) -> tuple[int, ...]:
    """Parse a sorted, unique comma-separated session-count list."""
    try:
        counts = tuple(sorted({int(part.strip()) for part in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("session counts must be integers") from exc
    if not counts or any(count < 0 for count in counts):
        raise argparse.ArgumentTypeError("session counts must be non-negative")
    return counts


def _percentile(values: list[float], percentile: float) -> float | None:
    """Return a nearest-rank percentile, or ``None`` for an empty series."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((percentile / 100) * len(ordered) + 0.999) - 1))
    return ordered[index]


async def _timed_first_delta(env: BenchEnvironment, session_id: str, label: str) -> float:
    started_at = time.perf_counter()
    await env.time_to_first_delta(session_id, label)
    return (time.perf_counter() - started_at) * 1000


def _settled_pss(samples: list[dict[str, object]]) -> int | None:
    values: list[int] = []
    for sample in samples:
        total = sample.get("total")
        if sample.get("complete") is not True or not isinstance(total, dict):
            continue
        value = total.get("pss_bytes")
        if isinstance(value, int):
            values.append(value)
    return int(statistics.median(values)) if values else None


async def _measure_point(
    sessions: int,
    *,
    run_index: int,
    settle_seconds: float,
    harness: str,
    model: str,
) -> dict[str, object]:
    async with BenchEnvironment(
        with_host=True,
        with_boot_runner=False,
        harness=harness,
        model=model,
    ) as env:
        await env.set_mock_fallback("benchmark token", stream=True)
        agent_name = await env.ensure_agent(f"resource-bench-{run_index}")
        agent_id = await env.agent_id(agent_name)

        readiness_started = time.perf_counter()
        session_ids = list(
            await asyncio.gather(*(env.create_hosted_session(agent_id) for _ in range(sessions)))
        )
        await asyncio.gather(
            *(env.wait_session_runner_online(session_id) for session_id in session_ids)
        )
        readiness_ms = (time.perf_counter() - readiness_started) * 1000

        cold_ttft_ms = list(
            await asyncio.gather(
                *(
                    _timed_first_delta(env, session_id, "resource benchmark cold harness")
                    for session_id in session_ids
                )
            )
        )
        await asyncio.gather(*(env._wait_idle(session_id) for session_id in session_ids))

        warm_ttft_ms = list(
            await asyncio.gather(
                *(
                    _timed_first_delta(env, session_id, "resource benchmark warm turn")
                    for session_id in session_ids
                )
            )
        )
        await asyncio.gather(*(env._wait_idle(session_id) for session_id in session_ids))

        settle_start = len(env.resource_tree_samples)
        await asyncio.sleep(settle_seconds)
        settled_samples = env.resource_tree_samples[settle_start:]
        resource_usage = env.resource_usage

    return {
        "sessions": sessions,
        "run": run_index,
        "timing": {
            "runner_readiness_all_ms": readiness_ms,
            "cold_harness_ttft_ms": cold_ttft_ms,
            "cold_harness_ttft_p95_ms": _percentile(cold_ttft_ms, 95),
            "warm_ttft_ms": warm_ttft_ms,
            "warm_ttft_p95_ms": _percentile(warm_ttft_ms, 95),
        },
        "settled_pss_bytes": _settled_pss(settled_samples),
        "settled_sample_count": len(settled_samples),
        "resources": resource_usage,
    }


def _scaling_analysis(points: list[dict[str, object]]) -> dict[str, object]:
    pss_by_count: dict[int, list[int]] = defaultdict(list)
    warm_by_count: dict[int, list[float]] = defaultdict(list)
    for point in points:
        sessions = point.get("sessions")
        pss = point.get("settled_pss_bytes")
        timing = point.get("timing")
        if isinstance(sessions, int) and isinstance(pss, int):
            pss_by_count[sessions].append(pss)
        if isinstance(sessions, int) and isinstance(timing, dict):
            values = timing.get("warm_ttft_ms")
            if isinstance(values, list):
                numeric = [value for value in values if isinstance(value, (int, float))]
                if numeric:
                    warm_by_count[sessions].extend(numeric)

    pss_points = [
        (float(sessions), float(statistics.median(values)))
        for sessions, values in sorted(pss_by_count.items())
        if values
    ]
    return {
        "pss_fit": fit_linear(pss_points).to_dict() if len(pss_points) >= 2 else None,
        "median_pss_bytes": {
            str(sessions): statistics.median(values)
            for sessions, values in sorted(pss_by_count.items())
        },
        "warm_ttft_p95_ms": {
            str(sessions): _percentile(values, 95)
            for sessions, values in sorted(warm_by_count.items())
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    points: list[dict[str, object]] = []
    for sessions in args.sessions:
        for run_index in range(1, args.runs + 1):
            print(f"sessions={sessions} run={run_index}/{args.runs}", flush=True)
            points.append(
                await _measure_point(
                    sessions,
                    run_index=run_index,
                    settle_seconds=args.settle_seconds,
                    harness=args.harness,
                    model=args.model,
                )
            )
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "git_branch": git_branch(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "config": {
            "sessions": list(args.sessions),
            "runs": args.runs,
            "settle_seconds": args.settle_seconds,
            "harness": args.harness,
            "model": args.model,
            "provider": "zero-latency-mock",
            "boot_runner": False,
            "rollout_environment": {key: os.environ.get(key) for key in _ROLLOUT_ENV_VARS},
        },
        "points": points,
        "analysis": _scaling_analysis(points),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions",
        type=_parse_counts,
        default=_DEFAULT_SESSION_COUNTS,
        help="Comma-separated session counts (default: 0,1,2,5,10,15)",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--settle-seconds", type=float, default=4.0)
    parser.add_argument(
        "--harness",
        choices=_SUPPORTED_HARNESSES,
        default=_DEFAULT_HARNESS,
        help="Coding harness to measure (default: codex)",
    )
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        raise SystemExit("resource scaling gates require Linux procfs")
    if args.runs <= 0 or args.settle_seconds <= 0:
        raise SystemExit("--runs and --settle-seconds must be positive")
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
