from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.sse import sse_stream
from app.deps import get_session
from app.models import Analysis, AuditEvent, BenchmarkRun, RecommendationCandidate
from app.schemas.benchmark import BenchmarkOut, BenchmarkRequest
from app.services.benchmarks import TERMINAL
from app.services.pipeline import emit

router = APIRouter(tags=["benchmarks"])
BENCHMARK_TERMINAL_EVENTS = {"benchmark_completed", "failed", "cancelled"}
ACTIVE = ("queued", "running")


def _active_run(session: Session, candidate_id: int) -> BenchmarkRun | None:
    return session.scalar(
        select(BenchmarkRun).where(BenchmarkRun.candidate_id == candidate_id, BenchmarkRun.status.in_(ACTIVE))
    )


@router.post("/api/analyses/{analysis_id}/candidates/{candidate_id}/benchmark", status_code=202)
def approve_benchmark(
    analysis_id: int,
    candidate_id: int,
    request: Request,
    body: BenchmarkRequest | None = None,
    session: Session = Depends(get_session),
):
    """Approve one candidate for an isolated benchmark. Idempotent while a run for it is queued or running."""
    from fastapi.responses import JSONResponse

    analysis = session.get(Analysis, analysis_id)
    candidate = session.get(RecommendationCandidate, candidate_id)
    if analysis is None or candidate is None or candidate.analysis_id != analysis.id:
        raise HTTPException(404, detail="analysis or index idea not found")
    if analysis.status != "completed":
        raise HTTPException(409, detail="the analysis has not finished yet")
    if candidate.dropped:
        raise HTTPException(409, detail="this index idea was ruled out and cannot be benchmarked")

    existing = _active_run(session, candidate.id)
    if existing is not None:
        return JSONResponse({"id": existing.id, "status": existing.status}, status_code=200)

    run = BenchmarkRun(
        analysis_id=analysis.id, candidate_id=candidate.id, status="queued", config=(body or BenchmarkRequest()).model_dump()
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError:  # a racing request created the active run first
        session.rollback()
        existing = _active_run(session, candidate.id)
        return JSONResponse({"id": existing.id, "status": existing.status}, status_code=200)
    session.add(AuditEvent(analysis_id=analysis.id, event_type="benchmark_approved",
                           detail={"run_id": run.id, "candidate_id": candidate.id, "ddl": candidate.ddl}))
    session.commit()

    request.app.state.enqueue_benchmark(run.id)
    session.refresh(run)
    return {"id": run.id, "status": run.status}


@router.get("/api/benchmarks/{run_id}", response_model=BenchmarkOut)
def get_benchmark(run_id: int, session: Session = Depends(get_session)):
    run = session.get(BenchmarkRun, run_id)
    if run is None:
        raise HTTPException(404, detail="benchmark not found")
    return run


@router.post("/api/benchmarks/{run_id}/cancel")
def cancel_benchmark(run_id: int, request: Request, session: Session = Depends(get_session)):
    run = session.get(BenchmarkRun, run_id)
    if run is None:
        raise HTTPException(404, detail="benchmark not found")
    if run.status in TERMINAL:
        raise HTTPException(409, detail=f"benchmark already {run.status}")
    run.status = "cancelled"  # the running job polls this and tears its sandbox down
    run.finished_at = datetime.now(UTC)
    session.add(AuditEvent(analysis_id=run.analysis_id, event_type="benchmark_cancelled", detail={"run_id": run.id}))
    session.commit()
    emit(request.app.state.benchmark_deps, f"benchmark:{run.id}", "cancelled")
    return {"id": run.id, "status": run.status}


@router.get("/api/benchmarks/{run_id}/events")
def benchmark_events(
    run_id: int,
    request: Request,
    session: Session = Depends(get_session),
    last_event_id: str | None = Header(default=None),
):
    if session.get(BenchmarkRun, run_id) is None:
        raise HTTPException(404, detail="benchmark not found")
    sessionmaker = request.app.state.sessionmaker

    def terminal_status() -> str | None:
        with sessionmaker() as s:
            status = s.get(BenchmarkRun, run_id).status
            return {"completed": "benchmark_completed", "failed": "failed", "cancelled": "cancelled"}.get(status)

    return sse_stream(request.app.state.bus, f"benchmark:{run_id}", BENCHMARK_TERMINAL_EVENTS, last_event_id, terminal_status)
