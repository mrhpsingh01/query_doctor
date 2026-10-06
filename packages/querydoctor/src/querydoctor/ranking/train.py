"""Train and evaluate the recommendation ranker.

Everything is evaluated on groups (query fingerprints) the model never saw: a held-out set of groups plus
``GroupKFold(5)`` over the rest for stability. The planner-score heuristic is the baseline every learned model has to
beat; ``use_for_ranking`` is true only when the best learned model does so on the held-out groups AND in
cross-validation, otherwise the heuristic keeps ordering the suggestions and ML output is informational.

Held-out and cross-validation metrics come from models trained without those groups. The deployed models are refit on
all rows with the same hyperparameters (and a calibrator fitted on out-of-fold predictions).
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import platform
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import sklearn
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from querydoctor.ranking.dataset import DatasetRow
from querydoctor.ranking.features import FEATURE_NAMES, FEATURE_VERSION
from querydoctor.ranking.metrics import (
    brier,
    expected_calibration_error,
    ndcg_at_k,
    per_query_ndcg,
    precision_at_k,
    reliability_points,
    top1_improves_rate,
)
from querydoctor.ranking.split import group_holdout, group_kfold

K = 3  # ranking metrics are reported at 3: the UI shows the best few suggestions
BANDS = (20.0, 50.0, 80.0)  # improvement thresholds of the graded relevance (0 = no real improvement)
HOLDOUT_FRAC = 0.27
CV_FOLDS = 5
MIN_GROUPS = 10
MIN_ROWS = 60
MARGIN_NDCG = 0.02  # a learned model must beat the heuristic by this much NDCG@3 on held-out groups ...
BOOTSTRAP = 1000  # ... and its paired NDCG@3 gain over the heuristic, measured on every out-of-fold query, must be
CONFIDENCE = 0.90  # distinguishable from zero (lower bound of this two-sided interval above 0), so noise cannot pass
LEARNED = ["logistic", "lgbm_regressor", "lgbm_classifier", "lgbm_ranker"]
MODELS = ["heuristic", *LEARNED]


def relevance_band(improvement_pct: float, checksum_match: bool) -> int:
    """0 = no real improvement (or a faster but different answer); 1..3 = 20%, 50%, 80% faster."""
    return sum(improvement_pct >= t for t in BANDS) if checksum_match else 0


def rows_to_matrix(rows: list[DatasetRow]) -> np.ndarray:
    return np.array([[r.features[name] for name in FEATURE_NAMES] for r in rows], dtype=float)


class _Const:
    """Stands in for a classifier when its label has a single class in the training data."""

    def __init__(self, p: float) -> None:
        self.p = p

    def proba(self, x: np.ndarray) -> np.ndarray:
        return np.full(len(x), self.p)


def _lgbm_params(seed: int) -> dict[str, Any]:
    return dict(
        n_estimators=120, learning_rate=0.06, num_leaves=15, min_child_samples=8, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, n_jobs=1, verbose=-1, deterministic=True, force_row_wise=True,
    )


def _classifier(kind: str, x: np.ndarray, y: np.ndarray, seed: int):
    if y.sum() < 3 or (len(y) - y.sum()) < 3:  # too few of one class to learn anything but the prior
        return _Const(float(y.mean()))
    if kind == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=0.5, random_state=seed)).fit(x, y)
    return lgb.LGBMClassifier(**_lgbm_params(seed)).fit(x, y)


def _proba(model, x: np.ndarray) -> np.ndarray:
    return model.proba(x) if isinstance(model, _Const) else model.predict_proba(x)[:, 1]


@dataclass
class _Models:
    logistic: Any
    regressor: Any
    classifier: Any
    size: Any
    regression: Any
    ranker: Any
    calibrator: Any  # IsotonicRegression or None


def _ranker_data(x, rows):
    order = np.argsort([r.query_id for r in rows], kind="stable")
    xs, rs = x[order], [rows[i] for i in order]
    sizes = [n for _q, n in Counter(r.query_id for r in rs).items()]
    labels = np.array([relevance_band(r.improvement_pct, r.checksum_match) for r in rs])
    return xs, labels, sizes


def _fit_core(x: np.ndarray, rows: list[DatasetRow], seed: int) -> _Models:
    useful = np.array([r.useful for r in rows], dtype=int)
    xr, labels, sizes = _ranker_data(x, rows)
    ranker = lgb.LGBMRanker(objective="lambdarank", **_lgbm_params(seed)).fit(xr, labels, group=sizes)
    return _Models(
        logistic=_classifier("logistic", x, useful, seed),
        regressor=lgb.LGBMRegressor(**_lgbm_params(seed)).fit(x, [r.log_speedup for r in rows]),
        classifier=_classifier("lgbm", x, useful, seed),
        size=lgb.LGBMRegressor(**_lgbm_params(seed)).fit(x, [math.log1p(r.index_size_mb) for r in rows]),
        regression=_classifier("lgbm", x, np.array([r.regression for r in rows], dtype=int), seed),
        ranker=ranker,
        calibrator=None,
    )


def _fit_calibrator(x: np.ndarray, rows: list[DatasetRow], seed: int):
    """Isotonic regression on out-of-fold probabilities of the LGBM classifier (never on its own training fit)."""
    useful = np.array([r.useful for r in rows], dtype=int)
    groups = [r.group_id for r in rows]
    if len(set(groups)) < CV_FOLDS or useful.sum() < 3 or (len(useful) - useful.sum()) < 3:
        return None
    oof = np.zeros(len(rows))
    for train, test in group_kfold(groups, CV_FOLDS, seed=seed):
        oof[test] = _proba(_classifier("lgbm", x[train], useful[train], seed), x[test])
    return IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(oof, useful)


def _fit(x: np.ndarray, rows: list[DatasetRow], seed: int, *, calibrate: bool) -> _Models:
    models = _fit_core(x, rows, seed)
    if calibrate:
        models.calibrator = _fit_calibrator(x, rows, seed)
    return models


def _calibrated(models: _Models, p_raw: np.ndarray) -> np.ndarray:
    return models.calibrator.predict(p_raw) if models.calibrator is not None else p_raw


def _predict(models: _Models, x: np.ndarray) -> dict[str, np.ndarray]:
    p_raw = _proba(models.classifier, x)
    return {
        "p_logistic": _proba(models.logistic, x),
        "expected_log_speedup": np.asarray(models.regressor.predict(x), dtype=float),
        "p_useful_raw": p_raw,
        "p_useful": np.clip(_calibrated(models, p_raw), 0.0, 1.0),
        "log_index_size_mb": np.asarray(models.size.predict(x), dtype=float),
        "p_regression": np.clip(_proba(models.regression, x), 0.0, 1.0),
        "ranker_score": np.asarray(models.ranker.predict(x), dtype=float),
    }


def _by_query(rows: list[DatasetRow], values: np.ndarray, graded: bool) -> tuple[dict, dict]:
    rel: dict[str, list[float]] = {}
    scores: dict[str, list[float]] = {}
    for r, v in zip(rows, values, strict=True):
        rel.setdefault(r.query_id, []).append(float(relevance_band(r.improvement_pct, r.checksum_match) if graded else r.useful))
        scores.setdefault(r.query_id, []).append(float(v))
    return rel, scores


def _scores_by_model(models: _Models, x: np.ndarray, rows: list[DatasetRow]) -> dict[str, np.ndarray]:
    p = _predict(models, x)
    return {
        "heuristic": np.array([r.planner_score for r in rows], dtype=float), "logistic": p["p_logistic"],
        "lgbm_regressor": p["expected_log_speedup"], "lgbm_classifier": p["p_useful_raw"], "lgbm_ranker": p["ranker_score"],
    }


def _ranking_metrics(rows: list[DatasetRow], scores: np.ndarray) -> dict[str, float]:
    rel, sc = _by_query(rows, scores, graded=True)
    return {
        "ndcg_at_3": ndcg_at_k(rel, sc, K), "precision_at_3": precision_at_k(rel, sc, K), "top1_improves": top1_improves_rate(rel, sc),
    }


def _holdout_metrics(models: _Models, x: np.ndarray, rows: list[DatasetRow]) -> dict[str, dict[str, Any]]:
    pred, scores = _predict(models, x), _scores_by_model(models, x, rows)
    out = {name: _ranking_metrics(rows, scores[name]) for name in MODELS}
    y = [int(r.useful) for r in rows]
    out["lgbm_regressor"]["mae_log_speedup"] = float(np.mean(np.abs(pred["expected_log_speedup"] - [r.log_speedup for r in rows])))
    out["logistic"].update(brier=brier(y, pred["p_logistic"]), ece=expected_calibration_error(y, pred["p_logistic"]))
    out["lgbm_classifier"].update(brier=brier(y, pred["p_useful"]), ece=expected_calibration_error(y, pred["p_useful"]))
    return out


def _per_query(rows: list[DatasetRow], scores: np.ndarray) -> dict[str, float]:
    rel, sc = _by_query(rows, scores, graded=True)
    return per_query_ndcg(rel, sc, K)


def _cv_metrics(x: np.ndarray, rows: list[DatasetRow], seed: int) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, float]]]:
    """Cross-validation summary and every query's out-of-fold NDCG@3 per model (each from a model that never saw its group)."""
    per_model: dict[str, list[dict[str, float]]] = {m: [] for m in MODELS}
    oof: dict[str, dict[str, float]] = {m: {} for m in MODELS}
    groups = [r.group_id for r in rows]
    for train, test in group_kfold(groups, CV_FOLDS, seed=seed):
        models = _fit(x[train], [rows[i] for i in train], seed, calibrate=False)
        test_rows = [rows[i] for i in test]
        scores = _scores_by_model(models, x[test], test_rows)
        for name in MODELS:
            per_model[name].append(_ranking_metrics(test_rows, scores[name]))
            oof[name].update(_per_query(test_rows, scores[name]))
    summary = {}
    for name, folds in per_model.items():
        nd, pr = [f["ndcg_at_3"] for f in folds], [f["precision_at_3"] for f in folds]
        summary[name] = {
            "ndcg_at_3_mean": float(np.mean(nd)), "ndcg_at_3_std": float(np.std(nd)), "precision_at_3_mean": float(np.mean(pr)),
            "precision_at_3_std": float(np.std(pr)), "folds": len(folds),
        }
    return summary, oof


