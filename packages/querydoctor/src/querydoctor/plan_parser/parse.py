from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from querydoctor.plan_parser import metrics
from querydoctor.plan_parser.models import ParsedPlan, PlanNode


def _as_int(value: Any) -> int | None:
    return int(value) if value is not None else None


PARALLEL_PARENTS = ("Gather", "Gather Merge")


def _build(raw: dict[str, Any], node_id: str, parent_id: str | None, parallelism: int = 1) -> PlanNode:
    # Below a Gather, "Actual Loops" counts processes (workers + the leader) and each loop's time is an average, so
    # time x loops would be summed CPU time. Dividing by the number of processes gives elapsed time.
    child_parallelism = parallelism
    if raw["Node Type"] in PARALLEL_PARENTS:
        child_parallelism = max(int(raw.get("Workers Launched") or 0), 0) + 1
    children = [
        _build(child, f"{node_id}.{i}", node_id, child_parallelism) for i, child in enumerate(raw.get("Plans", []))
    ]

    has_actuals = "Actual Total Time" in raw
    loops = _as_int(raw.get("Actual Loops")) if has_actuals else None
    actual_rows = float(raw["Actual Rows"]) if "Actual Rows" in raw else None
    actual_time = float(raw["Actual Total Time"]) if has_actuals else None
    estimated_rows = float(raw.get("Plan Rows", 0))
    removed = _as_int(raw.get("Rows Removed by Filter"))

    summed = metrics.total_time_ms(actual_time, loops)
    own_total = None if summed is None else summed / parallelism
    child_totals = [c.total_time_ms for c in children]

    shared_hit = _as_int(raw.get("Shared Hit Blocks"))
    shared_read = _as_int(raw.get("Shared Read Blocks"))
    temp_read = _as_int(raw.get("Temp Read Blocks"))
    temp_written = _as_int(raw.get("Temp Written Blocks"))

    return PlanNode(
        node_id=node_id,
        parent_id=parent_id,
        node_type=raw["Node Type"],
        relation_name=raw.get("Relation Name"),
        index_name=raw.get("Index Name"),
        estimated_rows=estimated_rows,
        actual_rows=actual_rows,
        estimated_cost=float(raw.get("Total Cost", 0.0)),
        actual_time_ms=actual_time,
        loops=loops,
        rows_removed_by_filter=removed,
        shared_hit_blocks=shared_hit,
        shared_read_blocks=shared_read,
        temp_read_blocks=temp_read,
        temp_written_blocks=temp_written,
        filter_condition=raw.get("Filter"),
        index_condition=raw.get("Index Cond") or raw.get("Recheck Cond"),
        join_condition=raw.get("Hash Cond") or raw.get("Merge Cond") or raw.get("Join Filter"),
        plan_width=_as_int(raw.get("Plan Width")),
        sort_method=raw.get("Sort Method"),
        hash_batches=_as_int(raw.get("Hash Batches")),
        output=list(raw.get("Output", [])),
        error_ratio=metrics.error_ratio(estimated_rows, actual_rows, loops),
        filter_waste=metrics.filter_waste(actual_rows, removed, loops),
        exclusive_time_ms=metrics.exclusive_time_ms(own_total, child_totals),
        total_time_ms=own_total,
        exclusive_temp_written_blocks=metrics.exclusive_blocks(temp_written, [c.temp_written_blocks for c in children]),
        exclusive_temp_read_blocks=metrics.exclusive_blocks(temp_read, [c.temp_read_blocks for c in children]),
        io_pressure=metrics.io_pressure(temp_read, temp_written, shared_read, shared_hit),
        raw={k: v for k, v in raw.items() if k != "Plans"},
        children=children,
    )


def iter_nodes(node: PlanNode) -> Iterator[PlanNode]:
    """Pre-order traversal."""
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def _fingerprint(node: PlanNode) -> str:
    def shape(n: PlanNode) -> list:
        return [n.node_type, n.relation_name, n.index_name, [shape(c) for c in n.children]]

    return hashlib.sha1(json.dumps(shape(node)).encode()).hexdigest()[:16]


def parse_explain(raw: str | dict | list) -> ParsedPlan:
    """Normalize PostgreSQL ``EXPLAIN (FORMAT JSON)`` output (string, list, wrapper dict or bare plan)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"plan is not valid JSON: {exc}") from exc
    if isinstance(raw, list):
        if not raw:
            raise ValueError("empty plan")
        raw = raw[0]
    if not isinstance(raw, dict):
        raise ValueError("unrecognized plan structure")

    if "Plan" in raw:
        wrapper, plan = raw, raw["Plan"]
    elif "Node Type" in raw:
        wrapper, plan = {}, raw
    else:
        raise ValueError("unrecognized plan structure: no 'Plan' or 'Node Type' key")

    root = _build(plan, "0", None)
    return ParsedPlan(
        root=root,
        planning_time_ms=wrapper.get("Planning Time"),
        execution_time_ms=wrapper.get("Execution Time"),
        settings={k: str(v) for k, v in wrapper.get("Settings", {}).items()},
        executed="Actual Total Time" in plan,
        fingerprint=_fingerprint(root),
    )
