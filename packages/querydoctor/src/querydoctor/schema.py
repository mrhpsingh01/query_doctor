"""Database-schema snapshot shared by diagnosis, candidate generation and the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IndexInfo(BaseModel):
    name: str
    table: str
    columns: list[str]
    include: list[str] = Field(default_factory=list)
    unique: bool = False
    predicate: str | None = None
    size_bytes: int | None = None


class ColumnStats(BaseModel):
    """What pg_stats knows about a column (only collected with ``collect_schema(extras=True)``)."""

    n_distinct_fraction: float  # distinct values / rows, in (0, 1]; 1.0 means unique
    null_frac: float
    correlation: float  # physical-order correlation in [-1, 1]; |1| means rows are stored sorted by this column


class TableInfo(BaseModel):
    name: str
    row_estimate: int
    columns: dict[str, str] = Field(default_factory=dict)  # column -> type
    size_bytes: int | None = None  # relation size; set with extras
    write_ratio: float | None = None  # writes / (writes + tuples read) since statistics reset; set with extras
    column_stats: dict[str, ColumnStats] = Field(default_factory=dict)  # set with extras


class SchemaSnapshot(BaseModel):
    tables: dict[str, TableInfo] = Field(default_factory=dict)
    indexes: dict[str, list[IndexInfo]] = Field(default_factory=dict)  # table -> indexes

    def row_estimate(self, table: str | None) -> int | None:
        info = self.tables.get(table) if table else None
        return info.row_estimate if info else None
