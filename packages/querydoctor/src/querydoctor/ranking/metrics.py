"""Ranking and calibration metrics, evaluated per query (the candidates generated for one concrete query).

Queries with no relevant candidate are skipped by the ranking metrics: nothing can be ranked well or badly there, and
counting them would only dilute the difference between models.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np


def _order(scores: Sequence[float]) -> np.ndarray:
    """Candidate positions from best to worst score; ties keep their original order so results are deterministic."""
    return np.argsort(-np.asarray(scores, dtype=float), kind="stable")


def _eligible(rel_by_query: Mapping[str, Sequence[float]]) -> list[str]:
    return [q for q, rel in rel_by_query.items() if any(r > 0 for r in rel)]


def precision_at_k(rel_by_query: Mapping[str, Sequence[float]], scores_by_query: Mapping[str, Sequence[float]], k: int) -> float:
    """Mean over queries of (relevant candidates among the k best-scored) / (candidates considered)."""
    values = []
    for q in _eligible(rel_by_query):
        rel = np.asarray(rel_by_query[q], dtype=float)
        top = _order(scores_by_query[q])[:k]
        values.append(float(np.count_nonzero(rel[top] > 0)) / len(top))
    return float(np.mean(values)) if values else 0.0


def _dcg(gains: np.ndarray) -> float:
    return float(sum(g / math.log2(i + 2) for i, g in enumerate(gains)))


def ndcg_at_k(rel_by_query: Mapping[str, Sequence[float]], scores_by_query: Mapping[str, Sequence[float]], k: int) -> float:
    """Mean normalised discounted cumulative gain at k with linear gain (the graded relevance itself)."""
    values = []
    for q in _eligible(rel_by_query):
        rel = np.asarray(rel_by_query[q], dtype=float)
        top = _order(scores_by_query[q])[:k]
        ideal = _dcg(np.sort(rel)[::-1][:k])
        values.append(_dcg(rel[top]) / ideal)
    return float(np.mean(values)) if values else 0.0


def per_query_ndcg(
    rel_by_query: Mapping[str, Sequence[float]], scores_by_query: Mapping[str, Sequence[float]], k: int
) -> dict[str, float]:
    """NDCG@k of each eligible query (used for paired comparisons between two scoring methods)."""
    out = {}
    for q in _eligible(rel_by_query):
        rel = np.asarray(rel_by_query[q], dtype=float)
        top = _order(scores_by_query[q])[:k]
        out[q] = _dcg(rel[top]) / _dcg(np.sort(rel)[::-1][:k])
    return out


def top1_improves_rate(rel_by_query: Mapping[str, Sequence[float]], scores_by_query: Mapping[str, Sequence[float]]) -> float:
    """The share of queries whose single best-scored candidate really is relevant (really improves the query)."""
    values = []
    for q in _eligible(rel_by_query):
        best = _order(scores_by_query[q])[0]
        values.append(1.0 if rel_by_query[q][best] > 0 else 0.0)
    return float(np.mean(values)) if values else 0.0


def _bin_index(p: np.ndarray, bins: int) -> np.ndarray:
    return np.minimum((np.clip(p, 0.0, 1.0) * bins).astype(int), bins - 1)


def expected_calibration_error(y: Sequence[float], p: Sequence[float], bins: int = 10) -> float:
    """Sum over equal-width probability bins of (share of predictions in the bin) x |observed rate - mean prediction|."""
    y_a, p_a = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y_a) == 0:
        return 0.0
    index = _bin_index(p_a, bins)
    total = 0.0
    for b in range(bins):
        mask = index == b
        if mask.any():
            total += mask.sum() / len(y_a) * abs(y_a[mask].mean() - p_a[mask].mean())
    return float(total)


def brier(y: Sequence[float], p: Sequence[float]) -> float:
    y_a, p_a = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    return float(np.mean((p_a - y_a) ** 2)) if len(y_a) else 0.0


def reliability_points(y: Sequence[float], p: Sequence[float], bins: int = 10) -> list[dict]:
    """One point per non-empty bin for a reliability diagram: where predictions fall versus what actually happened."""
    y_a, p_a = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y_a) == 0:
        return []
    index = _bin_index(p_a, bins)
    points = []
    for b in range(bins):
        mask = index == b
        if mask.any():
            points.append(
                {
                    "bin_lo": b / bins, "bin_hi": (b + 1) / bins, "mean_pred": float(p_a[mask].mean()),
                    "frac_pos": float(y_a[mask].mean()), "count": int(mask.sum()),
                }
            )
    return points
