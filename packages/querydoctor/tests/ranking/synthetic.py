"""Synthetic ranking datasets for tests: candidates per query with random features and, optionally, a planted signal."""

import math

import numpy as np

from querydoctor.ranking import FEATURE_NAMES
from querydoctor.ranking.dataset import DatasetRow


def synthetic_rows(seed: int, *, signal: bool, n_queries: int = 220, per_query: int = 6, n_groups: int = 55) -> list[DatasetRow]:
    """Candidates per query with random features. With ``signal`` the speedup is driven by two features the heuristic
    (planner_score) cannot see; without it the labels are pure noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for q in range(n_queries):
        group = f"g{q % n_groups}"
        for c in range(per_query):
            feats = {name: float(rng.normal()) for name in FEATURE_NAMES}
            driver = 1.5 * feats["c_equality_cov"] + 1.0 * feats["d_cost_reduction"] - 0.5
            log_speedup = (driver if signal else 0.0) + float(rng.normal(scale=0.4 if signal else 1.0))
            speedup = math.exp(log_speedup)
            improvement = (1 - 1 / speedup) * 100
            rows.append(
                DatasetRow(
                    features=feats, log_speedup=log_speedup, improvement_pct=improvement, useful=improvement >= 20,
                    regression=speedup < 0.9, index_size_mb=float(abs(rng.normal(5, 2))), build_ms=100.0, group_id=group,
                    query_id=f"{group}:v:{q}", variant="v", template_id=f"t{q % 7}", ddl=f"CREATE INDEX ON t (c{c})",
                    baseline_ms=5.0, candidate_ms=5.0 / speedup, planner_score=float(rng.uniform()), uses_candidate=True,
                    index_used=True, checksum_match=True, is_decoy=False, feature_version=1,
                )
            )
    return rows
