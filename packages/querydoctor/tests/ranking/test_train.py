import json
import math

import numpy as np
import pytest

from querydoctor.ranking import FEATURE_NAMES
from synthetic import synthetic_rows
from querydoctor.ranking.train import (
    TrainedBundle,
    load_bundle,
    relevance_band,
    rows_to_matrix,
    save_bundle,
    train_all,
)


@pytest.fixture(scope="module")
def signal_bundle():
    return train_all(synthetic_rows(1, signal=True), seed=3)


def test_relevance_bands_grade_the_improvement():
    assert [relevance_band(x, True) for x in (-30, 0, 19.9, 20, 49, 50, 79, 80, 99)] == [0, 0, 0, 1, 1, 2, 2, 3, 3]
    assert relevance_band(95, False) == 0  # a faster but different answer is never relevant


def test_rows_become_a_matrix_in_feature_name_order():
    rows = synthetic_rows(2, signal=True, n_queries=3)
    x = rows_to_matrix(rows)
    assert x.shape == (len(rows), len(FEATURE_NAMES))
    assert x[0, FEATURE_NAMES.index("c_sort_served")] == rows[0].features["c_sort_served"]


def test_a_planted_signal_is_learned_and_beats_the_heuristic_on_held_out_groups(signal_bundle):
    meta = signal_bundle.meta
    heuristic = meta["models"]["heuristic"]["holdout"]
    best = meta["models"][meta["deployed_model"]]["holdout"]
    assert meta["deployed_model"] != "heuristic"
    assert best["ndcg_at_3"] > heuristic["ndcg_at_3"] + 0.05 and best["precision_at_3"] > heuristic["precision_at_3"]
    assert meta["use_for_ranking"] is True and "beats" in meta["reason"].lower()
    assert meta["models"]["lgbm_regressor"]["holdout"]["mae_log_speedup"] < 0.8  # noise level is 0.4, the range is wide


def test_pure_noise_does_not_earn_use_for_ranking():
    bundle = train_all(synthetic_rows(2, signal=False), seed=3)
    assert bundle.meta["use_for_ranking"] is False
    assert "heuristic" in bundle.meta["reason"].lower()


def test_meta_reports_every_model_the_calibration_importances_and_the_data(signal_bundle):
    meta = signal_bundle.meta
    assert set(meta["models"]) == {"heuristic", "logistic", "lgbm_regressor", "lgbm_classifier", "lgbm_ranker"}
    for name, m in meta["models"].items():
        assert {"ndcg_at_3", "precision_at_3", "top1_improves"} <= set(m["holdout"]), name
        assert {"ndcg_at_3_mean", "ndcg_at_3_std", "folds"} <= set(m["cv"]), name
        assert m["cv"]["folds"] == 5
    assert "brier" in meta["models"]["lgbm_classifier"]["holdout"] and "ece" in meta["models"]["lgbm_classifier"]["holdout"]
    assert meta["feature_names"] == FEATURE_NAMES and meta["feature_version"] == 1
    top = max(meta["importances"]["lgbm_regressor"].items(), key=lambda kv: kv[1])[0]
    assert top in ("c_equality_cov", "d_cost_reduction")  # the planted drivers
    cal = meta["calibration"]
    assert {"brier_raw", "brier_calibrated", "ece_raw", "ece_calibrated", "points_raw", "points_calibrated"} <= set(cal)
    assert meta["dataset"]["rows"] == 220 * 6 and meta["dataset"]["groups"] == 55 and len(meta["data_hash"]) == 64
    assert meta["n_holdout_groups"] >= 1 and set(meta["versions"]) >= {"lightgbm", "scikit-learn", "python"}


def test_predictions_have_every_output_and_the_calibrated_probability_is_a_probability(signal_bundle):
    x = rows_to_matrix(synthetic_rows(5, signal=True, n_queries=20))
    out = signal_bundle.predict(x)
    assert {"expected_log_speedup", "p_useful_raw", "p_useful", "log_index_size_mb", "p_regression", "rank_score"} <= set(out)
    for key, values in out.items():
        assert values.shape == (len(x),) and np.all(np.isfinite(values)), key
    assert np.all((out["p_useful"] >= 0) & (out["p_useful"] <= 1)) and np.all((out["p_regression"] >= 0) & (out["p_regression"] <= 1))


def test_predicted_speedup_correlates_with_the_planted_truth(signal_bundle):
    rows = synthetic_rows(6, signal=True, n_queries=60)
    predicted = signal_bundle.predict(rows_to_matrix(rows))["expected_log_speedup"]
    actual = np.array([r.log_speedup for r in rows])
    assert np.corrcoef(predicted, actual)[0, 1] > 0.8


def test_training_is_deterministic_given_the_seed():
    rows = synthetic_rows(1, signal=True, n_queries=120, n_groups=30)
    a, b = train_all(rows, seed=11), train_all(rows, seed=11)
    strip = lambda m: {k: v for k, v in m.items() if k != "created_at"}  # noqa: E731
    assert json.dumps(strip(a.meta), sort_keys=True) == json.dumps(strip(b.meta), sort_keys=True)
    x = rows_to_matrix(rows[:50])
    assert all(np.array_equal(a.predict(x)[k], b.predict(x)[k]) for k in a.predict(x))
    assert strip(train_all(rows, seed=12).meta) != strip(a.meta)  # a different seed means a different split


def test_a_bundle_survives_a_save_and_load(tmp_path, signal_bundle):
    path = tmp_path / "ranker.joblib"
    save_bundle(signal_bundle, path)
    assert path.exists() and (tmp_path / "ranker.meta.json").exists()
    loaded = load_bundle(path)
    assert isinstance(loaded, TrainedBundle) and loaded.meta == signal_bundle.meta
    x = rows_to_matrix(synthetic_rows(7, signal=True, n_queries=15))
    for key, values in signal_bundle.predict(x).items():
        assert np.allclose(loaded.predict(x)[key], values), key
    assert json.loads((tmp_path / "ranker.meta.json").read_text(encoding="utf-8"))["use_for_ranking"] is True


def test_too_little_data_is_refused_with_a_clear_message():
    with pytest.raises(ValueError, match="at least"):
        train_all(synthetic_rows(1, signal=True, n_queries=4, n_groups=2), seed=0)


def test_a_label_with_a_single_class_does_not_break_training():
    rows = synthetic_rows(1, signal=True, n_queries=80, n_groups=25)
    for r in rows:
        r.regression = False  # no regressions at all
    bundle = train_all(rows, seed=1)
    p = bundle.predict(rows_to_matrix(rows[:10]))["p_regression"]
    assert np.all(p == 0.0) or np.all(p < 0.05)
