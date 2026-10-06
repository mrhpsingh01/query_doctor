from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from querydoctor.db import ParamMismatch
from querydoctor.db.explain import check_params
from querydoctor.safety import ValidationError, validate_query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Analysis, AuditEvent, Project, QuerySnapshot
from app.schemas.analysis import AnalysisAccepted, AnalysisCreate, AnalysisOut, AnalysisSummary
from app.services.pipeline import TERMINAL_STATUSES, emit

router = APIRouter(prefix="/api/analyses", tags=["analyses"])
project_router = APIRouter(prefix="/api/projects", tags=["projects"])

SSE_KEEPALIVE_MS = 15_000
SSE_MAX_SECONDS = 600


def _422(code: str, message: str) -> HTTPException:
    return HTTPException(422, detail={"code": code, "message": message})


def _get_or_404(session: Session, analysis_id: int) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(404, detail="analysis not found")
    return analysis


@router.post("", response_model=AnalysisAccepted, status_code=202)
def create_analysis(body: AnalysisCreate, request: Request, session: Session = Depends(get_session)):
    state = request.app.state
    if session.get(Project, body.project_id) is None:
        raise HTTPException(404, detail="project not found")
    try:
        vq = validate_query(body.sql, max_chars=state.settings.max_sql_chars)
        check_params(body.params)
    except ValidationError as exc:
        raise _422(exc.code, exc.message) from exc
    except ParamMismatch as exc:
        raise _422("param_mismatch", str(exc)) from exc
    if len(body.params) != vq.param_count:
        raise _422("param_mismatch", f"query uses {vq.param_count} parameter(s) but {len(body.params)} were supplied")

    snapshot = QuerySnapshot(
        project_id=body.project_id, sql=vq.sql, normalized_sql=vq.normalized_sql, fingerprint=vq.fingerprint,
        params=list(body.params),
    )
    analysis = Analysis(project_id=body.project_id, snapshot=snapshot, status="queued", execute=body.execute)
    session.add(analysis)
    session.flush()
    session.add(AuditEvent(analysis_id=analysis.id, event_type="analysis_started", detail={"fingerprint": vq.fingerprint}))
    if body.execute:
        session.add(AuditEvent(analysis_id=analysis.id, event_type="execute_explain_requested", detail={}))
    session.commit()

    state.enqueue(analysis.id)
    session.refresh(analysis)
    return AnalysisAccepted(id=analysis.id, status=analysis.status)


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: int, session: Session = Depends(get_session)):
    return AnalysisOut.from_model(_get_or_404(session, analysis_id))


@router.post("/{analysis_id}/cancel")
def cancel_analysis(analysis_id: int, request: Request, session: Session = Depends(get_session)):
    deps = request.app.state.pipeline_deps
    analysis = _get_or_404(session, analysis_id)
    if analysis.status in TERMINAL_STATUSES:
        raise HTTPException(409, detail=f"analysis already {analysis.status}")
    pid = analysis.backend_pid
    analysis.cancel_requested = True
    analysis.status = "cancelled"
    analysis.finished_at = datetime.now(UTC)
    session.add(AuditEvent(analysis_id=analysis.id, event_type="job_cancelled", detail={}))
    session.commit()
    if pid:
        try:
            deps.cancel_backend(deps.target_dsn, pid)
        except Exception:  # the statement timeout bounds it anyway
            pass
    emit(deps, f"analysis:{analysis.id}", "cancelled")
    return {"id": analysis.id, "status": analysis.status}


@router.get("/{analysis_id}/events")
def stream_events(
    analysis_id: int,
    request: Request,
    session: Session = Depends(get_session),
    last_event_id: str | None = Header(default=None),
):
    _get_or_404(session, analysis_id)
    bus = request.app.state.bus
    sessionmaker = request.app.state.sessionmaker
    key = f"analysis:{analysis_id}"

    def terminal_status() -> str | None:
        with sessionmaker() as s:
            status = s.get(Analysis, analysis_id).status
            return status if status in TERMINAL_STATUSES else None

    def generate():
        after = last_event_id
        deadline = time.monotonic() + SSE_MAX_SECONDS
        while time.monotonic() < deadline:
            batch = bus.read(key, after, block_ms=SSE_KEEPALIVE_MS)
            if not batch:
                final = terminal_status()
                if final:  # events expired or were never published; close with the DB's truth
                    yield f"event: progress\ndata: {json.dumps({'type': final})}\n\n"
                    return
                yield ": keepalive\n\n"
                continue
            for event_id, event in batch:
                after = event_id
                yield f"id: {event_id}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                if event.get("type") in TERMINAL_STATUSES:
                    return

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@project_router.get("/{project_id}/analyses", response_model=list[AnalysisSummary])
def list_project_analyses(project_id: int, session: Session = Depends(get_session)):
    if session.get(Project, project_id) is None:
        raise HTTPException(404, detail="project not found")
    rows = session.scalars(
        select(Analysis).where(Analysis.project_id == project_id).order_by(Analysis.id.desc()).limit(100)
    ).all()
    return [AnalysisSummary.from_model(a) for a in rows]
