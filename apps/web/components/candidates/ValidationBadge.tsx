import type { Validation } from "@/lib/types";

// The three levels of evidence behind a recommendation. They are deliberately visible and distinct: a suggestion
// from the query's shape is not the same claim as "the planner picks it" or "it was measured faster".
const LEVELS: Record<Validation, { label: string; explanation: string; style: string }> = {
  suggested: {
    label: "Suggested",
    explanation: "Derived from the query's shape. Not tested yet.",
    style: "border border-line text-ink-2",
  },
  planner_validated: {
    label: "Planner-validated",
    explanation: "PostgreSQL's planner picks this index in a what-if test. Not yet measured.",
    style: "bg-accent-soft text-accent border border-accent",
  },
  benchmark_validated: {
    label: "Benchmark-validated",
    explanation: "Measured faster in an isolated copy of the database.",
    style: "bg-heat-0 text-accent-ink border border-heat-0",
  },
};

export function ValidationBadge({ validation }: { validation: Validation }) {
  const level = LEVELS[validation];
  return (
    <div className="grid gap-0.5">
      <span
        data-validation={validation}
        className={`inline-block w-fit rounded px-2 py-0.5 text-xs font-semibold ${level.style}`}
      >
        {level.label}
      </span>
      <span className="text-xs text-ink-2">{level.explanation}</span>
    </div>
  );
}
