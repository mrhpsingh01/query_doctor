import Link from "next/link";
import { formatMs, formatNumber } from "@/lib/format";
import type { Engine, WorkloadQuery } from "@/lib/types";
import { ShareBar } from "./ShareBar";

const NUM = "px-3 py-2 text-right font-mono text-[13px] tabular-nums";
const HEAD = "px-3 py-2 text-xs font-semibold text-ink-2";

export function analyzeHref(sql: string, engine: Engine = "postgres"): string {
  const base = `/analyses/new?sql=${encodeURIComponent(sql)}`;
  return engine === "postgres" ? base : `${base}&engine=${engine}`;
}

/** The workload ranked by total database time (mean x calls): the order to optimize in. */
export function QueryTable({ queries, engine = "postgres" }: { queries: WorkloadQuery[]; engine?: Engine }) {
  if (queries.length === 0) return <p className="text-sm text-ink-2">No queries in this snapshot.</p>;
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead className="border-b border-line bg-surface-2 text-left">
          <tr>
            <th scope="col" className={`${HEAD} w-10 text-right`}>
              #
            </th>
            <th scope="col" className={HEAD}>
              Query
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Calls
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Mean
            </th>
            <th
              scope="col"
              className={`${HEAD} text-right`}
              title={
                engine === "mongodb"
                  ? "Estimated as mean + 1.645 standard deviations of the profiled calls. The profiler records whole milliseconds; a measured p95 comes only from a QueryDoctor benchmark."
                  : "Estimated as mean + 1.645 standard deviations. pg_stat_statements keeps no percentiles; a measured p95 comes only from a QueryDoctor benchmark."
              }
            >
              Estimated p95
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Total time
            </th>
            <th scope="col" className={HEAD}>
              Share of total
            </th>
            <th scope="col" className={HEAD}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {queries.map((q) => (
            <tr key={q.fingerprint} className="hover:bg-surface-2">
              <td className={`${NUM} text-ink-2`}>{q.rank}</td>
              <td className="max-w-[420px] px-3 py-2">
                <Link href={`/workload/${q.fingerprint}`} title={q.normalized_sql} className="block truncate font-mono text-[13px] text-accent hover:underline">
                  {q.normalized_sql}
                </Link>
                {q.tables.length > 0 && <span className="text-xs text-ink-2">{q.tables.join(", ")}</span>}
              </td>
              <td className={NUM}>{formatNumber(q.calls)}</td>
              <td className={NUM}>{formatMs(q.mean_ms)}</td>
              <td className={NUM}>{formatMs(q.p95_est_ms)}</td>
              <td className={NUM}>{formatMs(q.total_ms)}</td>
              <td className="px-3 py-2">
                <ShareBar share={q.share} />
              </td>
              <td className="px-3 py-2 text-right">
                <Link href={analyzeHref(q.sample_sql, engine)} className="text-sm font-medium text-accent hover:underline">
                  Analyze
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
