import Link from "next/link";
import { formatFactor, formatMs, formatNumber } from "@/lib/format";
import type { Regression, WorkloadQuery } from "@/lib/types";

interface Props {
  regressions: Regression[];
  queries: WorkloadQuery[];
  /** Whether there was an earlier snapshot to compare against. */
  comparing: boolean;
}

export function RegressionList({ regressions, queries, comparing }: Props) {
  if (!comparing) return <p className="text-sm text-ink-2">Import a second snapshot to compare against the first.</p>;
  if (regressions.length === 0) return <p className="text-sm text-ink-2">No query got slower between these two snapshots.</p>;
  const text = new Map(queries.map((q) => [q.fingerprint, q.normalized_sql]));
  return (
    <ul className="divide-y divide-line rounded-md border border-line bg-surface">
      {regressions.map((r) => (
        <li key={r.fingerprint} className="grid gap-1 px-3 py-2.5">
          <Link href={`/workload/${r.fingerprint}`} className="truncate font-mono text-[13px] text-accent hover:underline">
            {text.get(r.fingerprint) ?? r.fingerprint.slice(0, 8)}
          </Link>
          <span className="text-sm">
            was {formatMs(r.baseline_mean_ms)}, now {formatMs(r.recent_mean_ms)} ({formatFactor(r.ratio)} slower)
            <span className="text-ink-2"> · {formatNumber(r.recent_calls)} calls since the previous import</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
