"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ReliabilityPoint } from "@/lib/types";

/** Reliability diagram: when the model says 80%, how often was it right? The diagonal is perfect calibration. */
export function CalibrationChart({ raw, calibrated }: { raw: ReliabilityPoint[]; calibrated: ReliabilityPoint[] }) {
  const rows = (points: ReliabilityPoint[]) => points.map((p) => ({ predicted: Number(p.mean_pred.toFixed(3)), observed: Number(p.frac_pos.toFixed(3)) }));
  if (raw.length === 0 && calibrated.length === 0) return <p className="text-sm text-ink-2">No held-out probabilities to plot.</p>;
  return (
    <figure className="grid gap-1">
      <div className="h-64 w-full" role="img" aria-label="Predicted probability of a useful index against how often it was useful, before and after calibration">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart margin={{ top: 10, right: 16, bottom: 18, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis type="number" dataKey="predicted" domain={[0, 1]} tick={{ fill: "var(--ink-2)", fontSize: 12 }} label={{ value: "Predicted probability", position: "insideBottom", offset: -8, fill: "var(--ink-2)", fontSize: 12 }} />
            <YAxis type="number" dataKey="observed" domain={[0, 1]} tick={{ fill: "var(--ink-2)", fontSize: 12 }} width={36} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
            <Legend verticalAlign="top" height={28} />
            <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--ink-2)" strokeDasharray="5 4" />
            <Line name="Raw" data={rows(raw)} dataKey="observed" stroke="var(--heat-2)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            <Line name="Calibrated" data={rows(calibrated)} dataKey="observed" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-ink-2">Each point is a group of predictions; the dashed diagonal is perfect calibration.</figcaption>
    </figure>
  );
}
