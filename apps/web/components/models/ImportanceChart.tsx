"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const TOP = 12;

function readable(feature: string): string {
  return feature.replace(/^[a-z]+_/, "").replace(/_/g, " ");
}

/** How much each feature contributed to the speedup model's splits (share of total gain). */
export function ImportanceChart({ importances }: { importances: Record<string, Record<string, number>> }) {
  const source = importances.lgbm_regressor ?? Object.values(importances)[0] ?? {};
  const data = Object.entries(source)
    .sort((a, b) => b[1] - a[1])
    .slice(0, TOP)
    .map(([feature, share]) => ({ feature, label: readable(feature), share: Number((share * 100).toFixed(1)) }));
  if (data.length === 0) return <p className="text-sm text-ink-2">No feature importances were recorded.</p>;
  return (
    <figure className="grid gap-1">
      <div className="w-full" style={{ height: 28 * data.length + 40 }} role="img" aria-label="Share of the speedup model's gain from each of the most important features">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" unit="%" tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
            <YAxis type="category" dataKey="label" width={150} tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
            <Tooltip formatter={(v) => `${v}%`} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
            <Bar dataKey="share" fill="var(--accent)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-ink-2">The {data.length} most influential of the model&apos;s features.</figcaption>
    </figure>
  );
}
