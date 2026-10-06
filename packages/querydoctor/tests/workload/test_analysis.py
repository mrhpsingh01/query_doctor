import pytest

from querydoctor.workload import WorkloadQuery
from querydoctor.workload.analysis import (
    PlanChange,
    Regression,
    build_panels,
    detect_regressions,
    plan_changes,
    rank,
)


def q(fp, *, calls=10, mean=1.0, stddev=0.0, reads=0, temp=0, total=None, p95=None):
    total = calls * mean if total is None else total
    return WorkloadQuery(
        fingerprint=fp, normalized_sql=f"SELECT {fp}", sample_sql=f"SELECT {fp}", calls=calls, total_ms=total, mean_ms=mean,
        stddev_ms=stddev, min_ms=0.0, max_ms=mean * 3, rows=calls, shared_hit_blocks=0, shared_read_blocks=reads,
        temp_blocks=temp, p95_est_ms=p95 if p95 is not None else mean + 1.645 * stddev,
    )


def test_a_cheap_query_called_often_outranks_a_slow_query_called_once():
    ranked = rank([q("rare_heavy", calls=1, mean=5000.0), q("hot_cheap", calls=50_000, mean=50.0)])
    assert [r.fingerprint for r in ranked] == ["hot_cheap", "rare_heavy"]
    assert [r.rank for r in ranked] == [1, 2]
    assert sum(r.share for r in ranked) == pytest.approx(1.0)
    assert ranked[0].share == pytest.approx(2_500_000 / 2_505_000)


def test_rank_of_nothing_and_of_zero_total_time_is_safe():
    assert rank([]) == []
    assert [r.share for r in rank([q("a", calls=1, mean=0.0, total=0.0)])] == [0.0]


def test_panels_order_and_select_the_right_queries():
    qs = [
        q("big_total", calls=1000, mean=10.0, stddev=1.0),
        q("slow_p95", calls=30, mean=5.0, stddev=40.0),
        q("frequent", calls=900_000, mean=0.01),
        q("io_heavy", calls=10, mean=2.0, reads=5000, temp=100),
        q("unstable", calls=50, mean=2.0, stddev=8.0),
        q("rare_unstable", calls=3, mean=2.0, stddev=50.0),  # too few calls to call unstable
    ]
    panels = build_panels(qs, validated_fingerprints={"big_total"}, regressions=[])
    ids = lambda lst: [x.fingerprint for x in lst]  # noqa: E731
    assert ids(panels.highest_total_time)[0] == "big_total"
    assert ids(panels.slowest_p95)[0] == "rare_unstable" and ids(panels.slowest_p95)[1] == "slow_p95"
    assert ids(panels.most_frequent)[0] == "frequent"
    assert ids(panels.highest_io) == ["io_heavy"]  # only queries that did I/O
    # coefficient of variation: slow_p95 40/5 = 8, unstable 8/2 = 4; rare_unstable (CV 25) has too few calls to count
    assert ids(panels.most_unstable)[:2] == ["slow_p95", "unstable"] and "rare_unstable" not in ids(panels.most_unstable)
    assert "big_total" not in ids(panels.missing_validation) and "frequent" in ids(panels.missing_validation)
    totals = [x.total_ms for x in panels.missing_validation]
    assert totals == sorted(totals, reverse=True)  # what to validate first: the heaviest unvalidated query
    assert panels.largest_regression == []


def test_regression_uses_the_interval_since_the_earlier_snapshot():
    before = [q("a", calls=1000, mean=10.0)]
    after = [q("a", calls=1100, total=10_000.0 + 100 * 25.0)]  # the 100 new calls averaged 25 ms vs a 10 ms history
    (r,) = detect_regressions(before, after)
    assert isinstance(r, Regression)
    assert (r.baseline_mean_ms, r.recent_mean_ms, r.recent_calls) == (10.0, 25.0, 100)
    assert r.ratio == pytest.approx(2.5)


@pytest.mark.parametrize(
    "recent_mean,new_calls,expected",
    [(14.9, 100, 0), (15.0, 100, 1), (30.0, 19, 0), (30.0, 20, 1)],  # ratio 1.5 and 20-call boundaries
)
def test_regression_thresholds(recent_mean, new_calls, expected):
    before = [q("a", calls=100, mean=10.0)]
    after = [q("a", calls=100 + new_calls, total=1000.0 + new_calls * recent_mean)]
    assert len(detect_regressions(before, after)) == expected


def test_regression_ignores_unmatched_reset_and_degenerate_cases():
    before = [q("only_before"), q("reset", calls=500, mean=5.0), q("zero", calls=10, mean=0.0, total=0.0)]
    after = [q("only_after"), q("reset", calls=10, mean=50.0), q("zero", calls=100, total=500.0)]
    assert detect_regressions(before, after) == []
    assert detect_regressions([], []) == []


def test_largest_regression_panel_is_sorted_by_ratio():
    regs = [Regression(fingerprint="a", baseline_mean_ms=1, recent_mean_ms=2, ratio=2.0, recent_calls=30),
            Regression(fingerprint="b", baseline_mean_ms=1, recent_mean_ms=9, ratio=9.0, recent_calls=30)]
    panels = build_panels([q("a"), q("b")], validated_fingerprints=set(), regressions=regs)
    assert [r.fingerprint for r in panels.largest_regression] == ["b", "a"]


def test_plan_changes_report_each_switch_with_the_operators():
    analyses = [
        {"id": 1, "plan_fingerprint": "A", "operators": ["Seq Scan", "Sort"]},
        {"id": 2, "plan_fingerprint": "A", "operators": ["Seq Scan", "Sort"]},
        {"id": 3, "plan_fingerprint": "B", "operators": ["Index Scan"]},
        {"id": 4, "plan_fingerprint": "B", "operators": ["Index Scan"]},
        {"id": 5, "plan_fingerprint": "C", "operators": ["Bitmap Heap Scan"]},
        {"id": 6, "plan_fingerprint": None, "operators": []},
    ]
    changes = plan_changes(analyses)
    assert [(c.before_analysis_id, c.after_analysis_id) for c in changes] == [(2, 3), (4, 5)]
    assert isinstance(changes[0], PlanChange)
    assert changes[0].before_operators == ["Seq Scan", "Sort"] and changes[0].after_operators == ["Index Scan"]
    assert plan_changes([]) == [] and plan_changes(analyses[:1]) == []
