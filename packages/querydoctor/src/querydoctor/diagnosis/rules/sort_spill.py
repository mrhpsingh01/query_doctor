from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode


class SortSpill:
    rule_id = "PG_SORT_SPILL"
    supported_node_types = {"Sort", "Incremental Sort"}

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        # exclusive: PostgreSQL's temp counters include children, so a quicksort above a spilling join must not match
        spilled = "external" in (node.sort_method or "").lower() or (node.exclusive_temp_written_blocks or 0) > 0
        if not spilled:
            return None
        total = context.plan_total_ms()
        share = (node.exclusive_time_ms or 0.0) / total if total else None
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if share is not None and share >= t.sort_spill_critical_share else "warning",
            title="Sort spilled to disk",
            summary=f"Sort used '{node.sort_method or 'temporary files'}' and wrote "
            f"{node.exclusive_temp_written_blocks or 0:,} temporary blocks.",
            evidence={
                "sort_method": node.sort_method,
                "temp_written_blocks": node.exclusive_temp_written_blocks,
                "temp_read_blocks": node.exclusive_temp_read_blocks,
                "sort_space_used_kb": node.raw.get("Sort Space Used"),
                "exclusive_time_ms": node.exclusive_time_ms,
                "time_share": share,
                "work_mem": context.plan.settings.get("work_mem"),
            },
            node_ids=[node.node_id],
            confidence=0.95,
            next_actions=[
                "Reduce the number of rows sorted (filter earlier, select fewer columns)",
                "Add an index matching the ORDER BY so the sort can be skipped",
                "Test a higher work_mem for the session before changing it globally",
            ],
        )
