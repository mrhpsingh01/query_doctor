"""Workload intelligence: import pg_stat_statements snapshots and answer 'which query should I optimize first?'."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import psycopg

from querydoctor.lab.catalog import build_catalog
from querydoctor.safety import validate_query
from querydoctor.workload import WorkloadQuery, merge_statements, read_statements
from querydoctor.workload.analysis import build_panels, detect_regressions, plan_changes, rank
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, RecommendationCandidate, WorkloadQueryRow, WorkloadSnapshot

RANKED_LIMIT = 100
EVIDENCE_ORDER = ("suggested", "planner_validated", "benchmark_validated")


@dataclass
class WorkloadDeps:
    """What the workload endpoints touch outside the metadata database; tests substitute all of it."""

    target_dsn: str
    connect: Callable[[str], AbstractContextManager] = psycopg.connect
    reader: Callable[..., list] | None = None  # (conn, limit) -> list[StatementRow]; default: pg_stat_statements
    demo_runner: Callable[[str], None] = lambda mode: None  # starts the demo traffic generator in the background


def _query(row: WorkloadQueryRow) -> WorkloadQuery:
    return WorkloadQuery(
        fingerprint=row.fingerprint, normalized_sql=row.normalized_sql, sample_sql=row.sample_sql, calls=row.calls,
        total_ms=row.total_ms, mean_ms=row.mean_ms, stddev_ms=row.stddev_ms, min_ms=row.min_ms, max_ms=row.max_ms,
        rows=row.rows, shared_hit_blocks=row.shared_hit_blocks, shared_read_blocks=row.shared_read_blocks,
        temp_blocks=row.temp_blocks, p95_est_ms=row.p95_est_ms, tables=list(row.tables or []),
    )


def snapshot_dict(s: WorkloadSnapshot) -> dict[str, Any]:
    return {
        "id": s.id, "project_id": s.project_id, "engine": s.engine, "source": s.source, "taken_at": s.taken_at.isoformat(),
        "query_count": s.query_count, "skipped_count": s.skipped_count,
    }


def import_snapshot(session: Session, conn, project_id: int, limit: int = 500, reader=None) -> WorkloadSnapshot:
    queries, skipped = merge_statements((reader or read_statements)(conn, limit))
    snapshot = WorkloadSnapshot(project_id=project_id, query_count=len(queries), skipped_count=skipped)
    session.add(snapshot)
    session.flush()
    session.add_all(
        WorkloadQueryRow(snapshot_id=snapshot.id, **q.model_dump()) for q in queries
    )
    session.commit()
    return snapshot


def _snapshots(session: Session, project_id: int) -> list[WorkloadSnapshot]:
    return list(session.scalars(select(WorkloadSnapshot).where(WorkloadSnapshot.project_id == project_id).order_by(WorkloadSnapshot.id.desc())))


def latest_two(session: Session, project_id: int) -> tuple[WorkloadSnapshot | None, WorkloadSnapshot | None]:
    found = _snapshots(session, project_id)[:2]
    return (found[0] if found else None, found[1] if len(found) > 1 else None)


def _load(session: Session, snapshot: WorkloadSnapshot) -> list[WorkloadQuery]:
    return [_query(r) for r in session.scalars(select(WorkloadQueryRow).where(WorkloadQueryRow.snapshot_id == snapshot.id))]


def _validated_fingerprints(session: Session, project_id: int) -> set[str]:
    stmt = (
        select(Analysis).join(RecommendationCandidate, RecommendationCandidate.analysis_id == Analysis.id)
        .where(Analysis.project_id == project_id, RecommendationCandidate.validation == "benchmark_validated")
    )
    return {a.snapshot.fingerprint for a in session.scalars(stmt).unique()}


def dashboard(session: Session, project_id: int, snapshot_id: int | None = None, compare_id: int | None = None) -> dict[str, Any]:
    """The workload view of one snapshot (default: the latest) compared with another (default: the one before it)."""
    snapshots = _snapshots(session, project_id)
    if not snapshots and snapshot_id is None and compare_id is None:
        return {"snapshot": None, "compare_to": None, "queries": [], "panels": build_panels([], validated_fingerprints=set(), regressions=[]).model_dump(mode="json"), "regressions": []}
    by_id = {s.id: s for s in snapshots}
    if snapshot_id is not None and snapshot_id not in by_id:
        raise LookupError("snapshot not found")
    if compare_id is not None and compare_id not in by_id:
        raise LookupError("comparison snapshot not found")
    current = by_id[snapshot_id] if snapshot_id is not None else (snapshots[0] if snapshots else None)
    if current is None:
        raise LookupError("no workload snapshot yet")
    if compare_id is not None:
        previous = by_id[compare_id]
    else:
        older = [s for s in snapshots if s.id < current.id]
        previous = older[0] if older else None

    queries = _load(session, current)
    regressions = detect_regressions(_load(session, previous), queries) if previous else []
    ranked = rank(queries)
    panels = build_panels(queries, validated_fingerprints=_validated_fingerprints(session, project_id), regressions=regressions)
    return {
        "snapshot": snapshot_dict(current),
        "compare_to": snapshot_dict(previous) if previous else None,
        "queries": [q.model_dump(mode="json") for q in ranked[:RANKED_LIMIT]],
        "panels": panels.model_dump(mode="json"),
        "regressions": [r.model_dump(mode="json") for r in regressions],
    }


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _analysis_dicts(analyses: list[Analysis]) -> list[dict[str, Any]]:
    return [{"id": a.id, "plan_fingerprint": a.plan_fingerprint, "operators": _operators(a)} for a in analyses]


def _operators(analysis: Analysis) -> list[str]:
    """Node types of the stored plan, in tree order, for the before/after plan-change display."""
    raw = analysis.plan_json
    if not raw:
        return []
    root = raw[0].get("Plan") if isinstance(raw, list) and raw else None
    found: list[str] = []

    def walk(node: dict) -> None:
        found.append(node.get("Node Type", "?"))
        for child in node.get("Plans", []):
            walk(child)

    if isinstance(root, dict):
        walk(root)
    return found


def _project_analyses(session: Session, project_id: int) -> list[Analysis]:
    stmt = select(Analysis).where(Analysis.project_id == project_id, Analysis.status == "completed").order_by(Analysis.created_at, Analysis.id)
    return list(session.scalars(stmt).unique())


def _best_validation(analyses: list[Analysis]) -> str | None:
    levels = [c.validation for a in analyses for c in a.candidates if c.validation in EVIDENCE_ORDER and not c.dropped]
    return max(levels, key=EVIDENCE_ORDER.index) if levels else None


def fingerprint_history(session: Session, project_id: int) -> list[dict[str, Any]]:
    groups: dict[str, list[Analysis]] = defaultdict(list)
    for a in _project_analyses(session, project_id):
        groups[a.snapshot.fingerprint].append(a)
    history = []
    for fingerprint, analyses in groups.items():
        times = [a.execution_time_ms for a in analyses if a.execution_time_ms is not None]
        history.append(
            {
                "fingerprint": fingerprint, "normalized_sql": analyses[-1].snapshot.normalized_sql,
                "first_seen": analyses[0].created_at.isoformat(), "last_seen": analyses[-1].created_at.isoformat(),
                "analysis_count": len(analyses), "executed_count": len(times),
                "mean_execution_ms": sum(times) / len(times) if times else None,
                "p95_execution_ms": _percentile(times, 0.95) if times else None,  # measured: from executed analyses
                "plan_change_count": len(plan_changes(_analysis_dicts(analyses))),
                "best_validation": _best_validation(analyses),
            }
        )
    return sorted(history, key=lambda h: h["last_seen"], reverse=True)


def _catalog_sample(fingerprint: str, scale: str) -> dict[str, Any] | None:
    for q in build_catalog(scale):
        if validate_query(q.sql).fingerprint == fingerprint:
            return {"query_id": q.id, "title": q.title, "sql": q.sql, "params": list(q.params)}
    return None


def query_detail(session: Session, project_id: int, fingerprint: str, scale: str = "small") -> dict[str, Any] | None:
    rows = list(
        session.execute(
            select(WorkloadQueryRow, WorkloadSnapshot)
            .join(WorkloadSnapshot, WorkloadSnapshot.id == WorkloadQueryRow.snapshot_id)
            .where(WorkloadSnapshot.project_id == project_id, WorkloadQueryRow.fingerprint == fingerprint)
            .order_by(WorkloadSnapshot.id)
        )
    )
    analyses = [a for a in _project_analyses(session, project_id) if a.snapshot.fingerprint == fingerprint]
    if not rows and not analyses:
        return None
    latest = rows[-1][0] if rows else None
    return {
        "fingerprint": fingerprint,
        "normalized_sql": latest.normalized_sql if latest else analyses[-1].snapshot.normalized_sql,
        "sample_sql": latest.sample_sql if latest else analyses[-1].snapshot.sql,
        "trend": [
            {
                "snapshot_id": s.id, "taken_at": s.taken_at.isoformat(), "calls": r.calls, "total_ms": r.total_ms,
                "mean_ms": r.mean_ms, "p95_est_ms": r.p95_est_ms,
            }
            for r, s in rows
        ],
        "analyses": [
            {
                "id": a.id, "created_at": a.created_at.isoformat(), "plan_fingerprint": a.plan_fingerprint,
                "execution_time_ms": a.execution_time_ms, "validation": _best_validation([a]),
            }
            for a in analyses
        ],
        "plan_changes": [c.model_dump(mode="json") for c in plan_changes(_analysis_dicts(analyses))],
        "sample": _catalog_sample(fingerprint, scale),
    }


def run_demo_traffic(target_dsn: str, scale: str, mode: str) -> dict[str, int]:
    """Generate the lab's demo traffic with the read-only role (no statistics reset, so snapshots stay comparable)."""
    from querydoctor.lab.workload import run_workload

    return run_workload(target_dsn, scale, mode)
