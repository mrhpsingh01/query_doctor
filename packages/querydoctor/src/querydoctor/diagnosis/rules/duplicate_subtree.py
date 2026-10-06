from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode, iter_nodes
from querydoctor.plan_parser.metrics import total_time_ms


def _signature(node: PlanNode) -> tuple:
    return (
        node.node_type, node.relation_name, node.index_name, node.filter_condition, node.index_condition,
        node.join_condition, tuple(_signature(c) for c in node.children),
    )


def _subtree(node: PlanNode) -> list[PlanNode]:
    return list(iter_nodes(node))


def _relation(node: PlanNode) -> str | None:
    return next((n.relation_name for n in _subtree(node) if n.relation_name), None)


class DuplicateSubtree:
    """The same expensive piece of plan appears more than once: a candidate for a CTE, join or materialization."""

    rule_id = "PG_DUPLICATE_SUBTREE"
    supported_node_types: set[str] = set()

    def _groups(self, context: AnalysisContext) -> list[list[PlanNode]]:
        min_cost = context.thresholds.duplicate_min_cost
        by_signature: dict[tuple, list[PlanNode]] = {}
        for n in iter_nodes(context.plan.root):
            sub = _subtree(n)
            if _relation(n) is None or max(s.estimated_cost for s in sub) < min_cost:
                continue
            by_signature.setdefault(_signature(n), []).append(n)
        groups = [g for g in by_signature.values() if len(g) > 1]
        covered = {d.node_id for g in groups for member in g for d in _subtree(member)[1:]}
        return [g for g in groups if not any(m.node_id in covered for m in g)]

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        group = next((g for g in self._groups(context) if g[0].node_id == node.node_id), None)
        if group is None:
            return None
        total = context.plan_total_ms()
        times = [total_time_ms(m.actual_time_ms, m.loops) for m in group]
        spent = sum(t for t in times if t is not None) if any(t is not None for t in times) else None
        share = spent / total if spent is not None and total else None
        table = _relation(node)
        return Finding(
            rule_id=self.rule_id,
            severity="warning" if share is not None and share >= context.thresholds.duplicate_time_share_warn else "info",
            title="The same work is done more than once",
            summary=f"{len(group)} identical branches of the plan read {table} the same way. "
            "The result of one could be reused.",
            evidence={
                "occurrences": len(group),
                "node_type": node.node_type,
                "table": table,
                "estimated_cost_each": node.estimated_cost,
                "total_ms": spent,
                "time_share": share,
            },
            node_ids=[m.node_id for m in group],
            confidence=0.7,
            next_actions=[
                "Compute it once in a WITH clause (check whether MATERIALIZED helps) or restructure the query as a single join",
            ],
        )
