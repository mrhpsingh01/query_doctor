import { formatNumber, formatPercent } from "@/lib/format";
import type { ModelMeta, ModelMonitor, ModelResponse } from "@/lib/types";
import { CalibrationChart } from "./CalibrationChart";
import { ImportanceChart } from "./ImportanceChart";
import { MODEL_LABELS } from "./labels";
import { MetricsChart } from "./MetricsChart";

const two = (x: number) => x.toFixed(2);
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}`;
const CELL = "px-3 py-2 text-right font-mono text-[13px] tabular-nums";
const HEAD = "px-3 py-2 text-xs font-semibold text-ink-2";

function Unavailable() {
  return (
    <section className="rounded-md border border-line bg-surface p-5">
      <h2 className="text-lg font-semibold">No ranking model is loaded</h2>
      <p className="mt-1 max-w-[64ch] text-sm text-ink-2">
        Index ideas are ordered by the planner score (the heuristic), which is what you get without a trained model. A model is
        trained on benchmarks measured in the sample database and loaded from the models folder when the services start; see
        the README for how to build one.
      </p>
    </section>
  );
}

function Comparison({ meta }: { meta: ModelMeta }) {
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table aria-label="Model comparison" className="w-full min-w-[780px] border-collapse text-sm">
        <thead className="border-b border-line bg-surface-2 text-left">
          <tr>
            <th scope="col" className={HEAD}>
              Model
            </th>
            <th scope="col" className={`${HEAD} text-right`} title="Normalised discounted cumulative gain of the top three suggestions per query, on held-out query shapes">
              NDCG@3
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Precision@3
            </th>
            <th scope="col" className={`${HEAD} text-right`} title="How often the single top suggestion really improves the query">
              Top pick improves
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Cross-validated NDCG@3
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Other
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {Object.entries(meta.models).map(([name, m]) => {
            const other = m.holdout.mae_log_speedup !== undefined
              ? `MAE ${two(m.holdout.mae_log_speedup)} (log speedup)`
              : m.holdout.brier !== undefined
                ? `Brier ${two(m.holdout.brier)}, ECE ${two(m.holdout.ece ?? 0)}`
                : "";
            return (
              <tr key={name} className={name === meta.deployed_model ? "bg-accent-soft/40" : ""}>
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  {MODEL_LABELS[name] ?? name}
                  {name === meta.deployed_model && (
                    <span className="ml-2 rounded border border-accent px-1.5 py-0.5 text-xs font-semibold text-accent">
                      {meta.use_for_ranking ? "Deployed" : "Best learned (not used for ordering)"}
                    </span>
                  )}
                </th>
                <td className={CELL}>{two(m.holdout.ndcg_at_3)}</td>
                <td className={CELL}>{two(m.holdout.precision_at_3)}</td>
                <td className={CELL}>{two(m.holdout.top1_improves)}</td>
                <td className={CELL}>
                  {two(m.cv.ndcg_at_3_mean)} ± {two(m.cv.ndcg_at_3_std)}
                </td>
                <td className={`${CELL} text-ink-2`}>{other}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Monitoring({ monitor, trainingMae }: { monitor: ModelMonitor; trainingMae: number | null }) {
  return (
    <section aria-labelledby="monitor-h" className="grid gap-3 rounded-md border border-line bg-surface p-4">
      <h2 id="monitor-h" className="text-lg font-semibold">
        Monitoring
      </h2>
      {monitor.n === 0 ? (
        <p className="text-sm text-ink-2">
          No benchmark has been run on a predicted idea yet. Once some have, this compares what the model predicted with what was
          measured.
        </p>
      ) : (
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-ink-2">Benchmarks compared</dt>
            <dd className="font-mono text-base">{monitor.n}</dd>
          </div>
          <div>
            <dt className="text-ink-2">Average error (log speedup)</dt>
            <dd className="font-mono text-base">
              {two(monitor.mae_log_speedup ?? 0)}
              {trainingMae !== null && <span className="text-ink-2"> (training {two(trainingMae)})</span>}
            </dd>
          </div>
          <div>
            <dt className="text-ink-2">Predicted-helpful ideas that helped</dt>
            <dd className="font-mono text-base">{monitor.precision_of_positive === null ? "n/a" : formatPercent(monitor.precision_of_positive)}</dd>
          </div>
        </dl>
      )}
      {monitor.drift && (
        <p role="alert" className="rounded-md border border-heat-2 bg-heat-2-soft px-3 py-2 text-sm">
          {monitor.drift_reason ?? "The model's live error is well above its training error."} Consider retraining.
        </p>
      )}
    </section>
  );
}

/** The recommendation model: how it compares with the planner-score heuristic, what it learned from, and how it is doing. */
export function ModelsView({ model, monitor }: { model: ModelResponse; monitor: ModelMonitor }) {
  return (
    <div className="grid gap-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Recommendation model</h1>
        <p className="mt-1 max-w-[64ch] text-sm text-ink-2">
          Advice about which index ideas are likely to pay off. It never changes the evidence level: only a benchmark does that.
        </p>
      </div>

      {!model.available ? (
        <Unavailable />
      ) : (
        <>
          <section aria-labelledby="order-h" className="grid gap-2 rounded-md border border-line bg-surface p-4">
            <h2 id="order-h" className="text-lg font-semibold">
              Ordering of suggestions
            </h2>
            <p className="text-sm font-medium">
              {model.use_for_ranking
                ? "The learned model orders planner-validated suggestions."
                : "The planner score keeps ordering suggestions."}
            </p>
            <p className="max-w-[80ch] text-sm text-ink-2">{model.reason}</p>
            <p className="text-sm text-ink-2">
              Average NDCG@3 gain over the heuristic across all out-of-fold queries: {signed(model.paired_ndcg_gain.mean)} (90% interval{" "}
              {signed(model.paired_ndcg_gain.lo)} to {signed(model.paired_ndcg_gain.hi)}, {model.paired_ndcg_gain.queries} queries).
            </p>
          </section>

          <section aria-labelledby="compare-h" className="grid gap-3">
            <h2 id="compare-h" className="text-lg font-semibold">
              How the models compare
            </h2>
            <Comparison meta={model} />
            <MetricsChart models={model.models} />
          </section>

          <section aria-labelledby="data-h" className="grid gap-2">
            <h2 id="data-h" className="text-lg font-semibold">
              Training data
            </h2>
            <p className="max-w-[80ch] text-sm">
              Trained on {formatNumber(model.dataset.rows)} measured index candidates for {formatNumber(model.dataset.queries)} queries from{" "}
              {model.dataset.groups} query shapes ({formatPercent(model.dataset.useful_rate)} of candidates helped,{" "}
              {formatPercent(model.dataset.regression_rate)} made the query slower). Evaluated on groups of query shapes the model never
              saw: a shape is in training or in testing, never in both.
            </p>
          </section>

          <section aria-labelledby="imp-h" className="grid gap-3">
            <h2 id="imp-h" className="text-lg font-semibold">
              What the model pays attention to
            </h2>
            <ImportanceChart importances={model.importances} />
          </section>

          <section aria-labelledby="cal-h" className="grid gap-3">
            <h2 id="cal-h" className="text-lg font-semibold">
              Calibration
            </h2>
            <p className="text-sm text-ink-2">
              Calibration error (ECE) {two(model.calibration.ece_raw)} → {two(model.calibration.ece_calibrated)} after calibration; Brier
              score {two(model.calibration.brier_raw)} → {two(model.calibration.brier_calibrated)}.
            </p>
            <CalibrationChart raw={model.calibration.points_raw} calibrated={model.calibration.points_calibrated} />
          </section>
        </>
      )}

      <Monitoring monitor={monitor} trainingMae={monitor.training_mae} />
    </div>
  );
}
