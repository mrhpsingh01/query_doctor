import Link from "next/link";
import type { AnalysisSummary } from "@/lib/types";

const STATUS_STYLE: Record<string, string> = {
  completed: "text-heat-0",
  failed: "text-heat-3",
  cancelled: "text-ink-2",
  running: "text-accent",
  queued: "text-ink-2",
};

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function AnalysisList({ analyses, empty }: { analyses: AnalysisSummary[]; empty: string }) {
  if (analyses.length === 0) return <p className="text-sm text-ink-2">{empty}</p>;
  return (
    <ul className="divide-y divide-line rounded-md border border-line bg-surface">
      {analyses.map((a) => (
        <li key={a.id}>
          <Link href={`/analyses/${a.id}`} className="grid gap-1 px-3 py-2.5 hover:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
            <span className="truncate font-mono text-[13px]">{a.sql}</span>
            <span className="flex items-center gap-3 text-xs text-ink-2">
              <span className={STATUS_STYLE[a.status] ?? ""}>{a.status}</span>
              {a.critical_count > 0 && <span className="text-heat-3">{a.critical_count} critical</span>}
              {a.finding_count > 0 && a.critical_count === 0 && <span>{a.finding_count} findings</span>}
              <span>{when(a.created_at)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
