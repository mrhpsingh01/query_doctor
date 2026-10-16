from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from pymongo.errors import PyMongoError
from querydoctor.mongo.schema import mongo_connect
from querydoctor.mongo.workload import parse_profile
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Connection, Project
from app.services import connections as svc_connections
from app.services import workload as svc

router = APIRouter(prefix="/api/projects/{project_id}", tags=["workload"])

IMPORT_FAILED = (
    "Could not read pg_stat_statements from the target database. Check that it is running and that the read-only "
    "role has the pg_read_all_stats privilege."
)


MONGO_IMPORT_FAILED = (
    "Could not read the MongoDB profiler (system.profile). Check that the server is running, that the profiler is on, "
    "and that the read-only user may read system.profile."
)
Engine = Literal["postgres", "mongodb"]


class DemoRequest(BaseModel):
    mode: Literal["baseline", "regress"]
    engine: Engine = "postgres"


class BatchRequest(BaseModel):
    snapshot_id: int
    top_n: int = Field(default=5, ge=1, le=20)
    approve_benchmarks: Literal[True]


@router.post("/workload/batch", status_code=202)
def batch_workload(project_id: int, body: BatchRequest, request: Request, session: Session = Depends(get_session)):
    from app.models import WorkloadSnapshot
    from app.security.auth import require_project
    from app.services.fix_queue import prepare_batch
    from app.workers.tasks import run_workload_batch
    require_project(session, request, project_id)
    snapshot = session.get(WorkloadSnapshot, body.snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(404, detail="snapshot not found")
    try:
        result = prepare_batch(session, snapshot, body.top_n, request.app.state.settings.scale)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if result["analysis_ids"]:
        try:
            run_workload_batch.delay(result["analysis_ids"])
        except Exception as exc:
            from app.models import Analysis
            for analysis_id in result["analysis_ids"]:
                analysis = session.get(Analysis, analysis_id)
                analysis.status, analysis.error = "failed", "Batch could not be queued. Retry when the worker is available."
            session.commit()
            raise HTTPException(503, detail="Batch worker unavailable") from exc
    return result


class ProfilerImport(BaseModel):
    connection_id: int
    capture_start: datetime
    capture_end: datetime
    coverage: Literal["complete", "sampled", "unknown"]
    engine_version: str = Field(min_length=1, max_length=100)
    entries: list[dict[str, Any]] = Field(max_length=5000)
    schema_fingerprint: str | None = Field(default=None, max_length=64)
    index_fingerprint: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_window(self):
        if self.capture_start.tzinfo is None or self.capture_end.tzinfo is None or self.capture_start >= self.capture_end:
            raise ValueError("capture window must have time zones and capture_start must precede capture_end")
        if len(json.dumps(self.entries, separators=(",", ":"))) > 5_000_000:
            raise ValueError("profiler sample exceeds the 5 MB import limit")
        return self


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, detail="project not found")
    return project


@router.post("/workload/import", status_code=201)
def import_workload(project_id: int, request: Request, engine: Engine = "postgres", connection_id: int | None = None,
                    session: Session = Depends(get_session)):
    _project(session, project_id)
    deps: svc.WorkloadDeps = request.app.state.workload_deps

    row: Connection | None = None
    if connection_id is not None:
        svc_connections.require_enabled(request.app.state.settings)
        row = svc_connections.live_connection(session, connection_id, project_id)
        if row.engine != engine:
            raise HTTPException(404, detail="connection not found")
    if engine == "mongodb":
        if row is not None:
            raise HTTPException(409, detail="Import a bounded profiler sample for a customer MongoDB source.")
        try:
            with (deps.mongo_connect or mongo_connect)(deps.mongo_uri) as db:
                snapshot = svc.import_snapshot(session, db, project_id, reader=deps.mongo_reader, engine="mongodb")
        except PyMongoError as exc:  # messages can quote the connection string
            session.rollback()
            raise HTTPException(502, detail=MONGO_IMPORT_FAILED) from exc
        return svc.snapshot_dict(snapshot)
    try:
        dsn = deps.connection_dsn(session, row) if row is not None else deps.target_dsn
        with deps.connect(dsn) as conn:
            snapshot = svc.import_snapshot(session, conn, project_id, reader=deps.reader,
                                           source_connection_id=row.id if row is not None else None)
    except psycopg.Error as exc:  # the message may quote connection details, so it is not echoed back
        session.rollback()
        raise HTTPException(502, detail=IMPORT_FAILED) from exc
    return svc.snapshot_dict(snapshot)


@router.post("/workload/import-profiler", status_code=201)
def import_profiler(project_id: int, body: ProfilerImport, request: Request, session: Session = Depends(get_session)):
    _project(session, project_id)
    svc_connections.require_enabled(request.app.state.settings)
    row = svc_connections.live_connection(session, body.connection_id, project_id)
    if row.engine != "mongodb":
        raise HTTPException(404, detail="connection not found")
    queries, skipped = parse_profile(body.entries)
    warnings = ["Profiler sample supplied by the customer; capture coverage is declared, not verified.",
                "Individual durations have whole-millisecond precision."]
    if len(body.entries) == 5000:
        warnings.append("Import reached the 5,000-record limit; history may be truncated.")
    snapshot = svc.store_snapshot(
        session, project_id, queries, skipped, engine="mongodb", source_connection_id=row.id,
        capture_method="imported_profiler", capture_start=body.capture_start, capture_end=body.capture_end,
        coverage=body.coverage, engine_version=body.engine_version,
        schema_fingerprint=body.schema_fingerprint, index_fingerprint=body.index_fingerprint, warnings=warnings,
    )
    return svc.snapshot_dict(snapshot)


@router.get("/workload")
def get_workload(
    project_id: int, snapshot: int | None = None, compare: int | None = None, engine: Engine = "postgres",
    connection_id: int | None = None,
    session: Session = Depends(get_session),
):
    _project(session, project_id)
    if connection_id not in (None, 0):
        row = svc_connections.live_connection(session, connection_id, project_id)
        if row.engine != engine:
            raise HTTPException(404, detail="connection not found")
    try:
        return svc.dashboard(session, project_id, snapshot_id=snapshot, compare_id=compare, engine=engine,
                             source_connection_id=connection_id)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/workload/queries/{fingerprint}")
def get_query_detail(project_id: int, fingerprint: str, request: Request, connection_id: int | None = None,
                     session: Session = Depends(get_session)):
    _project(session, project_id)
    if connection_id not in (None, 0):
        svc_connections.live_connection(session, connection_id, project_id)
    detail = svc.query_detail(session, project_id, fingerprint, scale=request.app.state.settings.scale,
                              source_connection_id=connection_id)
    if detail is None:
        raise HTTPException(404, detail="no workload or analysis data for this query")
    return detail


@router.post("/workload/demo", status_code=202)
def run_demo(project_id: int, body: DemoRequest, request: Request, session: Session = Depends(get_session)):
    _project(session, project_id)
    deps = request.app.state.workload_deps
    (deps.mongo_demo_runner if body.engine == "mongodb" else deps.demo_runner)(body.mode)
    return {"status": "queued", "mode": body.mode, "engine": body.engine}


@router.get("/fingerprints")
def get_fingerprints(project_id: int, session: Session = Depends(get_session)):
    _project(session, project_id)
    return svc.fingerprint_history(session, project_id)
