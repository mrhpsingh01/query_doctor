from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._common import executed
from querydoctor.plan_parser import PlanNode


class CardinalityMisestimate:
    rule_id = "PG_CARDINALITY_MISESTIMATE"
    supported_node_types: set[str] = set()

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        if not executed(node) or node.error_ratio is None:
            return None
        actual = node.actual_rows or 0.0
        ratio = node.error_ratio
        if ratio < t.misestimate_warn or max(actual, node.estimated_rows) < t.misestimate_min_rows:
            return None
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if ratio >= t.misestimate_critical else "warning",
            title="Planner row estimate is far from reality",
            summary=f"{node.node_type} estimated {node.estimated_rows:,.0f} rows but produced {actual:,.0f} "
            f"({ratio:,.0f}x off).",
            evidence={
                "estimated_rows": node.estimated_rows,
                "actual_rows": actual,
                "error_ratio": ratio,
                "direction": "underestimated" if actual > node.estimated_rows else "overestimated",
                "loops": node.loops,
                "table": node.relation_name,
            },
            node_ids=[node.node_id],
            confidence=0.9,
            next_actions=[
                "Run ANALYZE on the involved tables",
                "Raise the statistics target for the filtered columns or create extended statistics for correlated columns",
            ],
        )
