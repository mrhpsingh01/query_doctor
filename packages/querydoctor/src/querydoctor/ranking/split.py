"""Group-based splits: every row of a group (here: a query fingerprint) lands on the same side, so a model is never
evaluated on a query shape it was trained on."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def _unique(groups: Sequence) -> list:
    return sorted(set(groups), key=str)


def group_holdout(groups: Sequence, frac: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """``(train_indices, test_indices)`` with about ``frac`` of the groups (at least one, never all) held out."""
    names = _unique(groups)
    if len(names) < 2:
        raise ValueError("a group-based holdout needs at least two groups")
    order = np.random.default_rng(seed).permutation(len(names))
    n_test = min(max(round(frac * len(names)), 1), len(names) - 1)
    test_groups = {names[i] for i in order[:n_test]}
    mask = np.array([g in test_groups for g in groups])
    idx = np.arange(len(groups))
    return idx[~mask], idx[mask]


def group_kfold(groups: Sequence, k: int, seed: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """``k`` (train, test) index pairs; each group is in exactly one test fold. Groups are placed largest first into the
    currently lightest fold, so fold sizes stay balanced."""
    names = _unique(groups)
    if len(names) < k:
        raise ValueError(f"cannot split {len(names)} groups into {k} folds")
    sizes = {g: 0 for g in names}
    for g in groups:
        sizes[g] += 1
    shuffled = [names[i] for i in np.random.default_rng(seed).permutation(len(names))]
    shuffled.sort(key=lambda g: -sizes[g])  # stable: ties keep their shuffled order
    load = [0] * k
    fold_of: dict = {}
    for g in shuffled:
        target = int(np.argmin(load))
        fold_of[g] = target
        load[target] += sizes[g]
    fold_index = np.array([fold_of[g] for g in groups])
    idx = np.arange(len(groups))
    return [(idx[fold_index != f], idx[fold_index == f]) for f in range(k)]
