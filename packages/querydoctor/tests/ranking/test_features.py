import math

import pytest
from helpers import node, plan_of

from querydoctor.candidates import IndexCandidate, IndexColumn, compare_plans, extract_usage, generate_candidates
from querydoctor.lab.catalog import build_catalog
from querydoctor.ranking import DESCRIPTORS, FEATURE_NAMES, FEATURE_VERSION, FeatureInput, extract_features
from querydoctor.schema import ColumnStats, IndexInfo, SchemaSnapshot, TableInfo

GOLDEN_NAMES = [
    # query
    "q_tables", "q_joins", "q_equality_cols", "q_range_cols", "q_join_cols", "q_order_cols", "q_group_cols", "q_output_cols",
    "q_select_star", "q_subqueries", "q_expression_preds", "q_has_limit",
    # the candidate's table
    "t_log_rows", "t_log_size", "t_columns", "t_indexes", "t_write_ratio", "t_key_distinct", "t_key_null", "t_key_corr", "t_has_extras",
    # the original plan
    "p_seq_scans", "p_index_scans", "p_sorts", "p_joins", "p_aggregates", "p_nodes", "p_depth", "p_log_cost", "p_log_root_rows", "p_log_seq_rows",
    # the candidate
    "c_key_cols", "c_include_cols", "c_partial", "c_expression", "c_desc_cols", "c_key_width", "c_overlap", "c_lead_equality",
    "c_lead_join", "c_lead_range", "c_lead_order", "c_equality_cov", "c_range_cov", "c_sort_served", "c_output_cov",
    # the difference between the hypothetical plan and the original
    "d_has_plan", "d_cost_reduction", "d_rows_reduction", "d_risk_reduction", "d_operator_improvement", "d_planner_score",
    "d_uses_candidate", "d_removed_seq_scans", "d_added_index_scans", "d_sort_removed", "d_join_changed", "d_depth_delta", "d_node_delta",
]

HEADLINE = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"
KEY_ONLY = "CREATE INDEX ON orders (customer_id, created_at DESC)"
COVERING = "CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)"


def table(name, rows=100_000, **columns):
    return TableInfo(name=name, row_estimate=rows, columns=columns)


def shop_schema(extras=False, indexes=None) -> SchemaSnapshot:
    orders = table("orders", id="integer", customer_id="integer", total_amount="numeric", created_at="timestamp", status="text", currency="text")
    if extras:
        orders.size_bytes = 12_000_000
        orders.write_ratio = 0.2
        orders.column_stats = {
            "customer_id": ColumnStats(n_distinct_fraction=0.01, null_frac=0.0, correlation=0.02),
            "created_at": ColumnStats(n_distinct_fraction=0.9, null_frac=0.1, correlation=-0.98),
        }
    return SchemaSnapshot(
        tables={
            "orders": orders,
            "payments": table("payments", id="integer", order_id="integer", amount="numeric", method="text"),
            "customers": table("customers", id="integer", email="text", country="text"),
        },
        indexes=indexes or {},
    )


def seq_plan():
    scan = node("Seq Scan", relation="orders", cost=60_400.0, est_rows=10_900, explain_only=True)
    return plan_of(node("Sort", cost=61_527.0, est_rows=10_900, plans=[scan], explain_only=True))


def index_plan(with_sort=True):
    scan = node("Index Scan", relation="orders", index="<1>btree_orders_customer_id", cost=2_100.0, est_rows=10_900, explain_only=True)
    return plan_of(node("Sort", cost=2_200.0, est_rows=10_900, plans=[scan], explain_only=True) if with_sort else scan)


def build(sql, ddl, *, schema=None, candidate_plan="none", original=None):
    schema = schema or shop_schema()
    candidate = next(c for c in generate_candidates(sql, schema, []) if c.ddl == ddl)
    original = original or seq_plan()
    comparison = None
    if candidate_plan == "index":
        candidate_plan = index_plan()
    if candidate_plan == "none":
        candidate_plan = None
    if candidate_plan is not None:
        comparison = compare_plans(original, candidate_plan, ddl, {"orders": 100_000})
    return FeatureInput(
        sql=sql, usage=extract_usage(sql, schema), schema=schema, original=original, candidate=candidate,
        candidate_plan=candidate_plan, comparison=comparison,
    )


