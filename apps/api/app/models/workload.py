from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class WorkloadSnapshot(Base):
    """One import of the database's query statistics (pg_stat_statements counters are cumulative, so snapshots are compared)."""

    __tablename__ = "workload_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    engine: Mapped[str] = mapped_column(String(20), default="postgres")
    source: Mapped[str] = mapped_column(String(40), default="pg_stat_statements")
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    query_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)


class WorkloadQueryRow(Base):
    __tablename__ = "workload_queries"
    __table_args__ = (UniqueConstraint("snapshot_id", "fingerprint", name="uq_workload_snapshot_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("workload_snapshots.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    normalized_sql: Mapped[str] = mapped_column(Text)
    sample_sql: Mapped[str] = mapped_column(Text)  # the heaviest member statement: what New analysis is pre-filled with
    tables: Mapped[list[str]] = mapped_column(JSON, default=list)
    calls: Mapped[int] = mapped_column(BigInteger)
    total_ms: Mapped[float] = mapped_column(Float)
    mean_ms: Mapped[float] = mapped_column(Float)
    stddev_ms: Mapped[float] = mapped_column(Float)
    min_ms: Mapped[float] = mapped_column(Float)
    max_ms: Mapped[float] = mapped_column(Float)
    rows: Mapped[int] = mapped_column(BigInteger)
    shared_hit_blocks: Mapped[int] = mapped_column(BigInteger)
    shared_read_blocks: Mapped[int] = mapped_column(BigInteger)
    temp_blocks: Mapped[int] = mapped_column(BigInteger)
    p95_est_ms: Mapped[float] = mapped_column(Float)  # an ESTIMATE (mean + 1.645 sigma), never a measured p95
