import type { Finding, ParsedPlan, PlanNode, Regression, WorkloadDashboard, WorkloadQuery } from "@/lib/types";

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
    total_time_ms: 1,
    exclusive_temp_written_blocks: null,
    exclusive_temp_read_blocks: null,
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
    total_time_ms: 735.2,
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
    total_time_ms: 741.9,
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
    prediction: null,
    ...overrides,
  };
}

export function prediction(overrides: Partial<import("@/lib/types").Prediction> = {}): import("@/lib/types").Prediction {
  return {
    expected_speedup: 8.2, p_improve: 0.93, expected_index_mb: 4.5, p_regression: 0.02, rank_score: 1.4, used_for_ranking: false,
    model_version: "lgbm_regressor-abc123",
    contributions: [
      { feature: "d_removed_seq_scans", value: 1, effect: 1.2, phrase: "removes a sequential scan raises the expected speedup" },
      { feature: "c_equality_cov", value: 1, effect: 0.6, phrase: "how many of the equality filters the index covers raises the expected speedup" },
      { feature: "t_log_rows", value: 11.5, effect: -0.2, phrase: "how many rows the table holds lowers the expected speedup" },
    ],
    ...overrides,
  };
}

export function runStats(median: number, extra: Partial<import("@/lib/types").RunStats> = {}): import("@/lib/types").RunStats {
  return { n: 7, median_ms: median, p95_ms: median * 1.1, mean_ms: median, stdev_ms: median * 0.04, cv: 0.04, min_ms: median * 0.95, max_ms: median * 1.15, ...extra };
}

export function benchmarkRun(overrides: Partial<import("@/lib/types").BenchmarkRun> = {}): import("@/lib/types").BenchmarkRun {
  const before = [700, 742, 750, 735, 760, 740, 745].map((ms) => ({ execution_ms: ms, planning_ms: 0.2, rows: 10, shared_hit_blocks: 5, shared_read_blocks: 9, temp_blocks: 0 }));
  const after = [40, 41, 43, 41, 39, 42, 41].map((ms) => ({ execution_ms: ms, planning_ms: 0.2, rows: 10, shared_hit_blocks: 5, shared_read_blocks: 0, temp_blocks: 0 }));
  return {
    id: 3,
    analysis_id: 1,
    candidate_id: 1,
    status: "completed",
    stage: "teardown",
    config: { runs: 7, warmups: 1 },
    result: {
      before: { ...runStats(742), p95_ms: 811 },
      after: { ...runStats(41), p95_ms: 48 },
      before_samples: before,
      after_samples: after,
      improvement_pct: 94.47,
      index_size_bytes: 86_000_000,
      index_build_ms: 4200,
      plan_fingerprint_before: "aaa",
      plan_fingerprint_after: "bbb",
      checksum_match: true,
      warnings: [],
      pg_version: "PostgreSQL 16.15",
      settings: { work_mem: "4096" },
      dataset_snapshot: "adf070622df0",
      ddl: "CREATE INDEX ON orders (customer_id)",
    },
    error: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

export function workloadQuery(overrides: Partial<WorkloadQuery> = {}): WorkloadQuery {
  return {
    fingerprint: "8ca0e74b3bc8a91d",
    normalized_sql: "SELECT id, total_amount FROM orders WHERE customer_id = ? AND created_at >= ?",
    sample_sql: "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2",
    calls: 1500, total_ms: 9000, mean_ms: 6, stddev_ms: 2, min_ms: 3, max_ms: 20, rows: 30000,
    shared_hit_blocks: 5000, shared_read_blocks: 0, temp_blocks: 0, p95_est_ms: 9.29, tables: ["orders"], rank: 1, share: 0.72,
    ...overrides,
  };
}

export function regression(overrides: Partial<Regression> = {}): Regression {
  return { fingerprint: "8ca0e74b3bc8a91d", baseline_mean_ms: 5, recent_mean_ms: 25, ratio: 5, recent_calls: 60, ...overrides };
}

export function dashboard(overrides: Partial<WorkloadDashboard> = {}): WorkloadDashboard {
  const queries = [workloadQuery(), workloadQuery({ fingerprint: "ffffffffffffffff", normalized_sql: "SELECT * FROM events ORDER BY payload", sample_sql: "SELECT * FROM events WHERE event_type = $1 ORDER BY payload", calls: 20, total_ms: 3500, mean_ms: 175, p95_est_ms: 400, rank: 2, share: 0.28 })];
  return {
    snapshot: { id: 2, project_id: 1, engine: "postgres", source: "pg_stat_statements", taken_at: "2026-01-01T10:00:00Z", query_count: 2, skipped_count: 4 },
    compare_to: null,
    queries,
    panels: {
      highest_total_time: queries, slowest_p95: [queries[1], queries[0]], most_frequent: queries, highest_io: [], most_unstable: [],
      largest_regression: [], missing_validation: queries,
    },
    regressions: [],
    ...overrides,
  };
}
