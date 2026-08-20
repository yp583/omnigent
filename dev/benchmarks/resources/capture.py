#!/usr/bin/env python3
"""Capture raw resource samples for one or more already-running roots.

Example::

    python -m dev.benchmarks.resources.capture \
      --root server=1234 --root runner:session-a=5678 \
      --samples 10 --interval 0.5 --output resources.json
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

from dev.benchmarks.resources.procfs import ProcessRoot, ProcfsSampler, cpu_percent_between

_SCHEMA_VERSION = 1


def _parse_root(value: str) -> ProcessRoot:
    """Parse ``ROLE[:SESSION]=PID`` into a process root."""
    label, separator, raw_pid = value.rpartition("=")
    if not separator or not label:
        raise argparse.ArgumentTypeError("root must be ROLE[:SESSION]=PID")
    try:
        pid = int(raw_pid)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("root PID must be an integer") from exc
    role, session_separator, session_id = label.partition(":")
    if not role:
        raise argparse.ArgumentTypeError("root role must not be empty")
    return ProcessRoot(
        pid=pid,
        role=role,
        session_id=session_id if session_separator and session_id else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True, type=_parse_root)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    """Capture samples and write the versioned JSON document."""
    args = _parser().parse_args()
    if args.samples <= 0:
        raise SystemExit("--samples must be positive")
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")

    sampler = ProcfsSampler()
    snapshots = []
    previous = None
    for index in range(args.samples):
        snapshot = sampler.snapshot(args.root)
        payload = snapshot.to_dict()
        payload["cpu_percent"] = (
            cpu_percent_between(previous, snapshot) if previous is not None else None
        )
        snapshots.append(payload)
        previous = snapshot
        if index + 1 < args.samples:
            time.sleep(args.interval)

    report = {
        "schema_version": _SCHEMA_VERSION,
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "config": {"samples": args.samples, "interval_s": args.interval},
        "snapshots": snapshots,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
