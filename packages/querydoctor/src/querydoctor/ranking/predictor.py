"""Use a trained ranker bundle at analysis time, defensively.

``Predictor.load`` never raises: a missing, corrupt or mismatched model simply means "no predictor", and the caller keeps
the heuristic ordering. A prediction is advice about a candidate; it never changes an evidence level.
"""

from __future__ import annotations

import logging
import math
import pathlib
from dataclasses import asdict, dataclass, field

import numpy as np

from querydoctor.ranking.descriptors import DESCRIPTORS
from querydoctor.ranking.features import FEATURE_NAMES, FEATURE_VERSION, NEUTRAL_DISTINCT
from querydoctor.ranking.train import TrainedBundle, load_bundle

log = logging.getLogger(__name__)

MODEL_FILE = "ranker.joblib"
TOP_CONTRIBUTIONS = 3
LOG_SPEEDUP_RANGE = (-3.0, 6.0)  # keeps exp() sane for outlier inputs: 0.05x .. ~400x
_NEUTRAL = {name: 0.0 for name in FEATURE_NAMES} | {"t_key_distinct": NEUTRAL_DISTINCT}


@dataclass
class Contribution:
    feature: str
    value: float
    effect: float  # in log-speedup units: positive pushes the expected speedup up
    phrase: str


@dataclass
class Prediction:
    expected_speedup: float  # a factor: 8.0 means about 8x faster
    p_improve: float  # calibrated probability that the index makes the query at least 20% faster
    expected_index_mb: float
    p_regression: float  # probability that the index makes the query slower
    rank_score: float
    contributions: list[Contribution]
    model_version: str
    base_value: float = 0.0  # the regressor's average output; base + contributions + other_effect = expected log speedup
    other_effect: float = 0.0  # the summed effect of every feature not listed in ``contributions``
    expected_log_speedup: float = 0.0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _phrase(feature: str, effect: float) -> str:
    verb = "raises" if effect > 0 else "lowers"
    return f"{DESCRIPTORS[feature]} {verb} the expected speedup"


def _vector(features: dict) -> np.ndarray:
    values = []
    for name in FEATURE_NAMES:
        v = features.get(name)
        values.append(float(v) if v is not None and math.isfinite(float(v)) else _NEUTRAL[name])
    return np.array(values, dtype=float)


class Predictor:
    def __init__(self, bundle: TrainedBundle) -> None:
        self.bundle = bundle
        self.meta = bundle.meta
        self.use_for_ranking: bool = bool(bundle.meta.get("use_for_ranking"))
        self.model_version = f"{bundle.meta['deployed_model']}-{bundle.meta['data_hash'][:10]}"

    @classmethod
    def load(cls, directory: str | pathlib.Path) -> Predictor | None:
        directory = pathlib.Path(directory)
        path = directory / MODEL_FILE
        meta_path = path.with_name(path.stem + ".meta.json")
        if not path.exists() and not meta_path.exists():
            log.info("no ranking model at %s: suggestions keep the heuristic order", directory)
            return None
        if not path.exists() or not meta_path.exists():
            log.warning("ranking model at %s is incomplete (needs %s and %s): ignored", directory, path.name, meta_path.name)
            return None
        try:
            bundle = load_bundle(path)
            if bundle.meta.get("feature_version") != FEATURE_VERSION or bundle.meta.get("feature_names") != FEATURE_NAMES:
                log.warning("ranking model at %s was trained on a different feature set: ignored", directory)
                return None
            bundle.predict(_vector({})[None, :])  # a smoke test: a model that cannot predict is no model
            return cls(bundle)
        except Exception:
            log.warning("ranking model at %s could not be loaded: ignored", directory, exc_info=True)
            return None

    def predict(self, features: dict) -> Prediction:
        return self.predict_many([features])[0]

    def predict_many(self, feature_dicts: list[dict]) -> list[Prediction]:
        if not feature_dicts:
            return []
        x = np.vstack([_vector(f) for f in feature_dicts])
        out = self.bundle.predict(x)
        contrib = np.asarray(self.bundle.models.regressor.booster_.predict(x, pred_contrib=True), dtype=float)
        predictions = []
        for i in range(len(x)):
            effects, base = contrib[i, :-1], float(contrib[i, -1])
            top = np.argsort(-np.abs(effects), kind="stable")[:TOP_CONTRIBUTIONS]
            listed = [
                Contribution(FEATURE_NAMES[j], float(x[i, j]), float(effects[j]), _phrase(FEATURE_NAMES[j], float(effects[j])))
                for j in top if effects[j] != 0
            ]
            raw = float(out["expected_log_speedup"][i])
            predictions.append(
                Prediction(
                    expected_speedup=math.exp(min(max(raw, LOG_SPEEDUP_RANGE[0]), LOG_SPEEDUP_RANGE[1])),
                    p_improve=float(out["p_useful"][i]), expected_index_mb=max(math.expm1(float(out["log_index_size_mb"][i])), 0.0),
                    p_regression=float(out["p_regression"][i]), rank_score=float(out["rank_score"][i]), contributions=listed,
                    model_version=self.model_version, base_value=base,
                    other_effect=float(effects.sum() - sum(c.effect for c in listed)), expected_log_speedup=raw,
                )
            )
        return predictions
