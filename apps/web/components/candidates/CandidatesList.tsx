import type { ReactNode } from "react";
import type { Candidate } from "@/lib/types";
import { CandidateCard } from "./CandidateCard";

interface Props {
  candidates: Candidate[];
  onApprove?: (candidate: Candidate) => void;
  busyCandidateId?: number | null;
  /** The analysis the candidates belong to; enables feedback buttons. */
  analysisId?: number;
  /** "mongodb" words each card's benchmark confirmation in MongoDB terms. */
  engine?: string;
  /** Rendered under each recommended card (the benchmark panel). */
  renderExtra?: (candidate: Candidate) => ReactNode;
}

export function CandidatesList({ candidates, onApprove, busyCandidateId = null, renderExtra, analysisId, engine }: Props) {
  const recommended = candidates.filter((c) => !c.dropped).sort((a, b) => a.rank - b.rank);
  const ruledOut = candidates.filter((c) => c.dropped);

  if (recommended.length === 0 && ruledOut.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        No index ideas for this query. It may not filter, join or sort on columns an index could help with.
      </p>
    );
  }

  return (
    <div className="grid gap-4">
      {recommended.length === 0 && <p className="text-sm text-ink-2">No index idea survived testing for this query.</p>}
      {recommended.map((c) => (
        <div key={c.id} className="grid gap-2">
          <CandidateCard candidate={c} onApprove={onApprove} busy={busyCandidateId === c.id} analysisId={analysisId} engine={engine} />
          {renderExtra?.(c)}
        </div>
      ))}

      {ruledOut.length > 0 && (
        <details className="rounded-md border border-line bg-surface p-3">
          <summary className="cursor-pointer text-sm font-medium">Ideas we ruled out ({ruledOut.length})</summary>
          <ul className="mt-3 grid gap-3">
            {ruledOut.map((c) => (
              <li key={c.id} className="grid gap-1">
                <code className="break-words font-mono text-[13px]">{c.ddl}</code>
                <span className="text-sm text-ink-2">{c.drop_reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
