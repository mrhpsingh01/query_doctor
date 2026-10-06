"use client";

import { useId, useState } from "react";
import { formatPercent, formatRows } from "@/lib/format";
import type { Candidate } from "@/lib/types";
import { FeedbackButtons } from "./FeedbackButtons";
import { PredictionBlock } from "./PredictionBlock";
import { ValidationBadge } from "./ValidationBadge";

interface Props {
  candidate: Candidate;
  /** When supplied, the card offers a benchmark behind an explicit confirmation. */
  onApprove?: (candidate: Candidate) => void;
  /** True while a benchmark for this candidate is already queued or running. */
  busy?: boolean;
  /** When supplied, the card offers thumbs up/down feedback on the idea. */
  analysisId?: number;
}

function planChange(c: Candidate): string | null {
  const cmp = c.comparison as
    | { removed_operators: string[]; added_operators: string[]; cost_before: number; cost_after: number; cost_reduction: number }
    | null;
  if (!cmp) return null;
  const operators =
    cmp.removed_operators.length || cmp.added_operators.length
      ? `${cmp.removed_operators.join(", ") || "nothing"} → ${cmp.added_operators.join(", ") || "nothing"}`
      : "The operators stay the same";
  return `${operators}. Estimated cost ${formatRows(cmp.cost_before)} → ${formatRows(cmp.cost_after)} (${formatPercent(
    -cmp.cost_reduction,
  ).replace("-", "−")}).`;
}

export function CandidateCard({ candidate, onApprove, busy = false, analysisId }: Props) {
  const [confirmed, setConfirmed] = useState(false);
  const confirmId = useId();
  const score = candidate.planner_score && candidate.comparison ? Math.round(candidate.planner_score * 100) : null;
  const change = planChange(candidate);

  return (
    <article className="grid gap-3 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <ValidationBadge validation={candidate.validation} />
        {score !== null && (
          <div className="min-w-32">
            <div className="flex justify-between text-xs text-ink-2">
              <span>Planner score</span>
              <span className="font-mono text-ink">{score}</span>
            </div>
            <div
              role="meter"
              aria-label="Planner score"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={score}
              className="mt-1 h-1.5 w-full overflow-hidden rounded bg-surface-2"
            >
              <div className="h-full bg-accent" style={{ width: `${score}%` }} />
            </div>
          </div>
        )}
      </div>

      <pre className="overflow-x-auto rounded bg-surface-2 p-2.5 font-mono text-[13px] leading-snug">{candidate.ddl}</pre>

      {change && <p className="text-sm">{change}</p>}

      {candidate.rationale.length > 0 && (
        <div className="grid gap-0.5 text-sm text-ink-2">
          {candidate.rationale.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      )}

      <PredictionBlock prediction={candidate.prediction} />
      {analysisId !== undefined && <FeedbackButtons analysisId={analysisId} candidateId={candidate.id} />}

      {onApprove && (
        <div className="grid gap-2 border-t border-line pt-3">
          <label htmlFor={confirmId} className="flex items-start gap-2 text-sm">
            <input id={confirmId} type="checkbox" className="mt-1" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            <span>
              I understand this creates a real index in an isolated copy of the database, to time the query before and
              after. The database being analyzed is never changed.
            </span>
          </label>
          <div>
            <button
              type="button"
              disabled={!confirmed || busy}
              onClick={() => onApprove(candidate)}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Benchmark running…" : "Run benchmark"}
            </button>
          </div>
        </div>
      )}
    </article>
  );
}
