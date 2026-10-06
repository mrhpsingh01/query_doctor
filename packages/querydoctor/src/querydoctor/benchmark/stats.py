"""Robust summary statistics for repeated timings: medians and percentiles, never a single run."""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from querydoctor.benchmark.models import RunStats


def _percentile(ordered: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (the common 'inclusive' definition)."""
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def summarize(samples_ms: Sequence[float]) -> RunStats:
    if len(samples_ms) == 0:
        raise ValueError("cannot summarize zero samples")
    ordered = sorted(float(s) for s in samples_ms)
    mean = statistics.fmean(ordered)
    stdev = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    return RunStats(
        n=len(ordered),
        median_ms=statistics.median(ordered),
        p95_ms=_percentile(ordered, 0.95),
        mean_ms=mean,
        stdev_ms=stdev,
        cv=stdev / mean if mean > 0 else 0.0,
        min_ms=ordered[0],
        max_ms=ordered[-1],
    )


def improvement_pct(before_median_ms: float, after_median_ms: float) -> float:
    """Positive = faster. A regression is negative."""
    if before_median_ms <= 0:
        return 0.0
    return (before_median_ms - after_median_ms) / before_median_ms * 100.0
