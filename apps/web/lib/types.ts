// Mirrors the API's JSON (apps/api/app/schemas + querydoctor.plan_parser). Kept by hand; the API is the source of truth.

export type Severity = "info" | "warning" | "critical";
export type Validation = "suggested" | "planner_validated" | "benchmark_validated";
export type AnalysisStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface PlanNode {
  node_id: string;
  parent_id: string | null;
  node_type: string;
  relation_name: string | null;
  index_name: string | null;
  estimated_rows: number;
  actual_rows: number | null;
  estimated_cost: number;
  actual_time_ms: number | null;
  loops: number | null;
  rows_removed_by_filter: number | null;
  shared_hit_blocks: number | null;
  shared_read_blocks: number | null;
  temp_read_blocks: number | null;
  temp_written_blocks: number | null;
  filter_condition: string | null;
  index_condition: string | null;
  join_condition: string | null;
  plan_width: number | null;
  sort_method: string | null;
  hash_batches: number | null;
  output: string[];
  error_ratio: number | null;
  filter_waste: number | null;
  exclusive_time_ms: number | null;
  total_time_ms: number | null;
  exclusive_temp_written_blocks: number | null;
  exclusive_temp_read_blocks: number | null;
  io_pressure: "none" | "memory" | "disk" | "temp";
  raw: Record<string, unknown>;
  children: PlanNode[];
}

export interface ParsedPlan {
  root: PlanNode;
  planning_time_ms: number | null;
  execution_time_ms: number | null;
  settings: Record<string, string>;
  executed: boolean;
  fingerprint: string;
}

export interface Finding {
  id: number;
  rule_id: string;
  severity: Severity;
  title: string;
  summary: string;
  evidence: Record<string, unknown>;
  node_ids: string[];
  confidence: number;
  next_actions: string[];
}

export interface Candidate {
  id: number;
  candidate_key: string;
  table_name: string;
  ddl: string;
  columns: { name: string; desc: boolean }[];
  include: string[];
  predicate: string | null;
  expression: string | null;
  rationale: string[];
  source_rule_ids: string[];
  validation: Validation;
  planner_score: number | null;
  comparison: Record<string, unknown> | null;
  dropped: boolean;
  drop_reason: string | null;
  rank: number;
}

export interface RunStats {
  n: number;
  median_ms: number;
  p95_ms: number;
  mean_ms: number;
  stdev_ms: number;
  cv: number;
  min_ms: number;
  max_ms: number;
}

export interface RunSample {
  execution_ms: number;
  planning_ms: number | null;
  rows: number | null;
  shared_hit_blocks: number | null;
  shared_read_blocks: number | null;
  temp_blocks: number | null;
}

export interface BenchmarkResult {
  before: RunStats;
  after: RunStats;
  before_samples: RunSample[];
  after_samples: RunSample[];
  improvement_pct: number;
  index_size_bytes: number;
  index_build_ms: number;
  plan_fingerprint_before: string;
  plan_fingerprint_after: string;
  checksum_match: boolean;
  warnings: string[];
  pg_version: string;
  settings: Record<string, unknown>;
  dataset_snapshot: string;
  ddl: string;
}

export interface BenchmarkRun {
  id: number;
  analysis_id: number;
  candidate_id: number;
  status: AnalysisStatus;
  stage: string | null;
  config: Record<string, unknown>;
  result: BenchmarkResult | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Analysis {
  id: number;
  project_id: number;
  status: AnalysisStatus;
  stage: string | null;
  execute: boolean;
  error: string | null;
  sql: string;
  normalized_sql: string;
  fingerprint: string;
  params: unknown[];
  plan: ParsedPlan | null;
  planning_time_ms: number | null;
  execution_time_ms: number | null;
  findings: Finding[];
  candidates: Candidate[];
  benchmarks: BenchmarkRun[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AnalysisSummary {
  id: number;
  status: AnalysisStatus;
  execute: boolean;
  sql: string;
  fingerprint: string;
  execution_time_ms: number | null;
  finding_count: number;
  critical_count: number;
  best_validation: Validation | null;
  created_at: string;
}

export interface Project {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
}

export interface SampleQuery {
  id: string;
  title: string;
  sql: string;
  params: unknown[];
  description: string;
  expected_rule_ids: string[];
  expected_candidate_table: string | null;
  expected_candidate_columns: string[] | null;
}

export interface ProgressEvent {
  type: string;
  message?: string | null;
}

// Workload intelligence (apps/api/app/api/workload.py).
export interface WorkloadSnapshot {
  id: number;
  project_id: number;
  engine: string;
  source: string;
  taken_at: string;
  query_count: number;
  skipped_count: number;
}

export interface WorkloadQuery {
  fingerprint: string;
  normalized_sql: string;
  sample_sql: string;
  calls: number;
  total_ms: number;
  mean_ms: number;
  stddev_ms: number;
  min_ms: number;
  max_ms: number;
  rows: number;
  shared_hit_blocks: number;
  shared_read_blocks: number;
  temp_blocks: number;
  /** An ESTIMATE (mean + 1.645 standard deviations): pg_stat_statements keeps no percentiles. */
  p95_est_ms: number;
  tables: string[];
  rank: number;
  /** This query's share (0..1) of the snapshot's total execution time. */
  share: number;
}

export interface Regression {
  fingerprint: string;
  baseline_mean_ms: number;
  recent_mean_ms: number;
  ratio: number;
  recent_calls: number;
}

export interface WorkloadPanels {
  highest_total_time: WorkloadQuery[];
  slowest_p95: WorkloadQuery[];
  most_frequent: WorkloadQuery[];
  highest_io: WorkloadQuery[];
  most_unstable: WorkloadQuery[];
  largest_regression: Regression[];
  missing_validation: WorkloadQuery[];
}

export interface WorkloadDashboard {
  snapshot: WorkloadSnapshot | null;
  compare_to: WorkloadSnapshot | null;
  queries: WorkloadQuery[];
  panels: WorkloadPanels;
  regressions: Regression[];
}

export interface FingerprintHistory {
  fingerprint: string;
  normalized_sql: string;
  first_seen: string;
  last_seen: string;
  analysis_count: number;
  executed_count: number;
  mean_execution_ms: number | null;
  /** Measured over executed analyses (unlike the workload's estimated p95). */
  p95_execution_ms: number | null;
  plan_change_count: number;
  best_validation: Validation | null;
}

export interface PlanChange {
  before_analysis_id: number;
  after_analysis_id: number;
  before_fingerprint: string;
  after_fingerprint: string;
  before_operators: string[];
  after_operators: string[];
}

export interface WorkloadQueryDetail {
  fingerprint: string;
  normalized_sql: string;
  sample_sql: string;
  trend: { snapshot_id: number; taken_at: string; calls: number; total_ms: number; mean_ms: number; p95_est_ms: number }[];
  analyses: {
    id: number;
    created_at: string;
    plan_fingerprint: string | null;
    execution_time_ms: number | null;
    validation: Validation | null;
  }[];
  plan_changes: PlanChange[];
  sample: { query_id: string; title: string; sql: string; params: unknown[] } | null;
}
