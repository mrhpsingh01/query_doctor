"""Warnings that tell a reader how far to trust a before/after comparison."""

from __future__ import annotations

from querydoctor.benchmark.models import BenchmarkConfig, RunStats

MIN_RUNS = 5
CACHE_WARMING_RATIO = 3.0
_BLOCKING_PREFIXES = ("Output differs",)


def is_blocking(warning: str) -> bool:
    """A blocking warning means the comparison must not award benchmark validation (the results are not comparable)."""
    return warning.startswith(_BLOCKING_PREFIXES)


def reliability_warnings(
    before: RunStats, after: RunStats, *, config: BenchmarkConfig, checksum_match: bool, first_run_ratio: float
) -> list[str]:
    warnings: list[str] = []
    for label, s in (("before", before), ("after", after)):
        if s.cv > config.cv_warn:
            warnings.append(
                f"High variance in the {label} timings (CV {s.cv:.0%}, runs from {s.min_ms:.1f} to {s.max_ms:.1f} ms): "
                "treat the median with caution and rerun on a quieter machine."
            )
        if s.n < MIN_RUNS:
            warnings.append(f"Too few runs in the {label} measurement ({s.n}; at least {MIN_RUNS} recommended).")
    if first_run_ratio > CACHE_WARMING_RATIO:
        warnings.append(
            f"Cache warming dominates: the first measured run was {first_run_ratio:.1f}x the median, so the "
            "database was not fully warm."
        )
    if not checksum_match:
        warnings.append(
            "Output differs: the query returned different results with the index, so the timings are not comparable "
            "and the index is not validated."
        )
    return warnings
