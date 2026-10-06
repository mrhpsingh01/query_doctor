from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

Validation = Literal["suggested", "planner_validated", "benchmark_validated"]


class IndexColumn(BaseModel):
    name: str
    desc: bool = False


class IndexCandidate(BaseModel):
    id: str  # stable: derived from the DDL
    table: str
    columns: list[IndexColumn] = Field(default_factory=list)  # key columns (empty for a pure expression index)
    include: list[str] = Field(default_factory=list)
    predicate: str | None = None  # partial-index WHERE clause
    expression: str | None = None  # expression index, e.g. "lower(email)"
    ddl: str
    rationale: list[str]
    source_rule_ids: list[str] = Field(default_factory=list)
    validation: Validation = "suggested"

    @staticmethod
    def make_id(ddl: str) -> str:
        return hashlib.sha1(ddl.encode()).hexdigest()[:12]
