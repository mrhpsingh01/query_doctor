import { formatBytes, formatFactor, formatPercent } from "@/lib/format";
import type { Prediction } from "@/lib/types";

function speedup(x: number): string {
  return x >= 1 ? `~${formatFactor(x)} faster` : `~${formatFactor(1 / x)} slower`;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * What a model trained on past benchmarks expects of an index idea. Deliberately its own block beside, never inside, the
 * evidence badge: a prediction is advice, only a benchmark is evidence.
 */
export function PredictionBlock({ prediction }: { prediction: Prediction | null }) {
  if (!prediction) return null;
  return (
    <section data-prediction aria-labelledby="prediction-h" className="grid gap-2 rounded-md border border-dashed border-line bg-surface-2/40 p-3">
      <h4 id="prediction-h" className="text-sm font-semibold">
        Prediction
      </h4>
      <p className="font-mono text-lg font-semibold">{speedup(prediction.expected_speedup)}</p>
      <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-2">
        <li>{formatPercent(prediction.p_improve)} likely to improve (at least 20% faster)</li>
        <li>Index size about {formatBytes(prediction.expected_index_mb * 1024 * 1024)}</li>
        <li>{formatPercent(prediction.p_regression)} chance of getting slower</li>
      </ul>
      {prediction.contributions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-ink-2">What drives this estimate</p>
          <ul aria-label="Why the model expects this" className="mt-1 grid gap-0.5 text-sm">
            {prediction.contributions.map((c) => (
              <li key={c.feature}>{capitalise(c.phrase)}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-2">
        This is an estimate learned from earlier benchmarks, not a measurement, and it does not change the evidence level above.{" "}
        {prediction.used_for_ranking ? "Ideas are ordered by this model." : "Ideas are ordered by the planner score."}
      </p>
    </section>
  );
}
