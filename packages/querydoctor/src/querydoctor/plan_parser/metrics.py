"""Pure derived-metric functions over normalized plan nodes."""

from __future__ import annotations

from typing import Literal


def error_ratio(estimated: float, actual: float | None, loops: int | None) -> float | None:
    """Symmetric estimation error, floored at one row so empty estimates never divide by zero."""
    if actual is None or not loops:
        return None
    est, act = max(estimated, 1.0), max(actual, 1.0)
    return max(act / est, est / act)


def filter_waste(actual_rows: float | None, removed: int | None, loops: int | None) -> float | None:
    """Fraction of rows visited that the filter discarded (loop-invariant)."""
    if actual_rows is None or removed is None or not loops:
        return None
    visited = actual_rows + removed
    return removed / visited if visited > 0 else 0.0


def total_time_ms(actual_time_ms: float | None, loops: int | None) -> float | None:
    if actual_time_ms is None or loops is None:
        return None
    return actual_time_ms * loops


def exclusive_blocks(own: int | None, children: list[int | None]) -> int | None:
    """Blocks attributable to a node itself: PostgreSQL's counters include all children."""
    if own is None:
        return None
    return max(own - sum(c or 0 for c in children), 0)


def exclusive_time_ms(own_total: float | None, child_totals: list[float | None]) -> float | None:
    if own_total is None:
        return None
    return max(own_total - sum(t or 0.0 for t in child_totals), 0.0)


def io_pressure(
    temp_read: int | None, temp_written: int | None, shared_read: int | None, shared_hit: int | None
) -> Literal["none", "memory", "disk", "temp"]:
    if (temp_read or 0) > 0 or (temp_written or 0) > 0:
        return "temp"
    if (shared_read or 0) > 0:
        return "disk"
    if (shared_hit or 0) > 0:
        return "memory"
    return "none"
