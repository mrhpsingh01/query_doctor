from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode


class WideResult:
    rule_id = "PG_WIDE_RESULT"
    supported_node_types: set[str] = set()

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        # Only the root's row width is the width of what is returned to the client.
        if node.node_id != "0" or context.query is None or not context.query.has_select_star:
            return None
        width = node.plan_width
        if width is None or width < t.wide_result_bytes:
            return None
        return Finding(
            rule_id=self.rule_id,
            severity="warning" if width >= t.wide_result_warn_bytes else "info",
            title="SELECT * returns wide rows",
            summary=f"Each returned row is about {width} bytes wide. Selecting only the columns you need moves less "
            "data and can allow index-only scans.",
            evidence={
                "plan_width": width,
                "output_columns": len(node.output),
                "has_select_star": True,
            },
            node_ids=[node.node_id],
            confidence=0.8,
            next_actions=["Replace SELECT * with the columns the application actually uses"],
        )
