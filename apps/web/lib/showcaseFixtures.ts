import captured from "./showcaseCaptured.json";
import type { BenchmarkResult, Finding, ParsedPlan } from "./types";
import type { PasteResult } from "./pasteExplain";
import { slowestNodeId } from "./planGraph";
interface Capture {
  plan: ParsedPlan; findings: Finding[]; sql: string; raw_plan: unknown;
  comparison: { cost_before: number; cost_after: number; uses_candidate: boolean } | null;
  benchmark: BenchmarkResult & { index_used: boolean };
  validated: boolean; captured_at: string; checksum_method: string;
}
export type RungStatus = "pass" | "fail" | "not-applicable";
export interface Rung { label: "Suggested" | "Planner-validated" | "Benchmark-validated"; status: RungStatus; detail: string }
const records = captured as unknown as Record<string, Capture>;
/** The verification ladder, derived only from what the recorded run measured. */
function ladderOf(capture: Capture): Rung[] {
  const b = capture.benchmark, mongo = capture.plan.engine === "mongodb", cmp = capture.comparison;
  const planner: Rung = mongo
    ? { label: "Planner-validated", status: "not-applicable", detail: "MongoDB has no hypothetical indexes, so an idea can only be measured." }
    : cmp?.uses_candidate
      ? { label: "Planner-validated", status: "pass", detail: `HypoPG: the planner picks the index (estimated cost ${cmp.cost_before} → ${cmp.cost_after}).` }
      : { label: "Planner-validated", status: "fail", detail: "HypoPG: the planner did not pick the index." };
  const speed = `median ${b.before.median_ms.toFixed(3)} → ${b.after.median_ms.toFixed(3)} ms (${b.improvement_pct.toFixed(1)}%)`;
  const reasons = b.index_used ? "the improvement is below the validation threshold" : "the new index was not used by the query's final plan";
  const bench: Rung = capture.validated
    ? { label: "Benchmark-validated", status: "pass", detail: `Isolated clone: ${speed}; output checksum identical.` }
    : { label: "Benchmark-validated", status: "fail", detail: `Isolated clone: ${speed}. Rejected: ${reasons}, so the planner's promise did not hold.` };
  return [{ label: "Suggested", status: "pass", detail: `Derived from the plan: ${b.ddl}` }, planner, bench];
}
const definitions = [
  { id: "scan", title: "🟢 The 97% Scan Waste", note: "250k rows; 97% discarded. A composite (customer_id, created_at) index supplies the first 100 matching rows in order." },
  { id: "sort", title: "🟡 The Sort Spill", note: "With work_mem=64kB, the external disk sort is replaced by an ordered B-tree scan. Unique timestamps preserve the LIMIT result." },
  { id: "lookup", title: "🔴 Unindexed MongoDB $lookup", note: "An index on the foreign customer_id field removes repeated collection scans. MongoDB has no HypoPG stage." },
  { id: "rejected", title: "⚪ The False Positive (Rejected Index)", note: "Stale statistics predict a selective lookup. After refreshing statistics in the benchmark clone, the index is not useful. Planner evidence does not earn benchmark validation." },
];
export const showcaseFixtures = definitions.map(example => {
  const capture = records[example.id];
  const benchmark = capture.benchmark;
  const slowest = slowestNodeId(capture.plan);
  const highlight: Finding = { id: -1, rule_id: "showcase_bottleneck", severity: "critical", title: "Largest measured operator time", summary: "Recorded execution plan bottleneck.", evidence: {}, node_ids: slowest ? [slowest] : [], confidence: 1, next_actions: [] };
  const analysis: PasteResult = { plan: capture.plan, findings: [...capture.findings, highlight], suggestions: [], warnings: benchmark.warnings };
  const before = benchmark.before.median_ms, after = benchmark.after.median_ms;
  return { ...example, analysis, capture, benchmark, ladder: ladderOf(capture),
    description: `Recorded median: ${before.toFixed(3)} → ${after.toFixed(3)} ms (${benchmark.improvement_pct.toFixed(1)}% improvement).`,
    ddl: benchmark.ddl,
    before: benchmark.before_samples.map(s => s.execution_ms), after: benchmark.after_samples.map(s => s.execution_ms),
    cost: capture.comparison ? [capture.comparison.cost_before, capture.comparison.cost_after] : null,
    verdict: capture.validated ? "Benchmark-Validated" : capture.comparison?.uses_candidate ? "Planner-Validated · Benchmark rejected" : "Suggested · Benchmark rejected",
    provenance: `Recorded ${capture.captured_at.slice(0, 10)} using the QueryDoctor pipeline on ${benchmark.pg_version}. Dataset ${benchmark.dataset_snapshot}. Timings depend on hardware and data.`,
  };
});
