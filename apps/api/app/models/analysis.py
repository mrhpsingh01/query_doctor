from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedMixin
from app.models.benchmark import BenchmarkRun
from app.models.ranking import ModelPrediction


class QuerySnapshot(CreatedMixin, Base):
    __tablename__ = "query_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    engine: Mapped[str] = mapped_column(String(20), default="postgres")  # postgres | mongodb
    sql: Mapped[str] = mapped_column(Text)  # the canonical query text (a JSON spec for MongoDB)
    normalized_sql: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[list[Any]] = mapped_column(JSON, default=list)


class Analysis(CreatedMixin, Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("query_snapshots.id", ondelete="CASCADE"))
    engine: Mapped[str] = mapped_column(String(20), default="postgres")
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"), default=None)  # None: the sample database
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed|cancelled
    stage: Mapped[str | None] = mapped_column(String(40), default=None)
    execute: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    backend_pid: Mapped[int | None] = mapped_column(Integer, default=None)  # target-DB backend of the running statement
    error: Mapped[str | None] = mapped_column(Text, default=None)
    plan_json: Mapped[Any | None] = mapped_column(JSON, default=None)  # raw EXPLAIN JSON; normalized on read
    schema_json: Mapped[dict | None] = mapped_column(JSON, default=None)  # SchemaSnapshot at analysis time
    plan_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    planning_time_ms: Mapped[float | None] = mapped_column(Float, default=None)
    execution_time_ms: Mapped[float | None] = mapped_column(Float, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    snapshot: Mapped[QuerySnapshot] = relationship(lazy="joined")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="Finding.id"
    )
    candidates: Mapped[list[RecommendationCandidate]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="RecommendationCandidate.id"
    )
    benchmark_runs: Mapped[list[BenchmarkRun]] = relationship(order_by="BenchmarkRun.id", viewonly=True)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    next_actions: Mapped[list[str]] = mapped_column(JSON, default=list)

    analysis: Mapped[Analysis] = relationship(back_populates="findings")


class RecommendationCandidate(Base):
    __tablename__ = "recommendation_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    candidate_key: Mapped[str] = mapped_column(String(64))  # stable id derived from the DDL
    table_name: Mapped[str] = mapped_column(String(200))
    ddl: Mapped[str] = mapped_column(Text)
    columns: Mapped[list[dict]] = mapped_column(JSON, default=list)
    include: Mapped[list[str]] = mapped_column(JSON, default=list)
    predicate: Mapped[str | None] = mapped_column(Text, default=None)
    expression: Mapped[str | None] = mapped_column(Text, default=None)
    rationale: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation: Mapped[str] = mapped_column(String(24), default="suggested")
    planner_score: Mapped[float | None] = mapped_column(Float, default=None)
    comparison: Mapped[dict | None] = mapped_column(JSON, default=None)
    dropped: Mapped[bool] = mapped_column(Boolean, default=False)
    drop_reason: Mapped[str | None] = mapped_column(Text, default=None)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    analysis: Mapped[Analysis] = relationship(back_populates="candidates")
    prediction: Mapped[ModelPrediction | None] = relationship(uselist=False, cascade="all, delete-orphan", lazy="selectin")
