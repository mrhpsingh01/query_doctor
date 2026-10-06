import { formatPercent } from "@/lib/format";

/** A query's share of the snapshot's total database time, as a bar plus the number (so it never relies on colour). */
export function ShareBar({ share }: { share: number }) {
  const percent = Math.round(share * 1000) / 10;
  return (
    <div className="flex items-center gap-2">
      <div
        role="meter"
        aria-label="Share of total database time"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={formatPercent(share)}
        className="h-2 w-24 overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
      <span className="w-12 font-mono text-xs text-ink-2">{formatPercent(share)}</span>
    </div>
  );
}
