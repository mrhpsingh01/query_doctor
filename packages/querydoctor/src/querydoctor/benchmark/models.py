from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunStats(BaseModel):
    n: int
    median_ms: float
    p95_ms: float
    mean_ms: float
    stdev_ms: float
    cv: float  # coefficient of variation: stdev / mean
    min_ms: float
    max_ms: float


class BenchmarkConfig(BaseModel):
    runs: int = Field(default=7, ge=5, le=10)  # measured runs per side
    warmups: int = Field(default=1, ge=0, le=3)
    cv_warn: float = 0.25
    timeout_s: int = 120  # whole benchmark


class RunSample(BaseModel):
    """One measured execution."""

    execution_ms: float
    planning_ms: float | None = None
    rows: int | None = None
    shared_hit_blocks: int | None = None
    shared_read_blocks: int | None = None
    temp_blocks: int | None = None


class BenchmarkResult(BaseModel):
    before: RunStats
    after: RunStats
    before_samples: list[RunSample]
    after_samples: list[RunSample]
    improvement_pct: float
    index_size_bytes: int
    index_build_ms: float
    plan_fingerprint_before: str
    plan_fingerprint_after: str
    checksum_match: bool
    index_used: bool  # the index that was created appears in the final (indexed) plan
    warnings: list[str]
    pg_version: str
    settings: dict[str, Any]
    dataset_snapshot: str
    ddl: str
