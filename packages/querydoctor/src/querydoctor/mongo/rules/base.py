"""Shared pieces of the MongoDB diagnosis rules."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from querydoctor.diagnosis.models import Finding, Severity
from querydoctor.mongo.query import ValidatedMongoQuery
from querydoctor.plan_parser import ParsedPlan, PlanNode, iter_nodes


class IndexSpec(BaseModel):
    name: str
    keys: list[tuple[str, int]]  # (field, 1 | -1) in index order; other kinds (text, hashed) are kept as given
    unique: bool = False
    partial: bool = False  # a partialFilterExpression restricts which documents it covers
    ttl: bool = False  # expireAfterSeconds


class CollectionInfo(BaseModel):
    count: int = 0
    indexes: list[IndexSpec] = Field(default_factory=list)

    def indexed_prefix(self, field: str) -> bool:
        """Is ``field`` the leading key of some index (so a lookup or filter on it can use an index)?"""
        return field == "_id" or any(i.keys and i.keys[0][0] == field for i in self.indexes)


class MongoThresholds(BaseModel):
    """Tunable rule thresholds (documented in docs/rule-catalog.md)."""

    collscan_min_docs: int = 50_000
    waste_critical: float = 0.95
    scan_amp_min_docs: int = 1_000
    scan_amp_warn: float = 20.0  # documents examined per document returned
    scan_amp_critical: float = 1_000.0
    key_amp_min_keys: int = 1_000
    key_amp_warn: float = 50.0  # index keys examined per document returned
    key_amp_critical: float = 500.0
    sort_min_docs: int = 10_000
    unbounded_min_docs: int = 50_000
    lookup_min_docs: int = 100_000
    unselective_min_keys: int = 10_000
    unselective_share: float = 0.20  # an index matching this share of the collection saves little


class MongoContext(BaseModel):
    plan: ParsedPlan
    query: ValidatedMongoQuery | None = None
    collections: dict[str, CollectionInfo] = Field(default_factory=dict)
    thresholds: MongoThresholds = Field(default_factory=MongoThresholds)

    @property
    def collection(self) -> str | None:
        return self.query.query.collection if self.query else None

    @property
    def executed(self) -> bool:
        return self.plan.executed

    def count_of(self, name: str | None) -> int | None:
        info = self.collections.get(name or "")
        return info.count if info else None

    def nodes(self, *types: str) -> list[PlanNode]:
        return [n for n in iter_nodes(self.plan.root) if not types or n.node_type in types]

    def pipeline(self) -> list[dict[str, Any]]:
        return (self.query.query.pipeline or []) if self.query and self.query.query.kind == "aggregate" else []


def finding(rule_id: str, severity: Severity, title: str, summary: str, evidence: dict[str, Any], node_ids: list[str], confidence: float, actions: list[str]) -> Finding:
    return Finding(rule_id=rule_id, severity=severity, title=title, summary=summary, evidence=evidence, node_ids=node_ids, confidence=confidence, next_actions=actions)
