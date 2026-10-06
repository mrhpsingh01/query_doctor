"""The recommendation ranker inside the product: predictions per candidate, outcome recording and monitoring.

Everything here is advisory and defensive. A missing, failing or nonsensical model must leave the analysis exactly as
it was (heuristic order, no predictions); a prediction never touches a candidate's evidence level.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from querydoctor.candidates import ScoredCandidate, extract_usage
from querydoctor.ranking import FeatureInput, extract_features
from querydoctor.ranking.metrics import reliability_points
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, ModelOutcome, ModelPrediction, RecommendationCandidate

log = logging.getLogger(__name__)

USEFUL_PCT = 20.0
DRIFT_MIN_OUTCOMES = 20
DRIFT_FACTOR = 1.5  # drift when the live error exceeds the training error by this factor
MONITOR_WINDOW = 500


def _finite(p) -> bool:
    values = [p.expected_speedup, p.p_improve, p.p_regression, p.expected_index_mb, p.rank_score, *(c.effect for c in p.contributions)]
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def schema_with_extras(deps, conn, schema, tables):
    """The schema plus table size and column statistics when the collector supports them (otherwise the plain schema)."""
    try:
        return deps.collect_schema(conn, tables, extras=True)
    except Exception:
        return schema


def predict_candidates(predictor, sql: str, schema, baseline, scored: list[ScoredCandidate]) -> dict[str, Any]:
    """``candidate.id -> Prediction`` for the testable candidates, or ``{}`` when anything about the model misbehaves."""
    if predictor is None or not scored:
        return {}
    try:
        usage = extract_usage(sql, schema)
        features = [
            extract_features(
                FeatureInput(sql=sql, usage=usage, schema=schema, original=baseline, candidate=s.candidate, candidate_plan=s.plan,
                             comparison=s.comparison)
            )
            for s in scored
        ]
        predictions = predictor.predict_many(features)
        if len(predictions) != len(scored) or not all(_finite(p) for p in predictions):
            log.warning("ranking model returned unusable predictions: ignored")
            return {}
        return {s.candidate.id: p for s, p in zip(scored, predictions, strict=True)}
    except Exception:
        log.warning("ranking prediction failed: suggestions keep the heuristic order", exc_info=True)
        return {}


def persist_predictions(session: Session, analysis: Analysis, predictions: dict[str, Any], used_for_ranking: bool) -> None:
    """Attach each prediction to its candidate row. Never raises: advice must not break a finished analysis."""
    try:
        for row in analysis.candidates:
            p = predictions.get(row.candidate_key)
            if p is None:
                continue
            row.prediction = ModelPrediction(
                analysis_id=analysis.id, model_version=p.model_version, expected_speedup=p.expected_speedup, p_improve=p.p_improve,
                expected_index_mb=p.expected_index_mb, p_regression=p.p_regression, rank_score=p.rank_score,
                used_for_ranking=used_for_ranking,
                contributions=[{"feature": c.feature, "value": c.value, "effect": c.effect, "phrase": c.phrase} for c in p.contributions],
                base_value=p.base_value, other_effect=p.other_effect,
            )
        session.commit()
    except Exception:
        log.warning("could not store predictions for analysis %s", analysis.id, exc_info=True)
        session.rollback()


def record_outcome(session: Session, run, candidate: RecommendationCandidate, result) -> None:
    """After a completed benchmark: what the model predicted for this candidate against what was measured."""
    try:
        prediction = candidate.prediction
        if prediction is None or session.scalar(select(ModelOutcome.id).where(ModelOutcome.benchmark_run_id == run.id)) is not None:
            return
        actual = max(result.before.median_ms, 1e-6) / max(result.after.median_ms, 1e-6)
        session.add(
            ModelOutcome(
                candidate_id=candidate.id, benchmark_run_id=run.id, model_version=prediction.model_version,
                predicted_speedup=prediction.expected_speedup, actual_speedup=actual,
                abs_error_log=abs(math.log(max(prediction.expected_speedup, 1e-6)) - math.log(actual)),
                predicted_p_improve=prediction.p_improve, predicted_positive=prediction.p_improve >= 0.5,
                actual_useful=result.improvement_pct >= USEFUL_PCT and result.checksum_match,
            )
        )
        session.commit()
    except Exception:
        log.warning("could not record the model outcome of benchmark %s", getattr(run, "id", "?"), exc_info=True)
        session.rollback()


def training_mae(meta: dict | None) -> float | None:
    try:
        return float(meta["models"]["lgbm_regressor"]["holdout"]["mae_log_speedup"])
    except (TypeError, KeyError, ValueError):
        return None


def monitor(session: Session, meta: dict | None) -> dict[str, Any]:
    """How the model is doing on candidates that were later benchmarked. Drift is flagged only with enough evidence."""
    rows = list(session.scalars(select(ModelOutcome).order_by(ModelOutcome.id.desc()).limit(MONITOR_WINDOW)))
    expected = training_mae(meta)
    n = len(rows)
    if n == 0:
        return {"n": 0, "mae_log_speedup": None, "precision_of_positive": None, "calibration": [], "training_mae": expected,
                "drift": False, "drift_reason": None}
    mae = sum(r.abs_error_log for r in rows) / n
    positives = [r for r in rows if r.predicted_positive]
    drift = bool(expected is not None and n >= DRIFT_MIN_OUTCOMES and mae > DRIFT_FACTOR * expected)
    reason = None
    if drift:
        reason = f"live error {mae:.2f} (log speedup) exceeds {DRIFT_FACTOR}x the training error {expected:.2f} over {n} benchmarks"
    return {
        "n": n, "mae_log_speedup": mae, "training_mae": expected,
        "precision_of_positive": (sum(r.actual_useful for r in positives) / len(positives)) if positives else None,
        "calibration": reliability_points([int(r.actual_useful) for r in rows], [r.predicted_p_improve for r in rows], bins=5),
        "drift": drift, "drift_reason": reason,
    }
