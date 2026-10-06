import { formatNumber, formatPercent } from "@/lib/format";

function label(key: string): string {
  return key.replace(/_/g, " ");
}

function value(key: string, v: unknown): string {
  if (typeof v === "number") {
    return /waste|share/.test(key) && v >= 0 && v <= 1 ? formatPercent(v) : formatNumber(v);
  }
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v === null || v === undefined) return "n/a";
  return String(v);
}

/** The measurements behind a finding, as a definition list. */
export function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence).filter(([, v]) => v !== null && v !== undefined);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 font-mono text-xs">
      {entries.map(([key, v]) => (
        <div key={key} className="contents">
          <dt className="text-ink-2">{label(key)}</dt>
          <dd className="break-words">{value(key, v)}</dd>
        </div>
      ))}
    </dl>
  );
}