def feats(*args, **kw):
    return extract_features(build(*args, **kw))


def test_names_are_unique_versioned_and_stable():
    assert FEATURE_VERSION == 1
    assert FEATURE_NAMES == GOLDEN_NAMES
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


def test_every_feature_is_present_and_finite_even_without_a_candidate_plan_or_schema_extras():
    f = feats(HEADLINE, KEY_ONLY)
    assert list(f) == FEATURE_NAMES
    assert all(math.isfinite(v) for v in f.values())


def test_query_features_on_the_headline_query():
    f = feats(HEADLINE, KEY_ONLY)
    assert (f["q_tables"], f["q_joins"], f["q_equality_cols"], f["q_range_cols"], f["q_order_cols"]) == (1, 0, 1, 1, 1)
    assert (f["q_join_cols"], f["q_group_cols"], f["q_output_cols"], f["q_select_star"], f["q_subqueries"]) == (0, 0, 2, 0, 0)
    assert f["q_has_limit"] == 0 and f["q_expression_preds"] == 0


def test_query_features_on_a_join_a_select_star_and_a_subquery():
    cat = {q.id: q.sql for q in build_catalog("small")}
    join = feats(cat["orders_payments_join"], "CREATE INDEX ON payments (order_id)")
    assert (join["q_tables"], join["q_joins"], join["q_join_cols"], join["c_lead_join"], join["c_lead_equality"]) == (2, 1, 2, 1, 0)  # a join key is counted on both sides: orders.id and payments.order_id
    star = extract_features(build(cat["select_star_wide_join"], "CREATE INDEX ON orders (status, created_at)", schema=shop_schema()))
    assert star["q_select_star"] == 1 and star["q_tables"] >= 2
    sub = feats(cat["correlated_subquery"], "CREATE INDEX ON orders (customer_id)")
    assert sub["q_subqueries"] >= 1


def test_candidate_features_on_the_headline_candidates():
    key_only = feats(HEADLINE, KEY_ONLY)
    assert (key_only["c_key_cols"], key_only["c_include_cols"], key_only["c_desc_cols"]) == (2, 0, 1)
    assert (key_only["c_lead_equality"], key_only["c_lead_range"], key_only["c_lead_order"], key_only["c_lead_join"]) == (1, 0, 0, 0)
    assert key_only["c_equality_cov"] == 1.0 and key_only["c_range_cov"] == 1.0
    assert key_only["c_sort_served"] == 1  # equality on customer_id, then created_at is the ORDER BY
    assert key_only["c_output_cov"] == 0.0  # id and total_amount are not in the index
    assert key_only["c_partial"] == 0 and key_only["c_expression"] == 0 and key_only["c_key_width"] == 12  # int4 + timestamp
    covering = feats(HEADLINE, COVERING)
    assert covering["c_output_cov"] == 1.0 and covering["c_include_cols"] == 2


def test_a_decoy_that_leads_with_an_unrelated_column_has_no_leading_role():
    sql = "SELECT id FROM orders WHERE customer_id = $1"
    schema = shop_schema()
    decoy = IndexCandidate(
        id="x", table="orders", columns=[IndexColumn(name="currency")], ddl="CREATE INDEX ON orders (currency)", rationale=["decoy"]
    )
    fi = build(sql, "CREATE INDEX ON orders (customer_id)", schema=schema)
    fi.candidate = decoy
    f = extract_features(fi)
    assert (f["c_lead_equality"], f["c_lead_join"], f["c_lead_range"], f["c_lead_order"]) == (0, 0, 0, 0)
    assert f["c_equality_cov"] == 0.0 and f["c_sort_served"] == 0


