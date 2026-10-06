from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._common import executed, rows_visited
from querydoctor.plan_parser import PlanNode


class HighFilterWaste:
    rule_id = "PG_HIGH_FILTER_WASTE"
    supported_node_types = {
        "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "CTE Scan", "Subquery Scan", "Function Scan", "Values Scan",
    }

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        if not executed(node) or node.filter_waste is None:
            return None
        visited = rows_visited(node)
        waste = node.filter_waste
        if visited < t.high_filter_min_rows or waste <= t.waste_warn:
            return None
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if waste > t.waste_critical else "warning",
            title=f"{node.node_type} filters out most fetched rows",
            summary=f"{waste:.1%} of the {int(visited):,} rows fetched were removed by a filter.",
            evidence={
                "table": node.relation_name,
                "index": node.index_name,
                "rows_visited": int(visited),
                "rows_removed": node.rows_removed_by_filter,
                "filter_waste": waste,
                "filter": node.filter_condition,
            },
            node_ids=[node.node_id],
            confidence=0.85,
            next_actions=["Add the filtered columns to the index (composite or partial) or rewrite the predicate"],
        )
