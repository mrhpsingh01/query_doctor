import pytest
from labdsn import TARGET_ADMIN_DSN, TARGET_RO_DSN

from querydoctor.lab.catalog import build_catalog
from querydoctor.lab.generator import COUNTS
from querydoctor.lab.workload import reset_statistics, run_workload, workload_plan
from querydoctor.safety import validate_query

CATALOG = {q.id: q for q in build_catalog("small")}
HOT = CATALOG["recent_orders_by_customer"]
SORT = CATALOG["unnecessary_large_sort"]


def test_the_plan_is_deterministic_and_every_call_is_a_valid_parameterised_select():
    a = workload_plan("small", "baseline", seed=7, scale_calls=0.1)
    b = workload_plan("small", "baseline", seed=7, scale_calls=0.1)
    c = workload_plan("small", "baseline", seed=8, scale_calls=0.1)
    assert a == b and a != c
    for call in a[:500]:
        assert validate_query(call.sql).param_count == len(call.params), call.query_id


def test_baseline_has_a_hot_moderately_slow_query_and_rare_heavy_ones_plus_cheap_lookups():
    plan = workload_plan("small", "baseline", seed=7, scale_calls=1.0)
    counts = {}
    for call in plan:
        counts[call.query_id] = counts.get(call.query_id, 0) + 1
    assert counts[HOT.id] >= 1000  # frequent: this is what should top the total-time ranking
    assert counts[SORT.id] <= 30 and counts["correlated_subquery"] <= 10  # rare and heavy
    assert {"pk_order_lookup", "pk_customer_lookup"} <= set(counts) and counts["pk_order_lookup"] >= 3000
    assert set(CATALOG) <= set(counts)  # every catalog query appears


def test_parameters_reference_real_rows_and_stay_in_range():
    n_customers, n_orders = COUNTS["small"]["customers"], COUNTS["small"]["orders"]
    for call in workload_plan("small", "baseline", seed=7, scale_calls=0.05):
        if call.query_id == "pk_order_lookup":
            assert 1 <= call.params[0] <= n_orders
        if call.query_id == "pk_customer_lookup":
            assert 1 <= call.params[0] <= n_customers


def test_regress_mode_runs_only_the_heavier_parameterisation_of_one_fingerprint():
    plan = workload_plan("small", "regress", seed=7, scale_calls=0.1)
    assert {c.query_id for c in plan} == {SORT.id}
    assert len(plan) >= 25  # enough interval calls for the detector's minimum, even when scaled down
    assert {c.params[0] for c in plan} <= {"page_view", "add_to_cart"}  # the frequent, big event types
    baseline_types = {c.params[0] for c in workload_plan("small", "baseline", seed=7, scale_calls=1.0) if c.query_id == SORT.id}
    assert baseline_types.isdisjoint({"page_view", "add_to_cart"})  # the baseline used the rare ones: same SQL, new shape of data


def test_scale_calls_scales_the_volume():
    small = len(workload_plan("small", "baseline", seed=7, scale_calls=0.1))
    big = len(workload_plan("small", "baseline", seed=7, scale_calls=0.5))
    assert 3 * small < big < 7 * small


@pytest.mark.integration
def test_a_frequent_moderately_slow_query_outranks_rare_heavy_ones_by_total_time():
    import psycopg

    from querydoctor.workload import merge_statements, read_statements
    from querydoctor.workload.analysis import rank

    reset_statistics(TARGET_ADMIN_DSN)
    done = run_workload(TARGET_RO_DSN, "small", "baseline", seed=7, scale_calls=0.2)
    assert done[HOT.id] >= 250
    with psycopg.connect(TARGET_RO_DSN) as conn:
        queries, _skipped = merge_statements(read_statements(conn, limit=200))
    ranked = rank(queries)
    by_fp = {r.fingerprint: r for r in ranked}
    hot, heavy = by_fp[validate_query(HOT.sql).fingerprint], by_fp[validate_query(SORT.sql).fingerprint]
    assert ranked[0].fingerprint == hot.fingerprint  # optimize this first
    assert heavy.mean_ms > 3 * hot.mean_ms  # ... even though the sort is far slower per call
    assert hot.total_ms > heavy.total_ms


@pytest.mark.integration
def test_the_regress_mode_is_detected_as_a_regression_between_two_snapshots():
    import psycopg

    from querydoctor.workload import merge_statements, read_statements
    from querydoctor.workload.analysis import detect_regressions

    def snapshot():
        with psycopg.connect(TARGET_RO_DSN) as conn:
            return merge_statements(read_statements(conn, limit=200))[0]

    reset_statistics(TARGET_ADMIN_DSN)
    run_workload(TARGET_RO_DSN, "small", "baseline", seed=7, scale_calls=0.2)
    before = snapshot()
    run_workload(TARGET_RO_DSN, "small", "regress", seed=7, scale_calls=0.1)
    regressions = detect_regressions(before, snapshot())
    assert validate_query(SORT.sql).fingerprint in [r.fingerprint for r in regressions]
    assert max(r.ratio for r in regressions) >= 1.5