def _paired_gain(model: dict[str, float], heuristic: dict[str, float], group_of: dict[str, str], seed: int) -> dict[str, float]:
    """Mean per-query NDCG@3 gain of ``model`` over the heuristic with a bootstrap interval that resamples whole groups
    (queries of one query shape are not independent)."""
    per_group: dict[str, list[float]] = {}
    for q, value in model.items():
        if q in heuristic:
            per_group.setdefault(group_of[q], []).append(value - heuristic[q])
    sums = np.array([sum(v) for v in per_group.values()])
    counts = np.array([len(v) for v in per_group.values()], dtype=float)
    if len(sums) == 0:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0, "queries": 0, "groups": 0}
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(sums), size=(BOOTSTRAP, len(sums)))
    boot = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    tail = (1 - CONFIDENCE) / 2 * 100
    return {
        "mean": float(sums.sum() / counts.sum()), "lo": float(np.percentile(boot, tail)), "hi": float(np.percentile(boot, 100 - tail)),
        "queries": int(counts.sum()), "groups": len(sums),
    }


def _importances(models: _Models) -> dict[str, dict[str, float]]:
    out = {}
    for name, model in (("lgbm_regressor", models.regressor), ("lgbm_classifier", models.classifier), ("lgbm_ranker", models.ranker)):
        if isinstance(model, _Const):
            continue
        gain = np.asarray(model.booster_.feature_importance(importance_type="gain"), dtype=float)
        total = gain.sum()
        share = gain / total if total > 0 else gain
        out[name] = {f: float(s) for f, s in sorted(zip(FEATURE_NAMES, share, strict=True), key=lambda kv: -kv[1]) if s > 0}
    return out


