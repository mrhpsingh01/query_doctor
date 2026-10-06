import math

import pytest

from querydoctor.ranking.metrics import (
    brier,
    expected_calibration_error,
    ndcg_at_k,
    per_query_ndcg,
    precision_at_k,
    reliability_points,
    top1_improves_rate,
)

REL = {"a": [1, 0, 1, 0], "b": [0, 0, 1], "none": [0, 0]}
SCORES = {"a": [0.9, 0.8, 0.7, 0.1], "b": [0.1, 0.2, 0.3], "none": [0.5, 0.4]}


def test_precision_at_k_is_the_share_of_relevant_candidates_in_each_queries_top_k():
    # a: top 2 = (rel 1, rel 0) -> 1/2; b: top 2 = (rel 1, rel 0) -> 1/2. Queries with nothing relevant are not counted.
    assert precision_at_k(REL, SCORES, 2) == pytest.approx(0.5)
    # k = 3: a -> 2/3; b has only 3 candidates -> 1/3
    assert precision_at_k(REL, SCORES, 3) == pytest.approx(0.5)
    assert precision_at_k(REL, SCORES, 1) == pytest.approx(1.0)  # both queries' best-scored candidate is relevant


def test_precision_at_1_counts_whether_the_best_scored_candidate_is_relevant():
    rel = {"a": [1, 0], "b": [1, 0]}
    scores = {"a": [0.9, 0.1], "b": [0.1, 0.9]}  # a ranked right, b ranked wrong
    assert precision_at_k(rel, scores, 1) == pytest.approx(0.5)


def test_ndcg_at_3_against_a_hand_computed_case():
    rel = {"q": [2, 3, 0, 1]}
    scores = {"q": [0.9, 0.8, 0.7, 0.6]}  # predicted order: 2, 3, 0, 1
    dcg = 2 / math.log2(2) + 3 / math.log2(3) + 0 / math.log2(4)
    ideal = 3 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(rel, scores, 3) == pytest.approx(dcg / ideal)
    assert dcg / ideal == pytest.approx(0.8175, abs=1e-3)


def test_ndcg_is_one_for_the_ideal_order_and_lower_for_a_bad_one():
    rel = {"q": [3, 2, 1, 0]}
    assert ndcg_at_k(rel, {"q": [4, 3, 2, 1]}, 3) == pytest.approx(1.0)
    assert ndcg_at_k(rel, {"q": [1, 2, 3, 4]}, 3) < 0.5


def test_ndcg_with_a_single_relevant_item_at_rank_two():
    assert ndcg_at_k({"q": [0, 1, 0]}, {"q": [0.9, 0.8, 0.1]}, 3) == pytest.approx(1 / math.log2(3))


def test_ties_in_score_are_broken_by_position_so_results_are_deterministic():
    rel, scores = {"q": [0, 1]}, {"q": [0.5, 0.5]}
    assert precision_at_k(rel, scores, 1) == 0.0 and precision_at_k(rel, scores, 1) == precision_at_k(rel, scores, 1)


def test_top1_improves_rate_is_the_share_of_queries_whose_top_pick_is_relevant():
    rel = {"a": [1, 0], "b": [0, 1], "none": [0, 0]}
    scores = {"a": [0.9, 0.1], "b": [0.9, 0.1], "none": [0.9, 0.1]}
    assert top1_improves_rate(rel, scores) == pytest.approx(0.5)


def test_metrics_with_nothing_to_rank_are_zero_not_an_error():
    assert precision_at_k({}, {}, 3) == 0.0 and ndcg_at_k({}, {}, 3) == 0.0 and top1_improves_rate({}, {}) == 0.0
    assert ndcg_at_k({"q": [0, 0]}, {"q": [1, 2]}, 3) == 0.0


def test_ece_of_a_perfectly_calibrated_set_is_zero():
    y = [1, 0] * 50
    assert expected_calibration_error(y, [0.5] * 100) == pytest.approx(0.0)
    y2 = [1] * 9 + [0]
    assert expected_calibration_error(y2 * 5, [0.9] * 50) == pytest.approx(0.0)


def test_ece_of_a_constant_probability_that_is_wrong_equals_the_gap():
    assert expected_calibration_error([1] * 8 + [0] * 2, [0.3] * 10) == pytest.approx(0.5)


def test_ece_weights_bins_by_their_size():
    # 8 predictions of 0.1 that are always 0 (gap 0.1) and 2 predictions of 0.9 that are always 1 (gap 0.1)
    assert expected_calibration_error([0] * 8 + [1] * 2, [0.1] * 8 + [0.9] * 2) == pytest.approx(0.1)


def test_brier_score():
    assert brier([1, 0], [1.0, 0.0]) == 0.0
    assert brier([1, 0], [0.5, 0.5]) == pytest.approx(0.25)
    assert brier([1, 1, 0, 0], [0.9, 0.7, 0.2, 0.4]) == pytest.approx((0.01 + 0.09 + 0.04 + 0.16) / 4)


def test_reliability_points_report_only_non_empty_bins():
    pts = reliability_points([0, 0, 1, 1], [0.05, 0.1, 0.8, 0.9], bins=5)
    assert [p["count"] for p in pts] == [2, 2]
    assert pts[0]["mean_pred"] == pytest.approx(0.075) and pts[0]["frac_pos"] == 0.0
    assert pts[1]["mean_pred"] == pytest.approx(0.85) and pts[1]["frac_pos"] == 1.0
    assert pts[0]["bin_lo"] == 0.0 and pts[0]["bin_hi"] == pytest.approx(0.2)
    assert reliability_points([], [], 10) == []


def test_per_query_ndcg_agrees_with_the_mean_and_skips_queries_with_nothing_relevant():
    rel = {"a": [2, 3, 0, 1], "b": [0, 1, 0], "none": [0, 0]}
    scores = {"a": [0.9, 0.8, 0.7, 0.6], "b": [0.9, 0.8, 0.1], "none": [1, 2]}
    per = per_query_ndcg(rel, scores, 3)
    assert set(per) == {"a", "b"} and per["b"] == pytest.approx(1 / math.log2(3))
    assert sum(per.values()) / 2 == pytest.approx(ndcg_at_k(rel, scores, 3))
