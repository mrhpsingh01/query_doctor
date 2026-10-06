import type { Finding, ParsedPlan, PlanNode } from "@/lib/types";

export function node(overrides: Partial<PlanNode> = {}): PlanNode {
  return {
    node_id: "0",
    parent_id: null,
    node_type: "Seq Scan",
    relation_name: null,
    index_name: null,
    estimated_rows: 100,
    actual_rows: 100,
    estimated_cost: 100,
    actual_time_ms: 1,
    loops: 1,
    rows_removed_by_filter: null,
    shared_hit_blocks: null,
    shared_read_blocks: null,
    temp_read_blocks: null,
    temp_written_blocks: null,
    filter_condition: null,
    index_condition: null,
    join_condition: null,
    plan_width: 40,
    sort_method: null,
    hash_batches: null,
    output: [],
    error_ratio: 1,
    filter_waste: null,
    exclusive_time_ms: 1,
    io_pressure: "none",
    raw: {},
    children: [],
    ...overrides,
  };
}

/** Sort (741 ms) over a Seq Scan on orders (735 ms) that discards 99.4% of the rows it reads. */
export function slowPlan(): ParsedPlan {
  const scan = node({
    node_id: "0.0",
    parent_id: "0",
    relation_name: "orders",
    estimated_rows: 10900,
    actual_rows: 10842,
    actual_time_ms: 735.2,
    exclusive_time_ms: 735.2,
    rows_removed_by_filter: 1813561,
    filter_waste: 0.9941,
    filter_condition: "(orders.customer_id = 4242)",
    shared_hit_blocks: 120,
    shared_read_blocks: 18000,
    io_pressure: "disk",
    raw: { "Node Type": "Seq Scan", "Relation Name": "orders" },
  });
  const sort = node({
    node_type: "Sort",
    estimated_rows: 10900,
    actual_rows: 10842,
    actual_time_ms: 741.9,
    exclusive_time_ms: 6.7,
    children: [scan],
  });
  return { root: sort, planning_time_ms: 0.21, execution_time_ms: 742.1, settings: {}, executed: true, fingerprint: "abc" };
}

export function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 1,
    rule_id: "PG_SEQ_SCAN_FILTER_WASTE",
    severity: "critical",
    title: "Large scan discards most rows",
    summary: "99.4% of visited rows were discarded.",
    evidence: { rows_visited: 1824403, rows_removed: 1813561, filter_waste: 0.9941 },
    node_ids: ["0.0"],
    confidence: 0.92,
    next_actions: ["Evaluate an index covering the filter columns on orders"],
    ...overrides,
  };
}

export function candidate(overrides: Partial<import("@/lib/types").Candidate> = {}): import("@/lib/types").Candidate {
  return {
    id: 1,
    candidate_key: "70eea4f86778",
    table_name: "orders",
    ddl: "CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)",
    columns: [
      { name: "customer_id", desc: false },
      { name: "created_at", desc: true },
    ],
    include: ["id", "total_amount"],
    predicate: null,
    expression: null,
    rationale: ["equality on customer_id", "range on created_at"],
    source_rule_ids: ["PG_SEQ_SCAN_FILTER_WASTE"],
    validation: "planner_validated",
    planner_score: 0.95,
    comparison: {
      cost_reduction: 0.964,
      rows_reduction: 0.99,
      plan_risk_reduction: 0.5,
      operator_improvement: 0.7,
      planner_score: 0.95,
      uses_candidate: true,
      removed_operators: ["Seq Scan"],
      added_operators: ["Index Scan"],
      cost_before: 61527,
      cost_after: 2200,
    },
    dropped: false,
    drop_reason: null,
    rank: 1,
    ...overrides,
  };
}
