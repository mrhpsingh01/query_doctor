from __future__ import annotations

from querydoctor.plan_parser import PlanNode


def executed(node: PlanNode) -> bool:
    """True when the node has actual measurements and ran at least once."""
    return node.loops is not None and node.loops > 0 and node.actual_rows is not None


def rows_visited(node: PlanNode) -> float:
    """Total rows the node looked at across all loops (returned + filtered out)."""
    return ((node.actual_rows or 0) + (node.rows_removed_by_filter or 0)) * (node.loops or 0)
