import type { FixQueue } from "@/lib/types";

export function savings(hours: number) {
  if (hours >= 1) return `${hours.toFixed(1)} hours`;
  if (hours >= 1 / 60) return `${(hours * 60).toFixed(1)} minutes`;
  return `${(hours * 3600).toFixed(1)} seconds`;
}

/** The ranked list of Benchmark-Validated indexes and the time they would recover. Shared by the live and recorded views. */
export function FixQueueSummary({ queue, evidenceLinks = true }: { queue: FixQueue | null | undefined; evidenceLinks?: boolean }) {
  if (!queue || queue.fixes.length === 0) {
    return <p className="mt-2 text-sm text-ink-2">No qualifying benchmark evidence yet. Analyze the busiest sample queries and benchmark their promising indexes.</p>;
  }
  return <>
    <p className="mt-2 font-medium">{queue.fixes.length} Benchmark-Validated indexes could recover approximately {queue.hours_per_day !== null ? `${savings(queue.hours_per_day)} of query execution time/day` : `${savings(queue.saved_hours_in_window)} over the captured workload`}.</p>
    <p className="mt-1 text-sm text-ink-2">{queue.note}</p>
    <ol className="mt-3 grid gap-3">{queue.fixes.map(fix => <li key={fix.ddl} className="rounded border border-line p-3">
      <code className="break-all text-sm">{fix.ddl}</code>
      <p className="text-sm text-ink-2">Helps {fix.fingerprints.length} query {fix.fingerprints.length === 1 ? "shape" : "shapes"} · {fix.hours_per_day !== null ? `${savings(fix.hours_per_day)} estimated/day` : "Daily rate unavailable"}</p>
      {evidenceLinks && <a href={`/analyses/${fix.analysis_ids[0]}`} className="text-sm text-accent underline">Inspect benchmark evidence</a>}
    </li>)}</ol>
  </>;
}
