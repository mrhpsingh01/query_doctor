import Link from "next/link";
import { VALIDATION_LABELS } from "@/components/candidates/ValidationBadge";
import { formatMs } from "@/lib/format";
import type { WorkloadQueryDetail } from "@/lib/types";
import { TrendChart } from "./TrendChart";

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function analyzeLink(detail: WorkloadQueryDetail): string {
  const sql = detail.sample?.sql ?? detail.sample_sql;
  // A MongoDB query is JSON text; PostgreSQL never starts with a brace.
  const engine = detail.sample?.engine ?? (sql.trimStart().startsWith("{") ? "mongodb" : "postgres");
  const base = `/analyses/new?sql=${encodeURIComponent(sql)}`;
  const withParams = detail.sample ? `${base}&params=${encodeURIComponent(JSON.stringify(detail.sample.params))}` : base;
  return engine === "mongodb" ? `${withParams}&engine=mongodb` : withParams;
}

/** One query shape: its workload trend, every analysis of it, and any change of plan between analyses. */
export function QueryDetailView({ detail }: { detail: WorkloadQueryDetail }) {
  return (
    <div className="grid gap-8">
      <header className="grid gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Query {detail.fingerprint.slice(0, 8)}</h1>
        <pre className="overflow-x-auto rounded-md border border-line bg-surface p-3 font-mono text-[13px]">{detail.normalized_sql}</pre>
        <div className="flex flex-wrap items-center gap-4">
          <Link href={analyzeLink(detail)} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink">
            Analyze this query
          </Link>
          {detail.sample && (
            <p className="text-sm text-ink-2">
              Matches the sample &ldquo;{detail.sample.title}&rdquo;. Sample parameters: <code className="font-mono">{JSON.stringify(detail.sample.params)}</code>
            </p>
          )}
        </div>
      </header>

      <section aria-labelledby="trend-h" className="grid gap-3">
        <h2 id="trend-h" className="text-lg font-semibold">
          Trend across imports
        </h2>
        {detail.trend.length === 0 ? (
          <p className="text-sm text-ink-2">This query is not in any imported snapshot yet. Import statistics from the Workload page.</p>
        ) : (
          <>
            <TrendChart trend={detail.trend} />
            <ul className="divide-y divide-line rounded-md border border-line bg-surface text-sm">
              {detail.trend.map((t) => (
                <li key={t.snapshot_id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                  <span className="text-ink-2">{when(t.taken_at)}</span>
                  <span className="font-mono text-[13px]">
                    {t.calls.toLocaleString("en-US")} calls · mean {formatMs(t.mean_ms)} · est. p95 {formatMs(t.p95_est_ms)}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section aria-labelledby="plans-h" className="grid gap-3">
        <h2 id="plans-h" className="text-lg font-semibold">
          Plan changes
        </h2>
        {detail.plan_changes.length === 0 ? (
          <p className="text-sm text-ink-2">The plan has not changed between analyses of this query.</p>
        ) : (
          <ul className="grid gap-2">
            {detail.plan_changes.map((c) => (
              <li key={`${c.before_analysis_id}-${c.after_analysis_id}`} className="rounded-md border border-line bg-surface px-3 py-2 text-sm">
                <p className="text-ink-2">
                  Between{" "}
                  <Link href={`/analyses/${c.before_analysis_id}`} className="text-accent hover:underline">
                    analysis {c.before_analysis_id}
                  </Link>{" "}
                  and{" "}
                  <Link href={`/analyses/${c.after_analysis_id}`} className="text-accent hover:underline">
                    analysis {c.after_analysis_id}
                  </Link>
                </p>
                <p className="font-mono text-[13px]">
                  {c.before_operators.join(" → ") || "unknown"} <span className="text-ink-2">became</span> {c.after_operators.join(" → ") || "unknown"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="analyses-h" className="grid gap-3">
        <h2 id="analyses-h" className="text-lg font-semibold">
          Analyses of this query
        </h2>
        {detail.analyses.length === 0 ? (
          <p className="text-sm text-ink-2">Not analysed yet. Use &ldquo;Analyze this query&rdquo; above.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line bg-surface">
            {detail.analyses.map((a) => (
              <li key={a.id}>
                <Link href={`/analyses/${a.id}`} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-sm hover:bg-surface-2">
                  <span>
                    Analysis {a.id} <span className="text-ink-2">· {when(a.created_at)}</span>
                  </span>
                  <span className="flex items-center gap-3 text-xs text-ink-2">
                    {a.execution_time_ms !== null && <span className="font-mono">{formatMs(a.execution_time_ms)}</span>}
                    {a.validation && <span>{VALIDATION_LABELS[a.validation]}</span>}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
