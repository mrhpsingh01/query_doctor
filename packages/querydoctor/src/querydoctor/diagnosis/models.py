from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from querydoctor.plan_parser import ParsedPlan, PlanNode
from querydoctor.plan_parser.metrics import total_time_ms
from querydoctor.safety.validator import ValidatedQuery
from querydoctor.schema import SchemaSnapshot

Severity = Literal["info", "warning", "critical"]
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    title: str
    summary: str
    evidence: dict[str, Any]
    node_ids: list[str]
    confidence: float
    next_actions: list[str]


class Thresholds(BaseModel):
    """Every tunable rule threshold. Defaults are documented in docs/rule-catalog.md."""

    seq_scan_min_rows: int = 100_000
    waste_warn: float = 0.80
    waste_critical: float = 0.95
    high_filter_min_rows: int = 10_000
    misestimate_warn: float = 10.0
    misestimate_critical: float = 100.0
    misestimate_min_rows: int = 1_000
    loops_min: int = 100
    loop_time_share_warn: float = 0.20
    loop_time_share_critical: float = 0.50
    sort_spill_critical_share: float = 0.50
    hash_spill_critical_share: float = 0.50
    index_filter_waste: float = 0.50
    index_filter_min_rows: int = 1_000
    wide_result_bytes: int = 200
    wide_result_warn_bytes: int = 400
    wide_result_columns: int = 15
    duplicate_min_cost: float = 1_000.0
    duplicate_time_share_warn: float = 0.20


class AnalysisContext(BaseModel):
    plan: ParsedPlan
    schema_: SchemaSnapshot = Field(default_factory=SchemaSnapshot)
    query: ValidatedQuery | None = None
    thresholds: Thresholds = Field(default_factory=Thresholds)

    def plan_total_ms(self) -> float | None:
        """Wall time of the whole executed plan, or None for planner-only plans."""
        if self.plan.execution_time_ms is not None:
            return self.plan.execution_time_ms
        root = self.plan.root
        return total_time_ms(root.actual_time_ms, root.loops)


class DiagnosisRule(Protocol):
    rule_id: str
    supported_node_types: set[str]  # empty set = every node type

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None: ...
