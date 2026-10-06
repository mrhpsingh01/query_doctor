from __future__ import annotations

import hashlib

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.plan_parser import PlanNode, iter_nodes
from querydoctor.plan_parser.metrics import total_time_ms


def _analyse(root: PlanNode, min_cost: float) -> list[list[PlanNode]]:
    """Groups of structurally identical, expensive subtrees (largest matching subtree only), in plan order.

    One bottom-up pass computes a signature, the subtree's highest cost and its table for every node, so the whole
    analysis is linear in the plan size (it runs once per plan, see ``AnalysisContext.memo``)."""
    info: dict[str, tuple[str, float, str | None]] = {}

    def visit(node: PlanNode) -> tuple[str, float, str | None]:
        child_info = [visit(c) for c in node.children]
        parts = [node.node_type, node.relation_name, node.index_name, node.filter_condition, node.index_condition,
                 node.join_condition, *(sig for sig, _cost, _rel in child_info)]
        signature = hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()
        cost = max([node.estimated_cost, *(c for _s, c, _r in child_info)])
        relation = node.relation_name or next((rel for _s, _c, rel in child_info if rel), None)
        info[node.node_id] = (signature, cost, relation)
        return info[node.node_id]

    visit(root)
    by_signature: dict[str, list[PlanNode]] = {}
    for n in iter_nodes(root):
        signature, cost, relation = info[n.node_id]
        if relation is not None and cost >= min_cost:
            by_signature.setdefault(signature, []).append(n)
    groups = [g for g in by_signature.values() if len(g) > 1]
    covered = {d.node_id for g in groups for member in g for d in list(iter_nodes(member))[1:]}
    return [g for g in groups if not any(m.node_id in covered for m in g)]


def _relation(node: PlanNode) -> str | None:
    return next((n.relation_name for n in iter_nodes(node) if n.relation_name), None)


class DuplicateSubtree:
    """The same expensive piece of plan appears more than once: a candidate for a CTE, join or materialization."""

    rule_id = "PG_DUPLICATE_SUBTREE"
    supported_node_types: set[str] = set()

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        groups = context.memo(
            "duplicate_subtree_groups", lambda: _analyse(context.plan.root, context.thresholds.duplicate_min_cost)
        )
        group = next((g for g in groups if g[0].node_id == node.node_id), None)
        if group is None:
            return None
        total = context.plan_total_ms()
        times = [m.total_time_ms for m in group]
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
