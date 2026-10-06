"""Compact builders for raw PostgreSQL plan nodes used across rule tests."""

from __future__ import annotations

from typing import Any

from querydoctor.diagnosis import AnalysisContext, Thresholds
from querydoctor.plan_parser import ParsedPlan, parse_explain
from querydoctor.schema import SchemaSnapshot, TableInfo

ALIASES = {
    "relation": "Relation Name",
    "index": "Index Name",
    "est_rows": "Plan Rows",
    "rows": "Actual Rows",
    "time": "Actual Total Time",
    "loops": "Actual Loops",
    "removed": "Rows Removed by Filter",
    "filter": "Filter",
    "index_cond": "Index Cond",
    "sort_method": "Sort Method",
    "sort_space_used": "Sort Space Used",
    "temp_written": "Temp Written Blocks",
    "temp_read": "Temp Read Blocks",
    "hash_batches": "Hash Batches",
    "width": "Plan Width",
    "cost": "Total Cost",
    "shared_read": "Shared Read Blocks",
    "shared_hit": "Shared Hit Blocks",
    "hash_cond": "Hash Cond",
    "alias": "Alias",
    "output": "Output",
}


def node(node_type: str = "Seq Scan", *, plans: list[dict] | None = None, explain_only: bool = False, **kw: Any) -> dict:
    """Raw PG node with sane ANALYZE defaults; keyword aliases map to PG JSON keys (see ALIASES)."""
    raw: dict[str, Any] = {
        "Node Type": node_type,
        "Parallel Aware": False,
        "Startup Cost": 0.0,
        "Total Cost": 100.0,
        "Plan Rows": 100,
        "Plan Width": 40,
        "Actual Startup Time": 0.1,
        "Actual Total Time": 1.0,
        "Actual Rows": 100,
        "Actual Loops": 1,
    }
    for key, value in kw.items():
        raw[ALIASES.get(key, key)] = value
    if plans:
        raw["Plans"] = plans
    if explain_only:
        for key in ("Actual Startup Time", "Actual Total Time", "Actual Rows", "Actual Loops", "Rows Removed by Filter"):
            raw.pop(key, None)
    return raw


def plan_of(root: dict, *, execution_time: float | None = None, settings: dict | None = None) -> ParsedPlan:
    wrapper: dict[str, Any] = {"Plan": root}
    if execution_time is not None:
        wrapper["Execution Time"] = execution_time
    if settings:
        wrapper["Settings"] = settings
    return parse_explain([wrapper])


def schema_of(**table_rows: int) -> SchemaSnapshot:
    return SchemaSnapshot(tables={name: TableInfo(name=name, row_estimate=rows) for name, rows in table_rows.items()})


def make_ctx(
    root: dict,
    *,
    schema: SchemaSnapshot | None = None,
    execution_time: float | None = None,
    settings: dict | None = None,
    query=None,
    **thresholds: Any,
) -> AnalysisContext:
    return AnalysisContext(
        plan=plan_of(root, execution_time=execution_time, settings=settings),
        schema_=schema or SchemaSnapshot(),
        query=query,
        thresholds=Thresholds(**thresholds),
    )
