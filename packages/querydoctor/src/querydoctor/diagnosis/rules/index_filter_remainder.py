from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._common import executed, rows_visited
from querydoctor.diagnosis.rules._sql import column_names
from querydoctor.plan_parser import PlanNode


class IndexFilterRemainder:
    """An index narrowed the scan, but a leftover filter on other columns still discards most fetched rows."""

    rule_id = "PG_INDEX_FILTER_REMAINDER"
    supported_node_types = {"Index Scan", "Index Only Scan", "Bitmap Heap Scan"}

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        if not executed(node) or node.filter_waste is None or not node.filter_condition:
            return None
        visited = rows_visited(node)
        if visited < t.index_filter_min_rows or node.filter_waste <= t.index_filter_waste:
            return None
        filter_cols = column_names(node.filter_condition)
        index_cols = column_names(node.index_condition)
        extra = [c for c in filter_cols if c not in index_cols]
        if not extra:
            return None
        target = node.relation_name or "the table"
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if node.filter_waste > t.waste_critical else "warning",
            title="Index finds rows, then a filter throws most away",
            summary=f"{node.index_name or 'The index'} fetched {int(visited):,} rows and the filter on "
            f"{', '.join(extra)} removed {node.filter_waste:.1%} of them.",
            evidence={
                "table": node.relation_name,
                "index": node.index_name,
                "index_condition": node.index_condition,
                "index_condition_columns": index_cols,
                "filter": node.filter_condition,
                "filter_columns": extra,
                "rows_visited": int(visited),
                "rows_removed": (node.rows_removed_by_filter or 0) * (node.loops or 1),
                "filter_waste": node.filter_waste,
                "loops": node.loops,
            },
            node_ids=[node.node_id],
            confidence=0.88,
            next_actions=[
                f"Add {', '.join(extra)} to the index on {target} as a composite index, so the filter is applied "
                "inside the index",
                "If the filter is on a constant, consider a partial index instead",
            ],
        )