def _decide(holdout: dict, cv: dict, oof: dict, group_of: dict[str, str], seed: int) -> tuple[str, bool, str, dict]:
    deployed = max(LEARNED, key=lambda m: holdout[m]["ndcg_at_3"])  # ties keep the earlier (simpler) model
    h, m = holdout["heuristic"], holdout[deployed]
    cv_h, cv_m = cv["heuristic"]["ndcg_at_3_mean"], cv[deployed]["ndcg_at_3_mean"]
    gain = _paired_gain(oof[deployed], oof["heuristic"], group_of, seed)
    facts = (
        f"{deployed}: held-out NDCG@3 {m['ndcg_at_3']:.3f} vs {h['ndcg_at_3']:.3f}, precision@3 {m['precision_at_3']:.3f} vs "
        f"{h['precision_at_3']:.3f}, cross-validated NDCG@3 {cv_m:.3f} vs {cv_h:.3f} (model vs heuristic); paired gain "
        f"{gain['mean']:+.3f} with a {int(CONFIDENCE * 100)}% interval of [{gain['lo']:+.3f}, {gain['hi']:+.3f}]"
    )
    failed = []
    if not m["ndcg_at_3"] > h["ndcg_at_3"] + MARGIN_NDCG:
        failed.append(f"held-out NDCG@3 is not better by at least {MARGIN_NDCG}")
    if not m["precision_at_3"] > h["precision_at_3"]:
        failed.append("held-out precision@3 is not better")
    if not gain["lo"] > 0:
        failed.append("the gain over the heuristic across all out-of-fold queries is not distinguishable from zero")
    if failed:
        return deployed, False, "The planner-score heuristic keeps ordering suggestions: " + "; ".join(failed) + f". ({facts})", gain
    return deployed, True, f"{deployed} beats the planner-score heuristic on held-out groups and in cross-validation. ({facts})", gain


