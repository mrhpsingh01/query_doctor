from __future__ import annotations

from pydantic import BaseModel


class StatementRow(BaseModel):
    """One pg_stat_statements row (times in milliseconds)."""

    query: str
    calls: int
    total_ms: float
    mean_ms: float
    stddev_ms: float
    min_ms: float
    max_ms: float
    rows: int
    shared_hit_blocks: int
    shared_read_blocks: int
    temp_blocks: int


class WorkloadQuery(BaseModel):
    """One query shape (fingerprint) with its statistics merged across statements."""

    fingerprint: str
    normalized_sql: str
    sample_sql: str
    calls: int
    total_ms: float
    mean_ms: float
    stddev_ms: float
    min_ms: float
    max_ms: float
    rows: int
    shared_hit_blocks: int
    shared_read_blocks: int
    temp_blocks: int
    p95_est_ms: float  # an ESTIMATE (mean + 1.645 sigma): pg_stat_statements keeps no percentiles
    tables: list[str] = []
