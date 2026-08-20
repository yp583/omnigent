#!/usr/bin/env python3
"""Compare standalone and Omnigent resource-scaling reports."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from dev.benchmarks.resources.analysis import fit_linear, paired_deltas


def _pss_series(report: dict[str, Any]) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for point in report.get("points", []):
        if not isinstance(point, dict):
            continue
        sessions = point.get("sessions")
        pss = point.get("settled_pss_bytes")
        if isinstance(sessions, int) and isinstance(pss, (int, float)):
            grouped[sessions].append(float(pss))
    return dict(grouped)


def compare_reports(standalone: dict[str, Any], wrapped: dict[str, Any]) -> dict[str, object]:
    """Return paired PSS deltas and their fixed/marginal fit."""
    standalone_series = _pss_series(standalone)
    wrapped_series = _pss_series(wrapped)
    deltas = paired_deltas(standalone_series, wrapped_series)
    fit_points = [(float(sessions), delta) for sessions, delta in deltas.items()]
    return {
        "standalone_median_pss_bytes": {
            str(key): statistics.median(values)
            for key, values in sorted(standalone_series.items())
        },
        "wrapped_median_pss_bytes": {
            str(key): statistics.median(values) for key, values in sorted(wrapped_series.items())
        },
        "omnigent_delta_pss_bytes": {str(key): value for key, value in deltas.items()},
        "delta_fit": fit_linear(fit_points).to_dict() if len(fit_points) >= 2 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("standalone", type=Path)
    parser.add_argument("wrapped", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    standalone = json.loads(args.standalone.read_text(encoding="utf-8"))
    wrapped = json.loads(args.wrapped.read_text(encoding="utf-8"))
    comparison = compare_reports(standalone, wrapped)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
