import pytest
from helpers import node, plan_of

from querydoctor.candidates import compare_plans

DDL = "CREATE INDEX ON orders (customer_id)"


def seq_plan(cost=61_527.0):
    scan = node("Seq Scan", relation="orders", cost=60_400.0, est_rows=10_900, explain_only=True)
    return plan_of(node("Sort", cost=cost, est_rows=10_900, plans=[scan], explain_only=True))


def index_plan(cost=2_200.0, index="<16401>btree_orders_customer_id"):
    scan = node("Index Scan", relation="orders", index=index, cost=2_100.0, est_rows=10_900, explain_only=True)
    return plan_of(node("Sort", cost=cost, est_rows=10_900, plans=[scan], explain_only=True))


def formula(c):
    return 0.35 * c.cost_reduction + 0.25 * c.rows_reduction + 0.20 * c.plan_risk_reduction + 0.20 * c.operator_improvement


def test_seq_scan_replaced_by_index_scan_is_a_clear_improvement():
    c = compare_plans(seq_plan(), index_plan(), DDL, table_rows={"orders": 1_824_403})
    assert c.uses_candidate is True
    assert c.removed_operators == ["Seq Scan"]
    assert c.added_operators == ["Index Scan"]
    assert c.cost_reduction == pytest.approx((61_527 - 2_200) / 61_527)
    assert c.rows_reduction == pytest.approx((1_824_403 - 10_900) / 1_824_403)  # rows read: whole table -> matches only
    assert c.plan_risk_reduction == pytest.approx(0.5)  # (seq scan + sort) -> (sort)
    assert c.operator_improvement == pytest.approx(0.7)  # seq scan became an index scan; the sort stays
    assert c.planner_score == pytest.approx(formula(c), abs=1e-9)
    assert 0.8 < c.planner_score <= 1.0


def test_without_table_sizes_rows_reduction_falls_back_to_plan_rows():
    c = compare_plans(seq_plan(), index_plan(), DDL)
    assert c.rows_reduction == 0.0


def test_a_candidate_the_planner_ignores_scores_zero():
    c = compare_plans(seq_plan(), seq_plan(cost=61_000.0), DDL)
    assert c.uses_candidate is False
    assert c.planner_score == 0.0
    assert c.removed_operators == [] and c.added_operators == []


def test_a_more_expensive_plan_clamps_every_improvement_to_zero():
    worse = index_plan(cost=90_000.0)
    c = compare_plans(seq_plan(), worse, DDL)
    assert c.cost_reduction == 0.0
    assert 0.0 <= c.planner_score <= 1.0


def test_removing_a_sort_counts_toward_operator_improvement():
    scan = node("Index Scan", relation="orders", index="<1>btree_x", cost=500.0, explain_only=True)
    after = plan_of(scan)
    before = plan_of(node("Sort", cost=3_000.0, explain_only=True, plans=[
        node("Index Scan", relation="orders", index="orders_pkey", cost=2_000.0, explain_only=True)]))
    c = compare_plans(before, after, DDL)
    assert c.removed_operators == ["Sort"]
    assert c.operator_improvement == pytest.approx(0.3)
    assert c.uses_candidate is True


def test_planner_score_is_always_within_zero_and_one():
    for before, after in [(seq_plan(), index_plan()), (index_plan(), seq_plan()), (seq_plan(), seq_plan())]:
        c = compare_plans(before, after, DDL, table_rows={"orders": 5})
        assert 0.0 <= c.planner_score <= 1.0
        for part in (c.cost_reduction, c.rows_reduction, c.plan_risk_reduction, c.operator_improvement):
            assert 0.0 <= part <= 1.0
