"""Scaling and paired-delta analysis for resource benchmark reports."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class LinearFit:
    """Least-squares fixed and marginal cost model."""

    intercept: float
    slope: float
    r_squared: float
    points: int

    def to_dict(self) -> dict[str, float | int]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def fit_linear(points: Sequence[tuple[float, float]]) -> LinearFit:
    """Fit ``y = intercept + slope*x`` without a numerical dependency."""
    if len(points) < 2:
        raise ValueError("at least two points are required")
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    mean_x = statistics.fmean(x_values)
    mean_y = statistics.fmean(y_values)
    variance_x = sum((value - mean_x) ** 2 for value in x_values)
    if variance_x == 0:
        raise ValueError("at least two distinct x values are required")
    covariance = sum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(x_values, y_values, strict=True)
    )
    slope = covariance / variance_x
    intercept = mean_y - slope * mean_x
    residual = sum((y_value - (intercept + slope * x_value)) ** 2 for x_value, y_value in points)
    total = sum((value - mean_y) ** 2 for value in y_values)
    r_squared = 1.0 if total == 0 and residual == 0 else (1.0 - residual / total if total else 0.0)
    return LinearFit(
        intercept=intercept,
        slope=slope,
        r_squared=r_squared,
        points=len(points),
    )


def paired_deltas(
    standalone: Mapping[int, Sequence[float]],
    wrapped: Mapping[int, Sequence[float]],
) -> dict[int, float]:
    """Return median wrapped-minus-standalone deltas at shared session counts."""
    deltas: dict[int, float] = {}
    for sessions in sorted(standalone.keys() & wrapped.keys()):
        bare_values = standalone[sessions]
        wrapped_values = wrapped[sessions]
        if not bare_values or not wrapped_values:
            continue
        deltas[sessions] = statistics.median(wrapped_values) - statistics.median(bare_values)
    return deltas
