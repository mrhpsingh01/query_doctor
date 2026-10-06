from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkRequest(BaseModel):
    runs: int = Field(default=7, ge=5, le=10)


class BenchmarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_id: int
    candidate_id: int
    status: str
    stage: str | None
    config: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
