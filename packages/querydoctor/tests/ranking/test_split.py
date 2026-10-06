import random

import pytest

from querydoctor.ranking.split import group_holdout, group_kfold


def random_groups(seed: int, n_groups: int = 40, max_size: int = 9) -> list[str]:
    rng = random.Random(seed)
    return [f"g{g}" for g in range(n_groups) for _ in range(rng.randint(1, max_size))]


@pytest.mark.parametrize("seed", range(25))
def test_a_holdout_never_puts_a_group_on_both_sides_and_covers_every_row(seed):
    groups = random_groups(seed)
    train, test = group_holdout(groups, 0.27, seed=seed)
    assert not ({groups[i] for i in train} & {groups[i] for i in test})
    assert sorted([*train, *test]) == list(range(len(groups)))
    assert len(train) and len(test)


def test_holdout_takes_about_the_requested_share_of_groups_and_is_deterministic():
    groups = random_groups(3, n_groups=100)
    train, test = group_holdout(groups, 0.25, seed=1)
    assert len({groups[i] for i in test}) == 25
    assert (train.tolist(), test.tolist()) == tuple(x.tolist() for x in group_holdout(groups, 0.25, seed=1))
    assert test.tolist() != group_holdout(groups, 0.25, seed=2)[1].tolist()


def test_holdout_always_leaves_at_least_one_group_on_each_side():
    train, test = group_holdout(["a", "a", "b"], 0.01, seed=0)
    assert len(test) >= 1 and len(train) >= 1
    train, test = group_holdout(["a", "b"], 0.99, seed=0)
    assert len(test) == 1 and len(train) == 1
    with pytest.raises(ValueError):
        group_holdout(["only"], 0.3, seed=0)


@pytest.mark.parametrize("seed", range(15))
def test_kfold_folds_partition_the_groups_and_never_leak(seed):
    groups = random_groups(seed, n_groups=30)
    folds = group_kfold(groups, 5, seed=seed)
    assert len(folds) == 5
    seen_test_groups: list[str] = []
    for train, test in folds:
        train_groups, test_groups = {groups[i] for i in train}, {groups[i] for i in test}
        assert not (train_groups & test_groups)
        assert sorted([*train, *test]) == list(range(len(groups)))
        seen_test_groups += sorted(test_groups)
    assert sorted(seen_test_groups) == sorted(set(groups))  # every group is tested exactly once


def test_kfold_balances_fold_sizes_and_is_deterministic():
    groups = random_groups(9, n_groups=50)
    folds = group_kfold(groups, 5, seed=4)
    sizes = [len(test) for _, test in folds]
    assert max(sizes) - min(sizes) <= 9  # no fold is much bigger than another (the biggest group has 9 rows)
    assert [t.tolist() for _, t in folds] == [t.tolist() for _, t in group_kfold(groups, 5, seed=4)]


def test_kfold_needs_at_least_k_groups():
    with pytest.raises(ValueError):
        group_kfold(["a", "b", "b"], 5, seed=0)
