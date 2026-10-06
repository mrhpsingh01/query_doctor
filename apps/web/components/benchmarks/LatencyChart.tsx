"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatMs } from "@/lib/format";
import type { BenchmarkResult } from "@/lib/types";

export interface ChartRow {
  run: number;
  original: number | null;
  indexed: number | null;
}

/** One row per measured run, original and indexed side by side (null where a side has fewer runs). */
export function chartData(result: BenchmarkResult): ChartRow[] {
  const rows = Math.max(result.before_samples.length, result.after_samples.length);
  return Array.from({ length: rows }, (_, i) => ({
    run: i + 1,
    original: result.before_samples[i]?.execution_ms ?? null,
    indexed: result.after_samples[i]?.execution_ms ?? null,
  }));
}

/** Every measured run as a point, with the medians marked: variance and the size of the gap are both visible. */
export function LatencyChart({ result }: { result: BenchmarkResult }) {
  const data = chartData(result);
  return (
    <figure className="grid gap-1">
      <div className="h-56 w-full" role="img" aria-label={`Execution time of each run. Median ${formatMs(result.before.median_ms)} without the index, ${formatMs(result.after.median_ms)} with it.`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="run" tick={{ fill: "var(--ink-2)", fontSize: 12 }} label={{ value: "Run", position: "insideBottom", offset: -2, fill: "var(--ink-2)", fontSize: 12 }} />
            <YAxis tick={{ fill: "var(--ink-2)", fontSize: 12 }} tickFormatter={(v: number) => formatMs(v)} width={64} domain={[0, "auto"]} />
            <Tooltip formatter={(v) => formatMs(Number(v))} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
            <Legend verticalAlign="top" height={28} />
            <ReferenceLine y={result.before.median_ms} stroke="var(--heat-2)" strokeDasharray="5 4" />
            <ReferenceLine y={result.after.median_ms} stroke="var(--accent)" strokeDasharray="5 4" />
            <Line name="Original" dataKey="original" stroke="var(--heat-2)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
            <Line name="With index" dataKey="indexed" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-ink-2">Dashed lines mark each side&apos;s median.</figcaption>
    </figure>
  );
}
