"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatMs } from "@/lib/format";
import type { WorkloadQueryDetail } from "@/lib/types";

/** Cumulative mean and estimated p95 at each imported snapshot. */
export function TrendChart({ trend }: { trend: WorkloadQueryDetail["trend"] }) {
  const data = trend.map((t, i) => ({ n: i + 1, mean: t.mean_ms, p95: t.p95_est_ms }));
  return (
    <figure className="grid gap-1">
      <div className="h-56 w-full" role="img" aria-label="Mean and estimated p95 time of this query at each import">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="n" tick={{ fill: "var(--ink-2)", fontSize: 12 }} label={{ value: "Import", position: "insideBottom", offset: -2, fill: "var(--ink-2)", fontSize: 12 }} />
            <YAxis tick={{ fill: "var(--ink-2)", fontSize: 12 }} tickFormatter={(v: number) => formatMs(v)} width={64} domain={[0, "auto"]} />
            <Tooltip formatter={(v) => formatMs(Number(v))} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
            <Legend verticalAlign="top" height={28} />
            <Line name="Mean" dataKey="mean" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            <Line name="Estimated p95" dataKey="p95" stroke="var(--heat-2)" strokeWidth={2} strokeDasharray="5 4" dot={{ r: 3 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-ink-2">Cumulative since the statistics were last reset. The p95 is estimated, not measured.</figcaption>
    </figure>
  );
}
