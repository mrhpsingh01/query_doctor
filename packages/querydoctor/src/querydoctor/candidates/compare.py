"""Compare the plan before and after a (hypothetical) index.

The planner score is *evidence, not proof*: it says the planner would change its plan, not that the query gets
faster. Only a benchmark can establish that.

    planner_score = 0.35 * cost_reduction + 0.25 * rows_reduction + 0.20 * plan_risk_reduction
                  + 0.20 * operator_improvement          (each term clamped to 0..1)
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from querydoctor.plan_parser import ParsedPlan, iter_nodes

SCAN_TYPES = {"Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan"}
INDEX_SCAN_TYPES = {"Index Scan", "Index Only Scan", "Bitmap Heap Scan"}


class PlanComparison(BaseModel):
    cost_reduction: float
    rows_reduction: float
    plan_risk_reduction: float
    operator_improvement: float
    planner_score: float
    uses_candidate: bool
    removed_operators: list[str]
    added_operators: list[str]
    cost_before: float
    cost_after: float


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _reduction(before: float, after: float) -> float:
    return _clamp((before - after) / before) if before > 0 else 0.0


def _types(plan: ParsedPlan) -> Counter:
    return Counter(n.node_type for n in iter_nodes(plan.root))


def _index_names(plan: ParsedPlan) -> set[str]:
    return {n.index_name for n in iter_nodes(plan.root) if n.index_name}


def _rows_read(plan: ParsedPlan, table_rows: dict[str, int] | None) -> float:
    """Estimated rows the scans read. A Seq Scan reads its whole table (when its size is known); index scans read
    roughly the rows they return."""
    total = 0.0
    for n in iter_nodes(plan.root):
        if n.node_type not in SCAN_TYPES:
            continue
        if n.node_type == "Seq Scan" and table_rows and n.relation_name in table_rows:
            total += table_rows[n.relation_name]
        else:
            total += n.estimated_rows
    return total


def _risk(plan: ParsedPlan) -> float:
    """Count of operators that tend to hurt as data grows: sequential scans and explicit sorts, with a nested loop
    over a sequential scan counted twice."""
    risk = 0.0
    for n in iter_nodes(plan.root):
        if n.node_type in ("Seq Scan", "Sort"):
            risk += 1
        if n.node_type == "Nested Loop" and any(s.node_type == "Seq Scan" for c in n.children[1:] for s in iter_nodes(c)):
            risk += 2
    return risk


def _expand(counter: Counter) -> list[str]:
    return sorted(counter.elements())


def compare_plans(
    before: ParsedPlan, after: ParsedPlan, candidate_ddl: str, table_rows: dict[str, int] | None = None
) -> PlanComparison:
    types_b, types_a = _types(before), _types(after)
    index_scans_b = sum(types_b[t] for t in INDEX_SCAN_TYPES)
    index_scans_a = sum(types_a[t] for t in INDEX_SCAN_TYPES)

    cost = _reduction(before.root.estimated_cost, after.root.estimated_cost)
    rows = _reduction(_rows_read(before, table_rows), _rows_read(after, table_rows))
    risk = _reduction(_risk(before), _risk(after))
    operators = _clamp(
        (0.7 if types_a["Seq Scan"] < types_b["Seq Scan"] and index_scans_a > index_scans_b else 0.0)
        + (0.3 if types_a["Sort"] < types_b["Sort"] else 0.0)
    )
    uses_candidate = bool(_index_names(after) - _index_names(before))
    score = 0.35 * cost + 0.25 * rows + 0.20 * risk + 0.20 * operators if uses_candidate else 0.0

    return PlanComparison(
        cost_reduction=cost, rows_reduction=rows, plan_risk_reduction=risk, operator_improvement=operators,
        planner_score=_clamp(score), uses_candidate=uses_candidate,
        removed_operators=_expand(types_b - types_a), added_operators=_expand(types_a - types_b),
        cost_before=before.root.estimated_cost, cost_after=after.root.estimated_cost,
    )

