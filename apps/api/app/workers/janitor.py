"""Housekeeping for the benchmark bench instance."""

from __future__ import annotations

import logging

from querydoctor.benchmark.sandbox import drop_stale

log = logging.getLogger(__name__)

DEFAULT_MAX_AGE_S = 900  # longer than any benchmark's time limit, so a live benchmark's sandbox is never touched


def cleanup_stale_sandboxes(bench_dsn: str, max_age_s: float = DEFAULT_MAX_AGE_S) -> int:
    """Drop sandboxes left behind by crashed workers. Never raises (the bench instance may be down at startup)."""
    try:
        dropped = drop_stale(bench_dsn, max_age_s)
    except Exception:
        log.warning("could not clean up stale benchmark sandboxes", exc_info=True)
        return 0
    if dropped:
        log.info("dropped %s stale benchmark sandbox(es)", dropped)
    return dropped
