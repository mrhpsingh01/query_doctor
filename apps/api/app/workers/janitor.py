"""Housekeeping for the benchmark bench instance."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from querydoctor.benchmark.sandbox import drop_stale
from sqlalchemy import and_, or_, select

from app.models import Analysis, BenchmarkRun

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


def fail_stale_jobs(sessionmaker, max_age_s: float = DEFAULT_MAX_AGE_S) -> int:
    """Fail analyses and benchmarks that a crashed worker left 'running' (or never started) past ``max_age_s``.

    Without this a job whose worker died stays running forever, because redelivered tasks return early for any job that
    is not queued. Returns how many jobs were failed."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=max_age_s)
    failed = 0
    with sessionmaker() as session:
        for model, noun in ((BenchmarkRun, "benchmark"), (Analysis, "analysis")):
            stale = session.scalars(
                select(model).where(
                    or_(
                        and_(model.status == "running", model.started_at < cutoff),
                        and_(model.status == "queued", model.created_at < cutoff),
                    )
                )
            ).all()
            for job in stale:
                job.status = "failed"
                job.error = f"The worker stopped before this {noun} finished. Start it again."
                job.finished_at = now
                failed += 1
        session.commit()
    if failed:
        log.info("failed %s stale job(s)", failed)
    return failed
