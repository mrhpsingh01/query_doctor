import json
import logging
import math

import numpy as np
import pytest
from synthetic import synthetic_rows

from querydoctor.ranking import DESCRIPTORS, FEATURE_NAMES
from querydoctor.ranking.predictor import Contribution, Prediction, Predictor
from querydoctor.ranking.train import rows_to_matrix, save_bundle, train_all


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("models")
    save_bundle(train_all(synthetic_rows(1, signal=True), seed=3), path / "ranker.joblib")
    return path


@pytest.fixture
def predictor(model_dir):
    loaded = Predictor.load(model_dir)
    assert loaded is not None
    return loaded


def features(seed=0) -> dict:
    return dict(synthetic_rows(seed + 40, signal=True, n_queries=1, per_query=1)[0].features)


def copy_dir(src, dst):
    for f in src.iterdir():
        (dst / f.name).write_bytes(f.read_bytes())
    return dst


def test_a_valid_bundle_loads_and_exposes_whether_it_may_order_suggestions(predictor):
    assert predictor.use_for_ranking is True  # the planted signal is strong
    assert predictor.meta["feature_names"] == FEATURE_NAMES


@pytest.mark.parametrize("breakage", ["missing_dir", "empty_dir", "no_meta", "no_model", "truncated", "garbage_meta", "names", "version"])
def test_a_missing_corrupt_or_mismatched_model_gives_none_and_a_logged_reason(model_dir, tmp_path, caplog, breakage):
    target = tmp_path / "m"
    if breakage == "missing_dir":
        target = tmp_path / "does-not-exist"
    else:
        target.mkdir()
        if breakage != "empty_dir":
            copy_dir(model_dir, target)
        if breakage == "no_meta":
            (target / "ranker.meta.json").unlink()
        if breakage == "no_model":
            (target / "ranker.joblib").unlink()
        if breakage == "truncated":
            data = (target / "ranker.joblib").read_bytes()
            (target / "ranker.joblib").write_bytes(data[: len(data) // 3])
        if breakage == "garbage_meta":
            (target / "ranker.meta.json").write_text("{not json", encoding="utf-8")
        if breakage in ("names", "version"):
            meta = json.loads((target / "ranker.meta.json").read_text(encoding="utf-8"))
            if breakage == "names":
                meta["feature_names"] = list(reversed(meta["feature_names"]))
            else:
                meta["feature_version"] = 99
            (target / "ranker.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="querydoctor.ranking.predictor"):
        assert Predictor.load(target) is None
    if breakage not in ("missing_dir", "empty_dir"):
        assert any(r.levelno >= logging.WARNING for r in caplog.records), breakage


def test_a_prediction_is_in_range_and_names_its_model(predictor):
    p = predictor.predict(features())
    assert isinstance(p, Prediction)
    assert p.expected_speedup > 0 and 0 <= p.p_improve <= 1 and 0 <= p.p_regression <= 1 and p.expected_index_mb >= 0
    assert math.isfinite(p.rank_score) and p.model_version


def test_the_top_three_contributions_are_mapped_through_the_descriptors(predictor):
    p = predictor.predict(features())
    assert len(p.contributions) == 3 and all(isinstance(c, Contribution) for c in p.contributions)
    assert [abs(c.effect) for c in p.contributions] == sorted((abs(c.effect) for c in p.contributions), reverse=True)
    for c in p.contributions:
        assert c.feature in FEATURE_NAMES and DESCRIPTORS[c.feature] in c.phrase
        assert c.value == features()[c.feature]
        assert ("raises" in c.phrase) == (c.effect > 0) and ("lowers" in c.phrase) == (c.effect < 0)
        assert "AI" not in c.phrase and "model thinks" not in c.phrase.lower()


def test_contributions_plus_the_rest_plus_the_base_equal_the_raw_model_output(predictor):
    f = features(3)
    p = predictor.predict(f)
    raw = predictor.bundle.predict(rows_to_matrix_from(f))["expected_log_speedup"][0]
    total = p.base_value + sum(c.effect for c in p.contributions) + p.other_effect
    assert total == pytest.approx(raw, abs=1e-6)


def rows_to_matrix_from(f: dict) -> np.ndarray:
    return np.array([[f[name] for name in FEATURE_NAMES]], dtype=float)


def test_the_planted_driver_pushes_the_prediction_in_the_right_direction(predictor):
    base = features(1)
    low, high = dict(base, c_equality_cov=-2.0), dict(base, c_equality_cov=2.5)
    assert predictor.predict(high).expected_speedup > predictor.predict(low).expected_speedup


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_inputs_are_filled_neutrally_and_never_raise(predictor, bad):
    f = features()
    for name in list(f)[:10]:
        f[name] = bad
    p = predictor.predict(f)
    assert all(math.isfinite(x) for x in (p.expected_speedup, p.p_improve, p.p_regression, p.expected_index_mb, p.rank_score))
    assert all(math.isfinite(c.effect) for c in p.contributions)


def test_missing_features_are_filled_neutrally_too(predictor):
    p = predictor.predict({"c_equality_cov": 1.0})
    assert math.isfinite(p.rank_score) and 0 <= p.p_improve <= 1


def test_predict_many_agrees_with_predict(predictor):
    fs = [features(i) for i in range(4)]
    many = predictor.predict_many(fs)
    assert [m.rank_score for m in many] == pytest.approx([predictor.predict(f).rank_score for f in fs])
    assert predictor.predict_many([]) == []
