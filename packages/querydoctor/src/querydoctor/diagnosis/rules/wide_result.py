from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode


class WideResult:
    """``SELECT *`` that returns wide rows or many columns: more data moved, and no index-only scans possible."""

    rule_id = "PG_WIDE_RESULT"
    supported_node_types: set[str] = set()

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        # Only the root's row is what is returned to the client.
        if node.node_id != "0" or context.query is None or not context.query.has_select_star:
            return None
        width = node.plan_width
        columns = len(node.output)
        wide_bytes = width is not None and width >= t.wide_result_bytes
        many_columns = columns >= t.wide_result_columns
        if not (wide_bytes or many_columns):
            return None
        size = f"about {width} bytes wide" if width is not None else "of unknown width"
        return Finding(
            rule_id=self.rule_id,
            severity="warning" if width is not None and width >= t.wide_result_warn_bytes else "info",
            title="SELECT * returns wide rows",
            summary=f"SELECT * returns {columns} columns per row, each row {size}. Selecting only the columns you need "
            "moves less data and can allow index-only scans.",
            evidence={"plan_width": width, "output_columns": columns, "has_select_star": True},
            node_ids=[node.node_id],
            confidence=0.8,
            next_actions=["Replace SELECT * with the columns the application actually uses"],
        )
