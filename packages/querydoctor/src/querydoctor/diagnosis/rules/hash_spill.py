from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode


def _hash_child_spilled(node: PlanNode) -> bool:
    return any(c.node_type == "Hash" and (c.hash_batches or 0) > 1 for c in node.children)


class HashSpill:
    rule_id = "PG_HASH_SPILL"
    supported_node_types = {"Hash", "Hash Join"}

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        t = context.thresholds
        temp = node.temp_written_blocks or 0
        if node.node_type == "Hash":
            spilled = (node.hash_batches or 0) > 1 or temp > 0
        else:  # a Hash Join is reported only when its Hash child did not already explain the spill
            spilled = temp > 0 and not _hash_child_spilled(node)
        if not spilled:
            return None
        total = context.plan_total_ms()
        share = (node.exclusive_time_ms or 0.0) / total if total else None
        return Finding(
            rule_id=self.rule_id,
            severity="critical" if share is not None and share >= t.hash_spill_critical_share else "warning",
            title="Hash join spilled to disk",
            summary=f"The hash table did not fit in memory: {node.hash_batches or 1} batches, "
            f"{temp:,} temporary blocks written.",
            evidence={
                "hash_batches": node.hash_batches,
                "original_hash_batches": node.raw.get("Original Hash Batches"),
                "peak_memory_kb": node.raw.get("Peak Memory Usage"),
                "temp_written_blocks": node.temp_written_blocks,
                "time_share": share,
                "work_mem": context.plan.settings.get("work_mem"),
            },
            node_ids=[node.node_id],
            confidence=0.93,
            next_actions=[
                "Reduce the rows being hashed (filter earlier or join on a smaller input)",
                "Test a higher work_mem or hash_mem_multiplier for the session",
                "Refresh statistics so the planner hashes the smaller side",
            ],
        )
