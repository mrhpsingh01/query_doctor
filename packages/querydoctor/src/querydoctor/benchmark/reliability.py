"""Warnings that tell a reader how far to trust a before/after comparison."""

from __future__ import annotations

from querydoctor.benchmark.models import BenchmarkConfig, BenchmarkResult, RunStats

MIN_RUNS = 5
CACHE_WARMING_RATIO = 3.0
_BLOCKING_PREFIXES = ("Output differs",)


def is_blocking(warning: str) -> bool:
    """A blocking warning means the comparison must not award benchmark validation (the results are not comparable)."""
    return warning.startswith(_BLOCKING_PREFIXES)


MIN_IMPROVEMENT_PCT = 10.0  # below this, a difference is indistinguishable from run-to-run noise on a shared machine


def compare_outputs(
    before_unordered: str, after_unordered: str, before_ordered: str | None, after_ordered: str | None
) -> tuple[bool, list[str]]:
    """Decide whether two runs returned the same output.

    The unordered (set) checksum decides. Rows that tie on an ORDER BY key may legitimately come back in another order, so a
    difference in ordered checksums alone is only a note, never a mismatch.
    """
    if before_unordered != after_unordered:
        return False, []
    if before_ordered is not None and before_ordered != after_ordered:
        return True, [
            "Row order among rows with equal sort keys differs between the runs; the rows themselves are identical."
        ]
    return True, []


def awards_validation(result: BenchmarkResult) -> bool:
    """May this result upgrade a candidate to 'benchmark-validated'? Only a clear, comparable, attributable speed-up.

    "Clear" means the gap survives the noise: the median improved by at least MIN_IMPROVEMENT_PCT *and* even the slow end
    of the indexed runs (p95) beats the typical original run (median). That is robust to run-to-run jitter without
    penalising sub-millisecond queries, whose relative variance is large even when every indexed run is far faster.
    """
    separated = result.after.p95_ms < result.before.median_ms
    return (
        result.improvement_pct >= MIN_IMPROVEMENT_PCT
        and separated
        and result.checksum_match
        and result.index_used
        and not any(is_blocking(w) for w in result.warnings)
    )


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
