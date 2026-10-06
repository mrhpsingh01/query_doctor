"""Features for the recommendation ranker, all computed from *planner-only* evidence.

Nothing here needs the query to be executed: the original plan is an estimate-only plan, the candidate's plan comes from
HypoPG, and the schema extras come from catalog statistics. ``FEATURE_NAMES`` is a fixed, versioned list: a stored model
is only used when its feature names equal this list, so changing it means bumping ``FEATURE_VERSION`` and retraining.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querydoctor.candidates import IndexCandidate, PlanComparison, TableUsage
from querydoctor.candidates.compare import INDEX_SCAN_TYPES
from querydoctor.plan_parser import ParsedPlan, PlanNode, iter_nodes
from querydoctor.schema import SchemaSnapshot

FEATURE_VERSION = 1

QUERY = [
    "q_tables", "q_joins", "q_equality_cols", "q_range_cols", "q_join_cols", "q_order_cols", "q_group_cols", "q_output_cols",
    "q_select_star", "q_subqueries", "q_expression_preds", "q_has_limit",
]
TABLE = [
    "t_log_rows", "t_log_size", "t_columns", "t_indexes", "t_write_ratio", "t_key_distinct", "t_key_null", "t_key_corr",
    "t_has_extras",
]
PLAN = [
    "p_seq_scans", "p_index_scans", "p_sorts", "p_joins", "p_aggregates", "p_nodes", "p_depth", "p_log_cost",
    "p_log_root_rows", "p_log_seq_rows",
]
CANDIDATE = [
    "c_key_cols", "c_include_cols", "c_partial", "c_expression", "c_desc_cols", "c_key_width", "c_overlap", "c_lead_equality",
    "c_lead_join", "c_lead_range", "c_lead_order", "c_equality_cov", "c_range_cov", "c_sort_served", "c_output_cov",
]
DIFFERENCE = [
    "d_has_plan", "d_cost_reduction", "d_rows_reduction", "d_risk_reduction", "d_operator_improvement", "d_planner_score",
    "d_uses_candidate", "d_removed_seq_scans", "d_added_index_scans", "d_sort_removed", "d_join_changed", "d_depth_delta",
    "d_node_delta",
]
FEATURE_NAMES: list[str] = [*QUERY, *TABLE, *PLAN, *CANDIDATE, *DIFFERENCE]

JOIN_TYPES = {"Nested Loop", "Hash Join", "Merge Join"}
AGGREGATE_TYPES = {"Aggregate", "GroupAggregate", "HashAggregate", "MixedAggregate"}
NEUTRAL_DISTINCT = 0.5  # used when pg_stats knowledge is missing
_WIDTHS = {"smallint": 2, "integer": 4, "int": 4, "bigint": 8, "boolean": 1, "date": 4, "uuid": 16, "text": 24, "character": 24}


@dataclass
class FeatureInput:
    sql: str
    usage: dict[str, TableUsage]
    schema: SchemaSnapshot
    original: ParsedPlan
    candidate: IndexCandidate
    candidate_plan: ParsedPlan | None = None
    comparison: PlanComparison | None = None


def _depth(node: PlanNode) -> int:
    return 1 + max((_depth(c) for c in node.children), default=0)


def _finite(x: float) -> float:
    return float(x) if math.isfinite(x) else 0.0


def _query_features(fi: FeatureInput) -> dict[str, float]:
    usage = list(fi.usage.values())
    joins = subqueries = limit = 0
    try:
        tree = sqlglot.parse_one(fi.sql, read="postgres")
        joins = len(list(tree.find_all(exp.Join)))
        subqueries = max(len(list(tree.find_all(exp.Select))) - 1, 0)
        limit = int(tree.find(exp.Limit) is not None)
    except (SqlglotError, ValueError, AttributeError):
        pass
    return {
        "q_tables": len(usage), "q_joins": joins, "q_equality_cols": sum(len(u.equality) for u in usage),
        "q_range_cols": sum(len(u.range) for u in usage), "q_join_cols": sum(len(u.join) for u in usage),
        "q_order_cols": sum(len(u.order_by) for u in usage), "q_group_cols": sum(len(u.group_by) for u in usage),
        "q_output_cols": sum(len(u.output) for u in usage), "q_select_star": int(any(u.select_star for u in usage)),
        "q_subqueries": subqueries, "q_expression_preds": sum(len(u.expressions) for u in usage), "q_has_limit": limit,
    }


def _key_names(c: IndexCandidate) -> list[str]:
    return [col.name for col in c.columns]


def _table_features(fi: FeatureInput) -> dict[str, float]:
    info = fi.schema.tables.get(fi.candidate.table)
    out = {
        "t_log_rows": 0.0, "t_log_size": 0.0, "t_columns": 0, "t_indexes": len(fi.schema.indexes.get(fi.candidate.table, [])),
        "t_write_ratio": 0.0, "t_key_distinct": NEUTRAL_DISTINCT, "t_key_null": 0.0, "t_key_corr": 0.0, "t_has_extras": 0,
    }
    if info is None:
        return out
    out["t_log_rows"] = math.log1p(max(info.row_estimate, 0))
    out["t_columns"] = len(info.columns)
    has_extras = info.size_bytes is not None
    out["t_has_extras"] = int(has_extras)
    if info.size_bytes is not None:
        out["t_log_size"] = math.log1p(info.size_bytes)
    if info.write_ratio is not None:
        out["t_write_ratio"] = info.write_ratio
    stats = [info.column_stats[c] for c in _key_names(fi.candidate) if c in info.column_stats]
    if stats:
        out["t_key_distinct"] = sum(s.n_distinct_fraction for s in stats) / len(stats)
        out["t_key_null"] = sum(s.null_frac for s in stats) / len(stats)
        out["t_key_corr"] = sum(abs(s.correlation) for s in stats) / len(stats)
    return out


def _plan_features(plan: ParsedPlan) -> dict[str, float]:
    types = Counter(n.node_type for n in iter_nodes(plan.root))
    seq_rows = sum(n.estimated_rows for n in iter_nodes(plan.root) if n.node_type == "Seq Scan")
    return {
        "p_seq_scans": types["Seq Scan"], "p_index_scans": sum(types[t] for t in INDEX_SCAN_TYPES), "p_sorts": types["Sort"],
        "p_joins": sum(types[t] for t in JOIN_TYPES), "p_aggregates": sum(types[t] for t in AGGREGATE_TYPES),
        "p_nodes": sum(types.values()), "p_depth": _depth(plan.root), "p_log_cost": math.log1p(max(plan.root.estimated_cost, 0)),
        "p_log_root_rows": math.log1p(max(plan.root.estimated_rows, 0)), "p_log_seq_rows": math.log1p(max(seq_rows, 0)),
    }


def _key_width(fi: FeatureInput) -> int:
    info = fi.schema.tables.get(fi.candidate.table)
    names = _key_names(fi.candidate) or ([fi.candidate.expression] if fi.candidate.expression else [])
    total = 0
    for name in names:
        declared = (info.columns.get(name, "") if info else "").lower()
        total += next((w for prefix, w in _WIDTHS.items() if declared.startswith(prefix)), 8)
    return total


def _overlap(fi: FeatureInput) -> float:
    key = _key_names(fi.candidate)
    if not key:
        return 0.0
    best = 0
    for index in fi.schema.indexes.get(fi.candidate.table, []):
        shared = 0
        for ours, theirs in zip(key, index.columns):
            if ours != theirs:
                break
            shared += 1
        best = max(best, shared)
    return best / len(key)


def _coverage(wanted: list[str], key: list[str]) -> float:
    return sum(1 for w in wanted if w in key) / len(wanted) if wanted else 0.0


def _candidate_features(fi: FeatureInput) -> dict[str, float]:
    c = fi.candidate
    u = fi.usage.get(c.table, TableUsage())
    key = _key_names(c)
    lead = key[0] if key else None
    order = [name for name, _desc in u.order_by]
    remaining = [k for k in key if k not in u.equality]  # equality columns have a fixed value, so order passes through them
    served = bool(order) and remaining[: len(order)] == order
    provided = set(key) | set(c.include)
    if u.select_star:
        output_cov = 0.0
    else:
        output_cov = sum(1 for o in u.output if o in provided) / len(u.output) if u.output else 1.0
    return {
        "c_key_cols": len(key) if key else int(bool(c.expression)), "c_include_cols": len(c.include),
        "c_partial": int(c.predicate is not None), "c_expression": int(c.expression is not None),
        "c_desc_cols": sum(1 for col in c.columns if col.desc), "c_key_width": _key_width(fi), "c_overlap": _overlap(fi),
        "c_lead_equality": int(lead in u.equality), "c_lead_join": int(lead in u.join and lead not in u.equality),
        "c_lead_range": int(lead in u.range and lead not in u.equality and lead not in u.join),
        "c_lead_order": int(lead in order and lead not in u.equality and lead not in u.join and lead not in u.range),
        "c_equality_cov": _coverage(u.equality, key), "c_range_cov": _coverage(u.range, key), "c_sort_served": int(served),
        "c_output_cov": output_cov,
    }


def _difference_features(fi: FeatureInput) -> dict[str, float]:
    out = dict.fromkeys(DIFFERENCE, 0.0)
    cmp, plan = fi.comparison, fi.candidate_plan
    if cmp is None or plan is None:
        return out
    original_joins = Counter(n.node_type for n in iter_nodes(fi.original.root) if n.node_type in JOIN_TYPES)
    new_joins = Counter(n.node_type for n in iter_nodes(plan.root) if n.node_type in JOIN_TYPES)
    out.update(
        d_has_plan=1, d_cost_reduction=cmp.cost_reduction, d_rows_reduction=cmp.rows_reduction,
        d_risk_reduction=cmp.plan_risk_reduction, d_operator_improvement=cmp.operator_improvement,
        d_planner_score=cmp.planner_score, d_uses_candidate=int(cmp.uses_candidate),
        d_removed_seq_scans=cmp.removed_operators.count("Seq Scan"),
        d_added_index_scans=sum(cmp.added_operators.count(t) for t in INDEX_SCAN_TYPES),
        d_sort_removed=int("Sort" in cmp.removed_operators), d_join_changed=int(original_joins != new_joins),
        d_depth_delta=_depth(plan.root) - _depth(fi.original.root),
        d_node_delta=sum(1 for _ in iter_nodes(plan.root)) - sum(1 for _ in iter_nodes(fi.original.root)),
    )
    return out


def extract_features(fi: FeatureInput) -> dict[str, float]:
    """Every name in ``FEATURE_NAMES``, in order, all finite."""
    merged = {
        **_query_features(fi), **_table_features(fi), **_plan_features(fi.original), **_candidate_features(fi),
        **_difference_features(fi),
    }
    return {name: _finite(merged[name]) for name in FEATURE_NAMES}
