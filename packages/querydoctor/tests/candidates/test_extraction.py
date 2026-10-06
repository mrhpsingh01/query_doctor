from querydoctor.candidates import TableUsage, extract_usage
from querydoctor.lab.catalog import build_catalog
from querydoctor.schema import SchemaSnapshot, TableInfo


def test_equality_range_order_and_output_for_the_headline_query():
    usage = extract_usage(
        "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"
    )
    assert set(usage) == {"orders"}
    orders = usage["orders"]
    assert isinstance(orders, TableUsage)
    assert orders.equality == ["customer_id"]
    assert orders.range == ["created_at"]
    assert orders.order_by == [("created_at", True)]
    assert orders.output == ["id", "total_amount"]


def test_join_keys_resolve_aliases_to_table_names():
    usage = extract_usage("SELECT o.id, p.amount FROM orders o JOIN payments p ON p.order_id = o.id WHERE o.status = $1")
    assert usage["payments"].join == ["order_id"]
    assert usage["orders"].join == ["id"]
    assert usage["orders"].equality == ["status"]
    assert usage["payments"].output == ["amount"]
    assert usage["orders"].output == ["id"]


def test_between_and_comparisons_are_ranges_and_in_is_equality():
    usage = extract_usage(
        "SELECT 1 FROM orders WHERE created_at BETWEEN $1 AND $2 AND total_amount > 5 AND total_amount < 900 "
        "AND status IN ('a', 'b') AND id = 7 AND currency <> 'USD'"
    )["orders"]
    assert usage.range == ["created_at", "total_amount"]
    assert usage.equality == ["status", "id"]  # <> is not indexable, so it is ignored


def test_group_by_is_captured():
    usage = extract_usage("SELECT status, count(*) FROM orders WHERE customer_id = $1 GROUP BY status")["orders"]
    assert usage.group_by == ["status"]
    assert usage.equality == ["customer_id"]


def test_function_on_a_column_is_an_expression_not_a_plain_equality():
    usage = extract_usage("SELECT id, email FROM customers WHERE lower(email) = $1")["customers"]
    assert usage.equality == []
    assert usage.expressions == ["lower(email)"]


def test_subquery_tables_are_extracted_in_their_own_scope():
    usage = extract_usage(
        "SELECT c.id FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o WHERE o.status = $1)"
    )
    assert usage["orders"].equality == ["status"]
    assert usage["customers"].equality == ["id"]


def test_correlated_subquery_attributes_the_inner_lookup_to_the_inner_table():
    sql = next(q.sql for q in build_catalog("small") if q.id == "correlated_subquery")
    usage = extract_usage(sql)
    assert usage["orders"].equality == ["customer_id"]
    assert usage["orders"].output == ["created_at"]
    assert usage["customers"].equality == ["segment"]
    assert usage["customers"].range == ["id"]


def test_unqualified_columns_resolve_in_single_table_queries():
    assert extract_usage("SELECT id FROM orders WHERE status = 'x'")["orders"].equality == ["status"]


def test_unqualified_columns_are_skipped_when_ambiguous_and_resolved_with_a_schema():
    sql = "SELECT 1 FROM orders o JOIN payments p ON p.order_id = o.id WHERE method = $1"
    assert extract_usage(sql)["payments"].equality == []
    schema = SchemaSnapshot(
        tables={
            "orders": TableInfo(name="orders", row_estimate=1, columns={"id": "integer"}),
            "payments": TableInfo(name="payments", row_estimate=1, columns={"method": "text", "order_id": "integer"}),
        }
    )
    assert extract_usage(sql, schema=schema)["payments"].equality == ["method"]


def test_select_star_is_flagged_instead_of_listing_columns():
    usage = extract_usage("SELECT * FROM orders WHERE customer_id = $1")["orders"]
    assert usage.select_star is True and usage.output == []


def test_every_catalog_query_extracts_without_error():
    for q in build_catalog("small"):
        assert extract_usage(q.sql), q.id


def test_unparseable_sql_gives_an_empty_result_instead_of_raising():
    assert extract_usage("SELEC nonsense ((") == {}
