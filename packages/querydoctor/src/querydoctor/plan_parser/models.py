from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanNode(BaseModel):
    """One operator of a normalized plan.

    Row counts and times are reported as PostgreSQL reports them: *per loop* (``total_time_ms`` is the elapsed total,
    already corrected for parallel workers; ``exclusive_time_ms`` is a total too). Buffer and temp block counts are
    *totals that include every child node*, so rules use the ``exclusive_temp_*`` values to find the node that spilled.
    """

    node_id: str  # tree path: "0", "0.1", "0.1.0"
    parent_id: str | None
    node_type: str
    relation_name: str | None = None
    index_name: str | None = None

    estimated_rows: float
    actual_rows: float | None = None
    estimated_cost: float
    actual_time_ms: float | None = None
    loops: int | None = None

    rows_removed_by_filter: int | None = None
    shared_hit_blocks: int | None = None
    shared_read_blocks: int | None = None
    temp_read_blocks: int | None = None
    temp_written_blocks: int | None = None

    filter_condition: str | None = None
    index_condition: str | None = None
    join_condition: str | None = None

    plan_width: int | None = None
    sort_method: str | None = None
    hash_batches: int | None = None
    output: list[str] = Field(default_factory=list)

    error_ratio: float | None = None
    filter_waste: float | None = None
    exclusive_time_ms: float | None = None
    total_time_ms: float | None = None  # elapsed time across all loops, corrected for parallel workers
    exclusive_temp_written_blocks: int | None = None  # temp blocks written by this node itself (children excluded)
    exclusive_temp_read_blocks: int | None = None
    io_pressure: Literal["none", "memory", "disk", "temp"] = "none"

    raw: dict[str, Any] = Field(default_factory=dict)  # the node's raw JSON without child plans
    children: list[PlanNode] = Field(default_factory=list)


class ParsedPlan(BaseModel):
    root: PlanNode
    planning_time_ms: float | None = None
    execution_time_ms: float | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    executed: bool = False
    fingerprint: str


PlanNode.model_rebuild()
