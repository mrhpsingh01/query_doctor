from __future__ import annotations

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from pymongo.errors import PyMongoError
from querydoctor.mongo.schema import mongo_connect
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Project
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


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, detail="project not found")
    return project


@router.post("/workload/import", status_code=201)
def import_workload(project_id: int, request: Request, engine: Engine = "postgres", session: Session = Depends(get_session)):
    _project(session, project_id)
    deps: svc.WorkloadDeps = request.app.state.workload_deps
    if engine == "mongodb":
        try:
            with (deps.mongo_connect or mongo_connect)(deps.mongo_uri) as db:
                snapshot = svc.import_snapshot(session, db, project_id, reader=deps.mongo_reader, engine="mongodb")
        except PyMongoError as exc:  # messages can quote the connection string
            session.rollback()
            raise HTTPException(502, detail=MONGO_IMPORT_FAILED) from exc
        return svc.snapshot_dict(snapshot)
    try:
        with deps.connect(deps.target_dsn) as conn:
            snapshot = svc.import_snapshot(session, conn, project_id, reader=deps.reader)
    except psycopg.Error as exc:  # the message may quote connection details, so it is not echoed back
        session.rollback()
        raise HTTPException(502, detail=IMPORT_FAILED) from exc
    return svc.snapshot_dict(snapshot)


@router.get("/workload")
def get_workload(
    project_id: int, snapshot: int | None = None, compare: int | None = None, engine: Engine = "postgres",
    session: Session = Depends(get_session),
):
    _project(session, project_id)
    try:
        return svc.dashboard(session, project_id, snapshot_id=snapshot, compare_id=compare, engine=engine)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/workload/queries/{fingerprint}")
def get_query_detail(project_id: int, fingerprint: str, request: Request, session: Session = Depends(get_session)):
    _project(session, project_id)
    detail = svc.query_detail(session, project_id, fingerprint, scale=request.app.state.settings.scale)
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
