import json
import pathlib

import pytest

from querydoctor.lab.catalog import SampleQuery, build_catalog
from querydoctor.safety import validate_query

FIXTURE = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "sample_queries" / "catalog.json"

EXPECTED_IDS = [
    "recent_orders_by_customer",
    "orders_by_status_and_date",
    "orders_payments_join",
    "unnecessary_large_sort",
    "function_on_indexed_column",
    "select_star_wide_join",
    "correlated_subquery",
    "correlated_filter_misestimate",
]


@pytest.fixture(scope="module")
def catalog() -> list[SampleQuery]:
    return build_catalog("small")


def test_has_the_eight_catalog_queries_in_order(catalog):
    assert [q.id for q in catalog] == EXPECTED_IDS
    assert len({q.id for q in catalog}) == 8


def test_every_query_is_valid_select_with_matching_param_count(catalog):
    for q in catalog:
        vq = validate_query(q.sql)
        assert vq.param_count == len(q.params), q.id
        assert q.title and q.description and q.expected_rule_ids, q.id


def test_expected_rules_are_real_rule_ids(catalog):
    from querydoctor.diagnosis import ALL_RULES

    # ALL_RULES grows to ten in v0.2; the catalog may only reference rules that exist by then.
    known = {
        "PG_SEQ_SCAN_FILTER_WASTE", "PG_HIGH_FILTER_WASTE", "PG_CARDINALITY_MISESTIMATE", "PG_REPEATED_NESTED_LOOP",
        "PG_SORT_SPILL", "PG_HASH_SPILL", "PG_INDEX_FILTER_REMAINDER", "PG_FUNCTION_ON_COLUMN", "PG_WIDE_RESULT",
        "PG_DUPLICATE_SUBTREE",
    }
    assert {r.rule_id for r in ALL_RULES} <= known
    for q in catalog:
        assert set(q.expected_rule_ids) <= known, q.id


def test_catalog_candidates_name_table_and_columns(catalog):
    q1 = next(q for q in catalog if q.id == "recent_orders_by_customer")
    assert (q1.expected_candidate_table, q1.expected_candidate_columns) == ("orders", ["customer_id", "created_at"])
    q3 = next(q for q in catalog if q.id == "orders_payments_join")
    assert (q3.expected_candidate_table, q3.expected_candidate_columns) == ("payments", ["order_id"])


def test_customer_param_is_the_heaviest_customer(catalog):
    from querydoctor.lab.generator import top_customers

    q1 = next(q for q in catalog if q.id == "recent_orders_by_customer")
    assert q1.params[0] == top_customers("small", 1)[0]


def test_the_join_query_filters_a_light_customer_so_an_index_lookup_beats_a_hash_join(catalog):
    from querydoctor.lab.generator import customer_order_count

    q3 = next(q for q in catalog if q.id == "orders_payments_join")
    q1 = next(q for q in catalog if q.id == "recent_orders_by_customer")
    assert q3.params[0] != q1.params[0]
    assert 1 <= customer_order_count("small", q3.params[0]) <= 25
    assert customer_order_count("small", q1.params[0]) > 500  # the headline query's customer is the heavy one


def test_committed_json_matches_catalog(catalog):
    committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert committed == [q.model_dump() for q in catalog]
