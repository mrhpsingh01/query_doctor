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


class TableInfo(BaseModel):
    name: str
    row_estimate: int
    columns: dict[str, str] = Field(default_factory=dict)  # column -> type


class SchemaSnapshot(BaseModel):
    tables: dict[str, TableInfo] = Field(default_factory=dict)
    indexes: dict[str, list[IndexInfo]] = Field(default_factory=dict)  # table -> indexes

    def row_estimate(self, table: str | None) -> int | None:
        info = self.tables.get(table) if table else None
        return info.row_estimate if info else None
