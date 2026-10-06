from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._common import executed, rows_visited
from querydoctor.plan_parser import PlanNode


class SeqScanFilterWaste:
    rule_id = "PG_SEQ_SCAN_FILTER_WASTE"
    supported_node_types = {"Seq Scan"}

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        if executed(node):
            removed = node.rows_removed_by_filter or 0
            visited, waste, estimated, confidence = rows_visited(node), node.filter_waste, False, 0.92
            if waste is None:
                return None
        else:
            # Planner-only: infer waste from table size and the planner's row estimate.
            table_rows = context.schema_.row_estimate(node.relation_name)
            if node.loops is not None or not node.filter_condition or not table_rows:
                return None
            visited = float(table_rows)
            waste = 1.0 - min(node.estimated_rows / table_rows, 1.0)
            removed = int(visited - node.estimated_rows)
            estimated, confidence = True, 0.6

        if visited <= t.seq_scan_min_rows or waste <= t.waste_warn:
            return None
        severity = "critical" if waste > t.waste_critical and not estimated else "warning"
        return Finding(
            rule_id=self.rule_id,
            severity=severity,
            title="Large scan discards most rows",
            summary=f"{waste:.1%} of visited rows were discarded"
            + (" (estimated from planner statistics)." if estimated else "."),
            evidence={
                "table": node.relation_name,
                "rows_visited": int(visited),
                "rows_removed": removed,
                "filter_waste": waste,
                "filter": node.filter_condition,
                "estimated": estimated,
            },
            node_ids=[node.node_id],
            confidence=confidence,
            next_actions=[
                f"Evaluate an index covering the filter columns on {node.relation_name}"
                if node.relation_name
                else "Evaluate an index covering the filter columns"
            ],
        )
