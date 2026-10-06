import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelsView } from "@/components/models/ModelsView";
import type { ModelMeta, ModelMonitor } from "@/lib/types";

// Recharts needs real layout; the numbers are asserted in the tables next to each chart.
vi.mock("@/components/models/MetricsChart", () => ({ MetricsChart: () => <div data-testid="metrics-chart" /> }));
vi.mock("@/components/models/ImportanceChart", () => ({ ImportanceChart: ({ importances }: { importances: object }) => <div data-testid="importance-chart">{Object.keys(importances).length}</div> }));
vi.mock("@/components/models/CalibrationChart", () => ({ CalibrationChart: () => <div data-testid="calibration-chart" /> }));

const metrics = (ndcg: number, extra = {}) => ({ ndcg_at_3: ndcg, precision_at_3: ndcg - 0.05, top1_improves: ndcg + 0.02, ...extra });
const cv = (m: number) => ({ ndcg_at_3_mean: m, ndcg_at_3_std: 0.03, precision_at_3_mean: m - 0.05, precision_at_3_std: 0.02, folds: 5 });

function meta(over: Partial<ModelMeta> = {}): ModelMeta {
  return {
    available: true,
    feature_names: ["a", "b"],
    feature_version: 1,
    created_at: "2026-10-06T10:00:00Z",
    models: {
      heuristic: { holdout: metrics(0.62), cv: cv(0.6) },
      logistic: { holdout: metrics(0.7, { brier: 0.2, ece: 0.05 }), cv: cv(0.68) },
      lgbm_regressor: { holdout: metrics(0.78, { mae_log_speedup: 0.41 }), cv: cv(0.75) },
      lgbm_classifier: { holdout: metrics(0.74, { brier: 0.15, ece: 0.04 }), cv: cv(0.72) },
      lgbm_ranker: { holdout: metrics(0.71), cv: cv(0.7) },
    },
    deployed_model: "lgbm_regressor",
    use_for_ranking: true,
    reason: "lgbm_regressor beats the planner-score heuristic on held-out groups and in cross-validation.",
    paired_ndcg_gain: { mean: 0.12, lo: 0.06, hi: 0.18, queries: 400, groups: 40 },
    importances: { lgbm_regressor: { d_removed_seq_scans: 0.5, c_equality_cov: 0.3 }, lgbm_ranker: { c_equality_cov: 0.9 } },
    calibration: {
      brier_raw: 0.16, brier_calibrated: 0.15, ece_raw: 0.08, ece_calibrated: 0.04, calibrated: true,
      points_raw: [{ bin_lo: 0, bin_hi: 0.2, mean_pred: 0.1, frac_pos: 0.2, count: 10 }],
      points_calibrated: [{ bin_lo: 0, bin_hi: 0.2, mean_pred: 0.15, frac_pos: 0.2, count: 10 }],
    },
    dataset: { rows: 3000, groups: 40, queries: 1100, by_variant: { tiny: 600, small: 600 }, useful_rate: 0.66, regression_rate: 0.08, decoy_rate: 0.47 },
    data_hash: "f".repeat(64),
    n_holdout_groups: 11,
    versions: { lightgbm: "4.6", "scikit-learn": "1.7", python: "3.12", numpy: "2.3" },
    ...over,
  };
}

const monitor = (over: Partial<ModelMonitor> = {}): ModelMonitor => ({
  n: 0, mae_log_speedup: null, training_mae: 0.41, precision_of_positive: null, calibration: [], drift: false, drift_reason: null, ...over,
});

beforeEach(() => undefined);

describe("ModelsView", () => {
  it("tells the person how to build a model when none is loaded", () => {
    render(<ModelsView model={{ available: false }} monitor={monitor()} />);
    expect(screen.getByText(/no ranking model is loaded/i)).toBeInTheDocument();
    expect(screen.getByText(/heuristic/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows one row per model with its held-out and cross-validated numbers", () => {
    render(<ModelsView model={meta()} monitor={monitor()} />);
    const table = screen.getByRole("table", { name: /model comparison/i });
    expect(within(table).getAllByRole("row")).toHaveLength(6); // header + five models
    const heuristic = within(table).getByRole("row", { name: /planner score/i });
    expect(heuristic).toHaveTextContent("0.62");
    const regressor = within(table).getByRole("row", { name: /speedup regressor/i });
    expect(regressor).toHaveTextContent("0.78");
    expect(regressor).toHaveTextContent("0.75 ± 0.03");
    expect(regressor).toHaveTextContent("0.41"); // MAE in log speedup
  });

  it("marks the deployed model and explains whether it orders suggestions", () => {
    render(<ModelsView model={meta()} monitor={monitor()} />);
    const regressor = screen.getByRole("row", { name: /speedup regressor/i });
    expect(within(regressor).getByText(/deployed/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /ordering of suggestions/i })).toBeInTheDocument();
    expect(screen.getByText(/the learned model orders planner-validated suggestions/i)).toBeInTheDocument();
    expect(screen.getByText(/beats the planner-score heuristic/i)).toBeInTheDocument();
    expect(screen.getByText(/\+0\.12/)).toBeInTheDocument();
  });

  it("says the heuristic keeps ordering when the model did not earn it, and shows why", () => {
    const reason = "The planner-score heuristic keeps ordering suggestions: held-out precision@3 is not better.";
    render(<ModelsView model={meta({ use_for_ranking: false, reason })} monitor={monitor()} />);
    expect(screen.getByText(/the planner score keeps ordering suggestions/i)).toBeInTheDocument();
    expect(screen.getByText(/held-out precision@3 is not better/i)).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /speedup regressor/i })).getByText(/best learned/i)).toBeInTheDocument();
  });

  it("describes the data the model was trained on and how it was evaluated", () => {
    render(<ModelsView model={meta()} monitor={monitor()} />);
    expect(screen.getByText(/3,000 measured index candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/40 query shapes/i)).toBeInTheDocument();
    expect(screen.getByText(/66% of candidates helped/i)).toBeInTheDocument();
    expect(screen.getByText(/never in both/i)).toBeInTheDocument(); // group-based splits
  });

  it("shows feature importances and calibration", () => {
    render(<ModelsView model={meta()} monitor={monitor()} />);
    expect(screen.getByTestId("importance-chart")).toBeInTheDocument();
    expect(screen.getByTestId("calibration-chart")).toBeInTheDocument();
    expect(screen.getByTestId("metrics-chart")).toBeInTheDocument();
    expect(screen.getByText(/0\.08 → 0\.04/)).toBeInTheDocument(); // ECE before and after calibration
  });

  it("shows the monitoring card: not enough data yet, then live error and a drift warning", () => {
    const { rerender } = render(<ModelsView model={meta()} monitor={monitor()} />);
    expect(screen.getByText(/no benchmark has been run on a predicted idea yet/i)).toBeInTheDocument();
    rerender(
      <ModelsView
        model={meta()}
        monitor={monitor({ n: 24, mae_log_speedup: 0.9, precision_of_positive: 0.6, drift: true, drift_reason: "live error 0.90 exceeds 1.5x the training error 0.41 over 24 benchmarks" })}
      />,
    );
    const card = screen.getByRole("region", { name: /monitoring/i });
    expect(card).toHaveTextContent("24");
    expect(card).toHaveTextContent("0.90");
    expect(card).toHaveTextContent("60%");
    expect(within(card).getByRole("alert")).toHaveTextContent(/exceeds 1\.5x/);
  });
});
