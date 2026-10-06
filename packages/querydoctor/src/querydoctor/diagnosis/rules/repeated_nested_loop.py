from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._common import executed
from querydoctor.plan_parser import PlanNode


class RepeatedNestedLoop:
    rule_id = "PG_REPEATED_NESTED_LOOP"
    supported_node_types: set[str] = set()

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        total = context.plan_total_ms()
        if not executed(node) or (node.loops or 0) < t.loops_min or not total:
            return None
        node_total = node.exclusive_time_ms or 0.0
        share = node_total / total
        if share < t.loop_time_share_warn:
            return None
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if share >= t.loop_time_share_critical else "warning",
            title=f"{node.node_type} re-executed {node.loops:,} times",
            summary=f"Inner work ran {node.loops:,} times, taking {node_total:,.0f} ms ({share:.0%} of the query).",
            evidence={
                "loops": node.loops,
                "per_loop_ms": node.actual_time_ms,
                "total_ms": node_total,
                "time_share": share,
                "table": node.relation_name,
                "condition": node.filter_condition or node.index_condition or node.join_condition,
            },
            node_ids=[node.node_id],
            confidence=0.88,
            next_actions=[
                "Index the join key on the inner table so each loop is an index lookup",
                "Consider whether a hash or merge join would suit the row counts better",
            ],
        )
