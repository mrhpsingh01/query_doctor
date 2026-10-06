"""The analysis pipeline: validate -> collect schema -> plan -> diagnose -> (candidates) -> done.

Plain functions over an injected ``PipelineDeps`` so it is testable without Celery, Redis or Postgres. Every exit
path leaves the analysis in a terminal state (completed / failed / cancelled) and emits a matching event.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from querydoctor.candidates import score_candidates
from querydoctor.db import DbError, ExplainTimeout, cancel_backend, collect_schema, explain, guarded_connection
from querydoctor.diagnosis import Thresholds
from sqlalchemy.orm import Session

from app.models import Analysis
from app.models import Finding as FindingRow
from app.services.adapters import adapter_for
from app.services.events import EventBus

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
STAGES = ["validating_query", "collecting_schema", "generating_plan", "diagnosing", "testing_candidates"]


@dataclass
class PipelineDeps:
    target_dsn: str
    bus: EventBus
    connect: Callable = guarded_connection
    explain: Callable = explain
    collect_schema: Callable = collect_schema
    cancel_backend: Callable = cancel_backend
    max_plan_bytes: int = 5_000_000
    thresholds: Thresholds = field(default_factory=Thresholds)
    scorer: Callable = score_candidates  # HypoPG scoring; replaceable in tests
    candidate_stage: Callable | None = field(default=None)  # defaults to the real candidate stage
    connection_dsn: Callable | None = None  # (session, analysis) -> DSN of the analysis's custom connection (guarded)
    hypopg_available: Callable = field(default=lambda conn: _hypopg_available(conn))  # is HypoPG installed on the target?
    predictor: object | None = None  # querydoctor.ranking.Predictor (or a test double); None means heuristic ordering only
    extra: dict = field(default_factory=dict)


def database_error_message(exc: psycopg.Error) -> str:
    """A person-readable reason for a database failure that names no host, object or value."""
    if isinstance(exc, psycopg.errors.LockNotAvailable):
        return "A table this query needs is locked by another session, so the analysis gave up waiting. Try again when it is free."
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return "The database took too long to answer, so the analysis was stopped. The query may be too heavy to analyse."
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return "The database user does not have permission to read something this query needs."
    if isinstance(exc, psycopg.OperationalError):
        return "The connection to the database failed or was lost while the analysis was running. Check that it is up and try again."
    return "The database reported an error that stopped the analysis."


def _hypopg_available(conn) -> bool:
    try:
        return conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'hypopg'").fetchone() is not None
    finally:
        conn.rollback()


def dsn_for(session: Session, deps: PipelineDeps, analysis: Analysis) -> str:
    """The sample target, or (flag permitting) the analysis's registered custom connection."""
    if analysis.connection_id is None:
        return deps.target_dsn
    if deps.connection_dsn is None:
        raise ValueError("custom connections are not configured")
    return deps.connection_dsn(session, analysis)


class _Stop(Exception):
    """Internal control flow: the analysis was cancelled."""


def emit(deps: PipelineDeps, key: str, event_type: str, message: str | None = None, **data) -> None:
    """Publish a progress event; a dead bus must never break an analysis."""
    try:
        deps.bus.publish(key, {"type": event_type, "message": message, **data})
    except Exception:
        log.warning("could not publish %s event for %s", event_type, key, exc_info=True)


def _checkpoint(session: Session, analysis: Analysis) -> None:
    session.refresh(analysis)
    if analysis.cancel_requested or analysis.status == "cancelled":
        raise _Stop


def _stage(session: Session, deps: PipelineDeps, analysis: Analysis, stage: str, message: str | None = None) -> None:
    _checkpoint(session, analysis)
    analysis.stage = stage
    session.commit()
    emit(deps, f"analysis:{analysis.id}", stage, message)


def _finish(session: Session, deps: PipelineDeps, analysis: Analysis, status: str, error: str | None = None) -> None:
    already_final = analysis.status == status  # e.g. the API already marked it cancelled
    analysis.status = status
    analysis.error = error
    analysis.backend_pid = None
    analysis.finished_at = datetime.now(UTC)
    session.commit()
    if not (already_final and status == "cancelled" and _cancel_event_exists(deps, analysis)):
        emit(deps, f"analysis:{analysis.id}", status, error)


def _cancel_event_exists(deps: PipelineDeps, analysis: Analysis) -> bool:
    try:
        events = deps.bus.read(f"analysis:{analysis.id}", None, 0)
    except Exception:
        return False
    return any(e.get("type") == "cancelled" for _id, e in events)


def run_analysis_pipeline(session: Session, analysis_id: int, deps: PipelineDeps) -> None:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None or analysis.status in TERMINAL_STATUSES:
        return
    analysis.status = "running"
    analysis.started_at = datetime.now(UTC)
    session.commit()
    try:
        _run(session, deps, analysis)
        _checkpoint(session, analysis)
        _finish(session, deps, analysis, "completed")
    except _Stop:
        _finish(session, deps, analysis, "cancelled")
    except ExplainTimeout as exc:
        if _cancelled(session, analysis):
            _finish(session, deps, analysis, "cancelled")
        else:
            _finish(session, deps, analysis, "failed", f"The query timed out before a plan could be collected ({exc}).")
    except DbError as exc:
        _finish(session, deps, analysis, "failed", str(exc))
    except ValueError as exc:
        _finish(session, deps, analysis, "failed", f"The analysis input or plan was not usable: {exc}")
    except psycopg.Error as exc:
        session.rollback()
        _finish(session, deps, analysis, "failed", database_error_message(exc))
    except Exception:
        log.exception("analysis %s crashed", analysis_id)
        session.rollback()
        _finish(session, deps, analysis, "failed", "Analysis failed due to an internal error.")


def _cancelled(session: Session, analysis: Analysis) -> bool:
    session.refresh(analysis)
    return analysis.cancel_requested or analysis.status == "cancelled"


def _run(session: Session, deps: PipelineDeps, analysis: Analysis) -> None:
    snapshot = analysis.snapshot
    _stage(session, deps, analysis, "validating_query")
    adapter = adapter_for(deps, analysis.engine)
    vq = adapter.validate_query(snapshot.sql, snapshot.params, max_chars=max(len(snapshot.sql), 1))

    _stage(session, deps, analysis, "collecting_schema")
    with deps.connect(dsn_for(session, deps, analysis)) as conn:
        analysis.backend_pid = conn.info.backend_pid
        session.commit()
        schema = adapter.collect_schema(conn, vq)
        analysis.schema_json = schema.model_dump()
        session.commit()

        _stage(session, deps, analysis, "generating_plan")
        raw = adapter.explain(conn, vq, snapshot.params, execute=analysis.execute)

    if len(json.dumps(raw)) > deps.max_plan_bytes:
        raise ValueError(f"execution plan is too large to analyse (limit {deps.max_plan_bytes:,} bytes)")
    plan = adapter.normalize_plan(raw)
    analysis.plan_json = raw
    analysis.plan_fingerprint = plan.fingerprint
    analysis.planning_time_ms = plan.planning_time_ms
    analysis.execution_time_ms = plan.execution_time_ms
    session.commit()

    _stage(session, deps, analysis, "diagnosing")
    findings = adapter.diagnose(plan, schema, vq, deps.thresholds)
    analysis.findings = [FindingRow(**f.model_dump()) for f in findings]
    session.commit()

    _stage(session, deps, analysis, "testing_candidates")
    from app.services.candidates import run_candidate_stage

    (deps.candidate_stage or run_candidate_stage)(session, deps, analysis, plan, schema, vq, findings)
