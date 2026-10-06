import Link from "next/link";
import { VALIDATION_LABELS } from "@/components/candidates/ValidationBadge";
import { formatMs } from "@/lib/format";
import type { FingerprintHistory } from "@/lib/types";

const NUM = "px-3 py-2 text-right font-mono text-[13px] tabular-nums";
const HEAD = "px-3 py-2 text-xs font-semibold text-ink-2";

/** One row per query shape across all of its analyses: how often it was analysed, how it performed, how far evidence got. */
export function FingerprintTable({ fingerprints }: { fingerprints: FingerprintHistory[] }) {
  if (fingerprints.length === 0) return <p className="text-sm text-ink-2">No analyses yet. Run one from New analysis.</p>;
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead className="border-b border-line bg-surface-2 text-left">
          <tr>
            <th scope="col" className={HEAD}>
              Query
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Analyses
            </th>
            <th scope="col" className={`${HEAD} text-right`} title="Mean execution time over analyses that ran the query">
              Mean time
            </th>
            <th scope="col" className={`${HEAD} text-right`} title="Measured over analyses that ran the query">
              Measured p95
            </th>
            <th scope="col" className={HEAD}>
              Plan changes
            </th>
            <th scope="col" className={HEAD}>
              Evidence
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {fingerprints.map((f) => (
            <tr key={f.fingerprint} className="hover:bg-surface-2">
              <td className="max-w-[420px] px-3 py-2">
                <Link href={`/workload/${f.fingerprint}`} title={f.normalized_sql} className="block truncate font-mono text-[13px] text-accent hover:underline">
                  {f.normalized_sql}
                </Link>
              </td>
              <td className={NUM}>{f.analysis_count}</td>
              <td className={NUM}>{formatMs(f.mean_execution_ms)}</td>
              <td className={NUM}>{formatMs(f.p95_execution_ms)}</td>
              <td className="px-3 py-2 text-xs text-ink-2">
                {f.plan_change_count > 0 ? `${f.plan_change_count} plan change${f.plan_change_count === 1 ? "" : "s"}` : "no plan changes"}
              </td>
              <td className="px-3 py-2 text-xs">{f.best_validation ? VALIDATION_LABELS[f.best_validation] : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