def _data_hash(rows: list[DatasetRow]) -> str:
    lines = sorted(f"{r.query_id}|{r.ddl}|{r.log_speedup:.6f}|{int(r.useful)}" for r in rows)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@dataclass
class TrainedBundle:
    models: _Models
    meta: dict[str, Any]

    def predict(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Every output for each feature row. ``rank_score`` is the deployed model's ordering score."""
        p = _predict(self.models, x)
        score_key = {
            "logistic": "p_logistic", "lgbm_regressor": "expected_log_speedup", "lgbm_classifier": "p_useful_raw",
            "lgbm_ranker": "ranker_score",
        }[self.meta["deployed_model"]]
        return {
            "expected_log_speedup": p["expected_log_speedup"], "p_useful_raw": p["p_useful_raw"], "p_useful": p["p_useful"],
            "log_index_size_mb": p["log_index_size_mb"], "p_regression": p["p_regression"], "rank_score": p[score_key],
        }


def train_all(rows: list[DatasetRow], *, seed: int = 0) -> TrainedBundle:
    groups = [r.group_id for r in rows]
    n_groups = len(set(groups))
    if n_groups < MIN_GROUPS or len(rows) < MIN_ROWS:
        raise ValueError(f"training needs at least {MIN_GROUPS} query groups and {MIN_ROWS} rows (got {n_groups} groups, {len(rows)} rows)")
    x = rows_to_matrix(rows)
    train_idx, test_idx = group_holdout(groups, HOLDOUT_FRAC, seed)
    train_rows, test_rows = [rows[i] for i in train_idx], [rows[i] for i in test_idx]

    evaluated = _fit(x[train_idx], train_rows, seed, calibrate=True)
    holdout = _holdout_metrics(evaluated, x[test_idx], test_rows)
    cv, oof = _cv_metrics(x[train_idx], train_rows, seed)
    held_scores = _scores_by_model(evaluated, x[test_idx], test_rows)
    for name in MODELS:  # held-out groups are disjoint from the cross-validation ones, so the union covers every group once
        oof[name].update(_per_query(test_rows, held_scores[name]))
    group_of = {r.query_id: r.group_id for r in rows}
    deployed, use, reason, gain = _decide(holdout, cv, oof, group_of, seed)

    y = [int(r.useful) for r in test_rows]
    raw = _predict(evaluated, x[test_idx])
    calibration = {
        "brier_raw": brier(y, raw["p_useful_raw"]), "brier_calibrated": brier(y, raw["p_useful"]),
        "ece_raw": expected_calibration_error(y, raw["p_useful_raw"]), "ece_calibrated": expected_calibration_error(y, raw["p_useful"]),
        "points_raw": reliability_points(y, raw["p_useful_raw"]), "points_calibrated": reliability_points(y, raw["p_useful"]),
        "calibrated": evaluated.calibrator is not None,
    }

    final = _fit(x, rows, seed, calibrate=True)
    by_variant = Counter(r.variant for r in rows)
    meta = {
        "feature_names": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION, "seed": seed,
        "created_at": datetime.now(UTC).isoformat(),
        "models": {m: {"holdout": holdout[m], "cv": cv[m]} for m in MODELS},
        "deployed_model": deployed, "use_for_ranking": use, "reason": reason, "paired_ndcg_gain": gain,
        "importances": _importances(final),
        "calibration": calibration, "data_hash": _data_hash(rows), "n_holdout_groups": len({r.group_id for r in test_rows}),
        "dataset": {
            "rows": len(rows), "groups": n_groups, "queries": len({r.query_id for r in rows}), "by_variant": dict(by_variant),
            "useful_rate": float(np.mean([r.useful for r in rows])), "regression_rate": float(np.mean([r.regression for r in rows])),
            "decoy_rate": float(np.mean([r.is_decoy for r in rows])),
        },
        "versions": {
            "lightgbm": lgb.__version__, "scikit-learn": sklearn.__version__, "python": platform.python_version(),
            "numpy": np.__version__,
        },
    }
    return TrainedBundle(models=final, meta=json.loads(json.dumps(meta)))


def _meta_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.stem + ".meta.json")


def save_bundle(bundle: TrainedBundle, path: str | pathlib.Path) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle.models, path, compress=3)
    _meta_path(path).write_text(json.dumps(bundle.meta, indent=2, sort_keys=True), encoding="utf-8")


def load_bundle(path: str | pathlib.Path) -> TrainedBundle:
    path = pathlib.Path(path)
    meta = json.loads(_meta_path(path).read_text(encoding="utf-8"))
    return TrainedBundle(models=joblib.load(path), meta=meta)

