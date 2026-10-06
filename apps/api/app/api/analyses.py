from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from querydoctor.benchmark import render_report, render_report_html
from querydoctor.db import ParamMismatch
from querydoctor.db.explain import check_params
from querydoctor.safety import ValidationError, validate_query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sse import sse_stream
from app.deps import get_session
from app.models import Analysis, AuditEvent, Project, QuerySnapshot
from app.schemas.analysis import AnalysisAccepted, AnalysisCreate, AnalysisOut, AnalysisSummary
from app.services.pipeline import TERMINAL_STATUSES, emit

router = APIRouter(prefix="/api/analyses", tags=["analyses"])
project_router = APIRouter(prefix="/api/projects", tags=["projects"])



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
    sessionmaker = request.app.state.sessionmaker

    def terminal_status() -> str | None:
        with sessionmaker() as s:
            status = s.get(Analysis, analysis_id).status
            return status if status in TERMINAL_STATUSES else None

    return sse_stream(request.app.state.bus, f"analysis:{analysis_id}", TERMINAL_STATUSES, last_event_id, terminal_status)


REPORT_TYPES = {
    "md": ("text/markdown; charset=utf-8", render_report),
    "html": ("text/html; charset=utf-8", render_report_html),
}


@router.get("/{analysis_id}/report")
def export_report(analysis_id: int, format: str = "md", session: Session = Depends(get_session)):
    if format not in REPORT_TYPES:
        raise _422("unsupported_format", "format must be 'md' or 'html'")
    analysis = _get_or_404(session, analysis_id)
    media_type, renderer = REPORT_TYPES[format]
    body = renderer(AnalysisOut.from_model(analysis).model_dump(mode="json"))
    session.add(AuditEvent(analysis_id=analysis.id, event_type="report_exported", detail={"format": format}))
    session.commit()
    return Response(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="querydoctor-analysis-{analysis.id}.{format}"'},
    )


@project_router.get("/{project_id}/analyses", response_model=list[AnalysisSummary])
def list_project_analyses(project_id: int, session: Session = Depends(get_session)):
    if session.get(Project, project_id) is None:
        raise HTTPException(404, detail="project not found")
    rows = session.scalars(
        select(Analysis).where(Analysis.project_id == project_id).order_by(Analysis.id.desc()).limit(100)
    ).all()
    return [AnalysisSummary.from_model(a) for a in rows]
