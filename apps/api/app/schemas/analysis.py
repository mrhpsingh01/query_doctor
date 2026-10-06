from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from querydoctor.plan_parser import parse_explain

from app.models import Analysis
from app.schemas.benchmark import BenchmarkOut


class AnalysisCreate(BaseModel):
    sql: str
    params: list[Any] = Field(default_factory=list)
    execute: bool = False
    project_id: int


class AnalysisAccepted(BaseModel):
    id: int
    status: str


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: str
    severity: str
    title: str
    summary: str
    evidence: dict[str, Any]
    node_ids: list[str]
    confidence: float
    next_actions: list[str]


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_key: str
    table_name: str
    ddl: str
    columns: list[dict]
    include: list[str]
    predicate: str | None
    expression: str | None
    rationale: list[str]
    source_rule_ids: list[str]
    validation: str
    planner_score: float | None
    comparison: dict | None
    dropped: bool
    drop_reason: str | None
    rank: int


class AnalysisOut(BaseModel):
    id: int
    project_id: int
    status: str
    stage: str | None
    execute: bool
    error: str | None
    sql: str
    normalized_sql: str
    fingerprint: str
    params: list[Any]
    plan: dict | None
    planning_time_ms: float | None
    execution_time_ms: float | None
    findings: list[FindingOut]
    candidates: list[CandidateOut]
    benchmarks: list[BenchmarkOut] = []
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_model(cls, a: Analysis) -> AnalysisOut:
        plan = None
        if a.plan_json is not None:
            try:
                plan = parse_explain(a.plan_json).model_dump()
            except ValueError:
                plan = None
        return cls(
            id=a.id, project_id=a.project_id, status=a.status, stage=a.stage, execute=a.execute, error=a.error,
            sql=a.snapshot.sql, normalized_sql=a.snapshot.normalized_sql, fingerprint=a.snapshot.fingerprint,
            params=a.snapshot.params, plan=plan, planning_time_ms=a.planning_time_ms,
            execution_time_ms=a.execution_time_ms,
            findings=[FindingOut.model_validate(f) for f in a.findings],
            candidates=[CandidateOut.model_validate(c) for c in a.candidates],
            benchmarks=[BenchmarkOut.model_validate(r) for r in a.benchmark_runs],
            created_at=a.created_at, started_at=a.started_at, finished_at=a.finished_at,
        )


class AnalysisSummary(BaseModel):
    id: int
    status: str
    execute: bool
    sql: str
    fingerprint: str
    execution_time_ms: float | None
    finding_count: int
    critical_count: int
    best_validation: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, a: Analysis) -> AnalysisSummary:
        order = ["benchmark_validated", "planner_validated", "suggested"]
        states = {c.validation for c in a.candidates if not c.dropped}
        return cls(
            id=a.id, status=a.status, execute=a.execute, sql=a.snapshot.sql, fingerprint=a.snapshot.fingerprint,
            execution_time_ms=a.execution_time_ms, finding_count=len(a.findings),
            critical_count=sum(1 for f in a.findings if f.severity == "critical"),
            best_validation=next((s for s in order if s in states), None), created_at=a.created_at,
        )
