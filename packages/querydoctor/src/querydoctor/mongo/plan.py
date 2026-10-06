"""Normalize a MongoDB explain document into the shared ``ParsedPlan`` tree.

MongoDB has no cost model, so ``estimated_*`` are zero and the UI hides estimates for this engine. What it has is exact
work counters per stage: ``nReturned``, ``docsExamined`` and ``keysExamined``. Stage times are cumulative; the exclusive
time of a stage is its own time minus its children's.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from querydoctor.plan_parser.models import ParsedPlan, PlanNode

_SHARD_STAGES = {"SINGLE_SHARD", "SHARD_MERGE", "SHARD_MERGE_SORT"}


def _children_of(stage: dict[str, Any]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for key in ("inputStage", "thenStage", "elseStage"):
        if isinstance(stage.get(key), dict):
            children.append(stage[key])
    if isinstance(stage.get("inputStages"), list):
        children += [c for c in stage["inputStages"] if isinstance(c, dict)]
    if isinstance(stage.get("shards"), list):  # sharded: each shard reports its own plan
        for shard in stage["shards"]:
            if isinstance(shard, dict):
                inner = shard.get("executionStages") or shard.get("winningPlan")
                if isinstance(inner, dict):
                    children.append(inner)
    return children


def _json(value: Any) -> str | None:
    return None if value in (None, {}, []) else json.dumps(value, separators=(",", ":"), default=str)


def _stage_node(stage: dict[str, Any], node_id: str, parent_id: str | None, namespace: str | None) -> PlanNode:
    if isinstance(stage.get("queryPlan"), dict):  # slot-based execution wraps the logical plan: use the logical plan
        merged = {**stage["queryPlan"], **{k: v for k, v in stage.items() if k in ("nReturned", "executionTimeMillisEstimate", "docsExamined", "keysExamined")}}
        return _stage_node(merged, node_id, parent_id, namespace)
    kind = str(stage.get("stage", "UNKNOWN"))
    returned = stage.get("nReturned")
    docs, keys = stage.get("docsExamined"), stage.get("keysExamined")
    total = stage.get("executionTimeMillisEstimate")
    children = [_stage_node(c, f"{node_id}.{i}", node_id, namespace) for i, c in enumerate(_children_of(stage))]
    child_time = sum(c.total_time_ms or 0.0 for c in children)
    exclusive = max(float(total) - child_time, 0.0) if total is not None else None
    removed = max(int(docs) - int(returned), 0) if docs is not None and returned is not None and kind in ("COLLSCAN", "FETCH") else None
    waste = removed / int(docs) if removed is not None and docs else None
    raw = {k: v for k, v in stage.items() if k not in ("inputStage", "inputStages", "thenStage", "elseStage", "shards", "queryPlan")}
    return PlanNode(
        node_id=node_id, parent_id=parent_id, node_type=kind,
        relation_name=namespace.split(".", 1)[-1] if namespace and kind in ("COLLSCAN", "COUNT_SCAN") else None,
        index_name=stage.get("indexName"), estimated_rows=0, estimated_cost=0,
        actual_rows=float(returned) if returned is not None else None,
        actual_time_ms=float(total) if total is not None else None, loops=1 if returned is not None else None,
        rows_removed_by_filter=removed, filter_condition=_json(stage.get("filter")),
        index_condition=_json(stage.get("indexBounds")), sort_method=("disk" if stage.get("usedDisk") else "memory") if kind.startswith("SORT") else None,
        output=[], filter_waste=waste, exclusive_time_ms=exclusive, total_time_ms=float(total) if total is not None else None,
        io_pressure="disk" if stage.get("usedDisk") else "none", raw=raw, children=children,
        docs_examined=int(docs) if docs is not None else None, keys_examined=int(keys) if keys is not None else None,
    )


def _pipeline_chain(stages: list[dict[str, Any]], namespace: str | None) -> PlanNode:
    """An aggregation's stage list (first stage first) as a tree whose root is the last stage."""
    child: PlanNode | None = None
    nodes: list[tuple[str, dict[str, Any]]] = []
    for item in stages:
        key = next(iter(item)) if item else "UNKNOWN"
        nodes.append((key, item))
    # build from the last stage down so ids read root-first: "0" is the last stage
    built: list[dict[str, Any]] = []
    for depth, (key, item) in enumerate(reversed(nodes)):
        built.append({"key": key, "item": item, "depth": depth})
    return _assemble(built, 0, None, namespace)


