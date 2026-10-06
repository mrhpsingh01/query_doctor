"""The benchmark job: run an approved candidate through the sandbox benchmark and record the outcome.

Every exit path leaves the run in a terminal state with a matching event. Only a completed, non-regressing run whose
output matched awards ``benchmark_validated``; anything else leaves the candidate's evidence level untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from querydoctor.adapters import get_adapter
from querydoctor.benchmark import BenchmarkConfig, awards_validation
from querydoctor.benchmark.runner import BenchmarkCancelled, BenchmarkFailed, run_benchmark
from querydoctor.candidates.models import IndexCandidate, IndexColumn
from sqlalchemy.orm import Session

from app.models import Analysis, AuditEvent, BenchmarkRun, RecommendationCandidate
from app.services import ranking
from app.services.events import EventBus
from app.services.pipeline import emit

log = logging.getLogger(__name__)

TERMINAL = {"completed", "failed", "cancelled"}
STAGE_EVENTS = {"creating_sandbox", "warmup_before", "measuring_before", "creating_index", "analyzing",
                "warmup_after", "measuring_after", "comparing", "teardown"}


@dataclass
class BenchmarkDeps:
    bench_dsn: str
    bus: EventBus
    run: Callable = run_benchmark  # replaceable in tests
    mongo_bench_uri: str = ""  # bench user on the MongoDB server
    mongo_run: Callable | None = None  # replaceable in tests
    janitor: Callable | None = None  # housekeeping before each job (stale sandboxes / jobs); None in tests


def _key(run_id: int) -> str:
    return f"benchmark:{run_id}"


def _finish(session: Session, deps: BenchmarkDeps, run: BenchmarkRun, status: str, event: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    session.commit()
    emit(deps, _key(run.id), event, error)


def run_benchmark_job(session: Session, run_id: int, deps: BenchmarkDeps) -> None:
    if deps.janitor is not None:
        try:
            deps.janitor()
        except Exception:
            log.warning("housekeeping before benchmark %s failed", run_id, exc_info=True)
    run = session.get(BenchmarkRun, run_id)
    if run is None or run.status != "queued":
        return
    candidate = session.get(RecommendationCandidate, run.candidate_id)
    analysis = session.get(Analysis, run.analysis_id)
    run.status, run.started_at = "running", datetime.now(UTC)
    session.commit()
    emit(deps, _key(run.id), "benchmarking")

    def cancelled() -> bool:
        session.refresh(run)
        return run.status == "cancelled"

    def progress(stage: str) -> None:
        run.stage = stage
        session.commit()
        emit(deps, _key(run.id), stage)
        if stage == "creating_index":
            session.add(AuditEvent(analysis_id=analysis.id, event_type="sandbox_index_created",
                                   detail={"run_id": run.id, "ddl": candidate.ddl}))
            session.commit()

    try:
        snapshot = analysis.snapshot
        if analysis.engine == "mongodb":
            extras = {"benchmark_runner": deps.mongo_run} if deps.mongo_run is not None else {}
            adapter = get_adapter("mongodb", bench_uri=deps.mongo_bench_uri, **extras)
        else:
            adapter = get_adapter("postgres", benchmark_runner=deps.run)
        vq = adapter.validate_query(snapshot.sql, snapshot.params, max_chars=max(len(snapshot.sql), 1))
        idea = IndexCandidate(
            id=candidate.candidate_key, table=candidate.table_name, columns=[IndexColumn(**c) for c in candidate.columns], ddl=candidate.ddl,
            rationale=list(candidate.rationale or []), include=list(candidate.include or []), predicate=candidate.predicate,
            expression=candidate.expression,
        )
        result = adapter.benchmark(
            deps.bench_dsn, vq, snapshot.params, idea, BenchmarkConfig(**run.config), on_progress=progress, cancelled=cancelled
        )
    except BenchmarkCancelled:
        _finish(session, deps, run, "cancelled", "cancelled")
        return
    except BenchmarkFailed as exc:
        _finish(session, deps, run, "failed", "failed", str(exc))
        return
    except Exception:
        log.exception("benchmark %s crashed", run_id)
        session.rollback()
        _finish(session, deps, run, "failed", "failed", "The benchmark failed due to an internal error.")
        return

    if cancelled():
        _finish(session, deps, run, "cancelled", "cancelled")
        return
    run.result = result.model_dump()
    if awards_validation(result):  # clear, comparable, attributable to the index (see querydoctor.benchmark.reliability)
        candidate.validation = "benchmark_validated"
    session.commit()
    ranking.record_outcome(session, run, candidate, result)  # monitoring only; never affects the benchmark's outcome
    _finish(session, deps, run, "completed", "benchmark_completed")