def test_overlap_with_an_existing_index_is_the_shared_leading_prefix():
    existing = {"orders": [IndexInfo(name="i1", table="orders", columns=["customer_id"])]}
    f = feats(HEADLINE, KEY_ONLY, schema=shop_schema(indexes=existing))
    assert f["c_overlap"] == 0.5 and f["t_indexes"] == 1
    assert feats(HEADLINE, KEY_ONLY)["c_overlap"] == 0.0


def test_table_features_use_extras_when_present_and_neutral_defaults_when_not():
    plain = feats(HEADLINE, KEY_ONLY)
    assert plain["t_has_extras"] == 0 and plain["t_log_size"] == 0.0 and plain["t_write_ratio"] == 0.0
    assert (plain["t_key_distinct"], plain["t_key_null"], plain["t_key_corr"]) == (0.5, 0.0, 0.0)
    assert plain["t_log_rows"] == pytest.approx(math.log1p(100_000)) and plain["t_columns"] == 6
    rich = feats(HEADLINE, KEY_ONLY, schema=shop_schema(extras=True))
    assert rich["t_has_extras"] == 1 and rich["t_log_size"] == pytest.approx(math.log1p(12_000_000))
    assert rich["t_write_ratio"] == 0.2
    assert rich["t_key_distinct"] == pytest.approx((0.01 + 0.9) / 2) and rich["t_key_null"] == pytest.approx(0.05)
    assert rich["t_key_corr"] == pytest.approx((0.02 + 0.98) / 2)  # absolute correlation


def test_original_plan_features():
    f = feats(HEADLINE, KEY_ONLY)
    assert (f["p_seq_scans"], f["p_sorts"], f["p_index_scans"], f["p_joins"], f["p_nodes"], f["p_depth"]) == (1, 1, 0, 0, 2, 2)
    assert f["p_log_cost"] == pytest.approx(math.log1p(61_527.0)) and f["p_log_root_rows"] == pytest.approx(math.log1p(10_900))
    assert f["p_log_seq_rows"] == pytest.approx(math.log1p(10_900))


def test_plan_difference_features_from_a_seq_to_index_plan():
    f = feats(HEADLINE, KEY_ONLY, candidate_plan="index")
    assert f["d_has_plan"] == 1 and f["d_uses_candidate"] == 1
    assert (f["d_removed_seq_scans"], f["d_added_index_scans"], f["d_sort_removed"], f["d_join_changed"]) == (1, 1, 0, 0)
    assert f["d_cost_reduction"] == pytest.approx((61_527 - 2_200) / 61_527)
    assert 0.8 < f["d_planner_score"] <= 1.0 and f["d_node_delta"] == 0 and f["d_depth_delta"] == 0


def test_a_removed_sort_and_a_changed_join_algorithm_are_flagged():
    scan = node("Index Scan", relation="orders", index="<1>btree_x", cost=2_100.0, est_rows=10_900, explain_only=True)
    no_sort = plan_of(scan)
    f = feats(HEADLINE, KEY_ONLY, candidate_plan=no_sort)
    assert f["d_sort_removed"] == 1 and f["d_node_delta"] == -1 and f["d_depth_delta"] == -1
    a = plan_of(node("Nested Loop", plans=[node("Seq Scan", relation="orders", explain_only=True), node("Seq Scan", relation="payments", explain_only=True)], explain_only=True))
    b = plan_of(node("Hash Join", plans=[node("Index Scan", relation="orders", index="<1>btree_x", explain_only=True), node("Seq Scan", relation="payments", explain_only=True)], explain_only=True))
    assert feats(HEADLINE, KEY_ONLY, candidate_plan=b, original=a)["d_join_changed"] == 1


def test_without_a_candidate_plan_all_difference_features_are_zero():
    f = feats(HEADLINE, KEY_ONLY)
    assert all(f[name] == 0 for name in FEATURE_NAMES if name.startswith("d_"))


def test_every_feature_has_a_plain_language_descriptor():
    assert set(DESCRIPTORS) == set(FEATURE_NAMES)
    assert all(isinstance(v, str) and 10 < len(v) < 90 and "AI" not in v for v in DESCRIPTORS.values())