def _assemble(built: list[dict[str, Any]], i: int, parent_id: str | None, namespace: str | None) -> PlanNode:
    entry = built[i]
    key, item = entry["key"], entry["item"]
    node_id = "0" if parent_id is None else f"{parent_id}.0"
    returned, total = item.get("nReturned"), item.get("executionTimeMillisEstimate")
    children: list[PlanNode] = []
    if key == "$cursor":
        inner = item["$cursor"]
        plan = (inner.get("executionStats") or {}).get("executionStages") or (inner.get("queryPlanner") or {}).get("winningPlan")
        ns = (inner.get("queryPlanner") or {}).get("namespace") or namespace
        if isinstance(plan, dict):
            children.append(_stage_node(plan, f"{node_id}.0", node_id, ns))
    elif i + 1 < len(built):
        children.append(_assemble(built, i + 1, node_id, namespace))
    child_time = sum(c.total_time_ms or 0.0 for c in children)
    exclusive = max(float(total) - child_time, 0.0) if total is not None else None
    stage_args = item.get(key) if key != "$cursor" else None
    return PlanNode(
        node_id=node_id, parent_id=parent_id, node_type=key, estimated_rows=0, estimated_cost=0,
        actual_rows=float(returned) if returned is not None else None, actual_time_ms=float(total) if total is not None else None,
        loops=1 if returned is not None else None, exclusive_time_ms=exclusive, total_time_ms=float(total) if total is not None else None,
        filter_condition=_json(stage_args) if key == "$match" else None,
        raw={"stage": key, **({"args": stage_args} if stage_args is not None else {}), **{k: v for k, v in item.items() if k not in (key, "$cursor")}},
        children=children,
        docs_examined=int(item["totalDocsExamined"]) if item.get("totalDocsExamined") is not None else None,
        keys_examined=int(item["totalKeysExamined"]) if item.get("totalKeysExamined") is not None else None,
    )


def _fingerprint(root: PlanNode) -> str:
    def shape(n: PlanNode) -> list:
        return [n.node_type, n.relation_name, n.index_name, [shape(c) for c in n.children]]

    return hashlib.sha1(json.dumps(shape(root)).encode()).hexdigest()[:16]


def _first(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("stages", "queryPlanner"):
        if key in raw:
            return raw
    for value in raw.values():  # a sharded aggregate reports one explain per shard under "shards"
        if isinstance(value, dict) and ("stages" in value or "queryPlanner" in value):
            return value
    raise ValueError("unrecognized MongoDB explain output: no 'stages' or 'queryPlanner'")


def normalize_plan(raw: dict[str, Any] | str) -> ParsedPlan:
    """The shared plan tree for a MongoDB explain document (``queryPlanner`` or ``executionStats`` verbosity)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"plan is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("unrecognized MongoDB explain output")
    if "shards" in raw and "stages" not in raw and "queryPlanner" not in raw:
        raw = _first(raw)
    if "stages" not in raw and "queryPlanner" not in raw:
        raise ValueError("unrecognized MongoDB explain output: no 'stages' or 'queryPlanner'")
    namespace: str | None = None
    if "stages" in raw:
        stages = raw["stages"]
        if not isinstance(stages, list) or not stages:
            raise ValueError("an aggregation explain without stages")
        cursor = next((s["$cursor"] for s in stages if isinstance(s, dict) and "$cursor" in s), None)
        namespace = ((cursor or {}).get("queryPlanner") or {}).get("namespace")
        root = _pipeline_chain(stages, namespace)
        executed = any("nReturned" in s for s in stages if isinstance(s, dict))
        execution_ms = ((cursor or {}).get("executionStats") or {}).get("executionTimeMillis")
        if execution_ms is None and executed:
            execution_ms = max((s.get("executionTimeMillisEstimate") or 0 for s in stages if isinstance(s, dict)), default=None)
    else:
        planner = raw["queryPlanner"]
        namespace = planner.get("namespace")
        stats = raw.get("executionStats") or {}
        tree = stats.get("executionStages") or planner.get("winningPlan")
        if not isinstance(tree, dict):
            raise ValueError("unrecognized MongoDB explain output: no winning plan")
        root = _stage_node(tree, "0", None, namespace)
        executed = bool(stats.get("executionStages"))
        execution_ms = stats.get("executionTimeMillis")
    return ParsedPlan(
        root=root, planning_time_ms=None, execution_time_ms=float(execution_ms) if execution_ms is not None else None, settings={},
        executed=executed, engine="mongodb", fingerprint=_fingerprint(root),
    )
