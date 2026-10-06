"""The MongoDB query model: a JSON spec of a read-only ``find`` or ``aggregate``."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MongoQuery(BaseModel):
    kind: Literal["find", "aggregate"]
    collection: str
    filter: dict[str, Any] = Field(default_factory=dict)
    sort: dict[str, int] | None = None
    projection: dict[str, Any] | None = None
    limit: int | None = None
    skip: int | None = None
    pipeline: list[dict[str, Any]] | None = None


class ValidatedMongoQuery(BaseModel):
    """A query that passed validation. Mirrors the fields of the PostgreSQL ``ValidatedQuery`` the pipeline relies on."""

    query: MongoQuery  # parameters substituted: this is what runs
    canonical_json: str  # the text that is stored: placeholders kept, top-level keys sorted, no stray whitespace
    fingerprint: str  # of the shape: the same for different literals
    shape: str  # the query with every literal replaced by "?"
    collections: list[str]  # the queried collection first, then $lookup targets
    param_count: int

    @property
    def sql(self) -> str:
        return self.canonical_json

    @property
    def normalized_sql(self) -> str:
        return self.shape

    @property
    def tables(self) -> list[str]:
        return self.collections

    @property
    def has_select_star(self) -> bool:
        return False
