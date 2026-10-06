"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ModelMeta } from "@/lib/types";

const SHORT: Record<string, string> = { heuristic: "Heuristic", logistic: "Logistic", lgbm_regressor: "Regressor", lgbm_classifier: "Classifier", lgbm_ranker: "LambdaRank" };

/** Held-out ranking quality of every model: the heuristic against each learned one. */
export function MetricsChart({ models }: { models: ModelMeta["models"] }) {
  const data = Object.entries(models).map(([name, m]) => ({
    name: SHORT[name] ?? name,
    ndcg: Number(m.holdout.ndcg_at_3.toFixed(3)),
    precision: Number(m.holdout.precision_at_3.toFixed(3)),
  }));
  return (
    <figure className="grid gap-1">
      <div className="h-64 w-full" role="img" aria-label="Held-out NDCG@3 and precision@3 of each model, from 0 to 1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="name" tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
            <YAxis domain={[0, 1]} tick={{ fill: "var(--ink-2)", fontSize: 12 }} width={36} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
            <Legend verticalAlign="top" height={28} />
            <Bar name="NDCG@3" dataKey="ndcg" fill="var(--accent)" isAnimationActive={false} />
            <Bar name="Precision@3" dataKey="precision" fill="var(--heat-1)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-ink-2">Measured on query shapes the models never saw. Higher is better.</figcaption>
    </figure>
  );
}
