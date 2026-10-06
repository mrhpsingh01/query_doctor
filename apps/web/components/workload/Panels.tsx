import Link from "next/link";
import { formatFactor, formatMs, formatNumber } from "@/lib/format";
import type { Regression, WorkloadPanels, WorkloadQuery } from "@/lib/types";

const PANEL_ITEMS = 5;

const blocks = (n: number) => `${formatNumber(n)} block${n === 1 ? "" : "s"}`;

interface Props {
  panels: WorkloadPanels;
  /** The ranked queries, used to show a regression's query text. */
  queries: WorkloadQuery[];
}

function QueryItem({ q, metric }: { q: WorkloadQuery; metric: string }) {
  return (
    <li className="flex items-baseline justify-between gap-3 py-1.5">
      <Link href={`/workload/${q.fingerprint}`} title={q.normalized_sql} className="min-w-0 flex-1 truncate font-mono text-[13px] text-accent hover:underline">
        {q.normalized_sql}
      </Link>
      <span className="shrink-0 font-mono text-xs text-ink-2">{metric}</span>
    </li>
  );
}

function Panel({ id, title, hint, empty, children, count }: { id: string; title: string; hint: string; empty: string; children: React.ReactNode; count: number }) {
  return (
    <section aria-labelledby={id} className="rounded-md border border-line bg-surface p-3">
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      <p className="mb-1 text-xs text-ink-2">{hint}</p>
      {count === 0 ? <p className="py-2 text-sm text-ink-2">{empty}</p> : <ul className="divide-y divide-line">{children}</ul>}
    </section>
  );
}

export function Panels({ panels, queries }: Props) {
  const text = new Map(queries.map((q) => [q.fingerprint, q.normalized_sql]));
  const top = <T,>(items: T[]) => items.slice(0, PANEL_ITEMS);
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <Panel id="p-total" title="Highest total time" hint="Mean time × calls: the most database time spent." empty="No queries yet." count={panels.highest_total_time.length}>
        {top(panels.highest_total_time).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={`${formatMs(q.total_ms)} total`} />
        ))}
      </Panel>
      <Panel id="p-p95" title="Slowest queries (estimated p95)" hint="Estimated from the mean and spread; not a measured percentile." empty="No queries yet." count={panels.slowest_p95.length}>
        {top(panels.slowest_p95).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={`${formatMs(q.p95_est_ms)} est.`} />
        ))}
      </Panel>
      <Panel id="p-freq" title="Most frequent" hint="Called the most often." empty="No queries yet." count={panels.most_frequent.length}>
        {top(panels.most_frequent).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={`${formatNumber(q.calls)} calls`} />
        ))}
      </Panel>
      <Panel id="p-io" title="Highest I/O" hint="Blocks read from disk plus temporary-file blocks." empty="No query read from disk or used temporary files." count={panels.highest_io.length}>
        {top(panels.highest_io).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={blocks(q.shared_read_blocks + q.temp_blocks)} />
        ))}
      </Panel>
      <Panel id="p-unstable" title="Most unstable" hint="Run time varies most compared with its mean (at least 20 calls)." empty="No query with enough calls varies noticeably." count={panels.most_unstable.length}>
        {top(panels.most_unstable).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={`std dev ${formatFactor(q.mean_ms > 0 ? q.stddev_ms / q.mean_ms : 0)} the mean`} />
        ))}
      </Panel>
      <Panel id="p-regression" title="Largest regression" hint="Slower since the previous import." empty="No query got slower since the previous import." count={panels.largest_regression.length}>
        {top(panels.largest_regression).map((r: Regression) => (
          <li key={r.fingerprint} className="flex items-baseline justify-between gap-3 py-1.5">
            <Link href={`/workload/${r.fingerprint}`} className="min-w-0 flex-1 truncate font-mono text-[13px] text-accent hover:underline">
              {text.get(r.fingerprint) ?? r.fingerprint.slice(0, 8)}
            </Link>
            <span className="shrink-0 font-mono text-xs text-heat-3">{formatFactor(r.ratio)} slower</span>
          </li>
        ))}
      </Panel>
      <Panel id="p-missing" title="Missing benchmark validation" hint="No index idea for these has been measured in a benchmark yet." empty="Every query has benchmark evidence." count={panels.missing_validation.length}>
        {top(panels.missing_validation).map((q) => (
          <QueryItem key={q.fingerprint} q={q} metric={`${formatMs(q.total_ms)} total`} />
        ))}
      </Panel>
    </div>
  );
}
