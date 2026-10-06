"""The eight deliberately slow queries of the sample shop, each with the diagnosis and candidate we expect.

Parameters are JSON-friendly values bound positionally to ``$1..$n``. The heaviest customer id depends on the data
scale, hence ``build_catalog(scale)``.
"""

from __future__ import annotations

import json
import pathlib

from pydantic import BaseModel, Field

from querydoctor.lab.generator import light_customer, top_customers


class SampleQuery(BaseModel):
    id: str
    title: str
    sql: str
    params: list = Field(default_factory=list)
    description: str
    expected_rule_ids: list[str]
    expected_candidate_table: str | None = None
    expected_candidate_columns: list[str] | None = None


def build_catalog(scale: str = "small") -> list[SampleQuery]:
    heavy = top_customers(scale, 1)[0]
    return [
        SampleQuery(
            id="recent_orders_by_customer",
            title="Recent orders for one customer",
            sql="SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC",
            params=[heavy, "2022-01-01"],
            description="orders has no index on customer_id, so every order is read to find one customer's.",
            expected_rule_ids=["PG_SEQ_SCAN_FILTER_WASTE"],
            expected_candidate_table="orders",
            expected_candidate_columns=["customer_id", "created_at"],
        ),
        SampleQuery(
            id="orders_by_status_and_date",
            title="Orders by status in a date window",
            sql="SELECT id, customer_id, total_amount FROM orders "
            "WHERE status = $1 AND created_at >= $2 AND created_at < $3",
            params=["pending", "2023-11-01", "2023-12-01"],
            description="An equality on a skewed status plus a narrow date range, with nothing to narrow the scan.",
            expected_rule_ids=["PG_SEQ_SCAN_FILTER_WASTE"],
            expected_candidate_table="orders",
            expected_candidate_columns=["status", "created_at"],
        ),
        SampleQuery(
            id="orders_payments_join",
            title="Orders joined to payments on an unindexed key",
            sql="SELECT o.id, p.amount, p.method FROM orders o JOIN payments p ON p.order_id = o.id "
            "WHERE o.customer_id = $1",
            params=[light_customer(scale)],
            description="payments.order_id is unindexed, so even a handful of orders makes the join scan all payments.",
            expected_rule_ids=["PG_SEQ_SCAN_FILTER_WASTE"],
            expected_candidate_table="payments",
            expected_candidate_columns=["order_id"],
        ),
        SampleQuery(
            id="unnecessary_large_sort",
            title="Sorting a large result for no reason",
            sql="SELECT id, customer_id, payload, created_at FROM events WHERE event_type = $1 ORDER BY payload",
            params=["page_view"],
            description="Hundreds of thousands of rows are sorted by an arbitrary text column, spilling to disk.",
            expected_rule_ids=["PG_SORT_SPILL"],
        ),
        SampleQuery(
            id="function_on_indexed_column",
            title="Function applied to an indexed column",
            sql="SELECT id, email FROM customers WHERE lower(email) = $1",
            params=["user42@example.com"],
            description="idx_customers_email exists, but lower(email) cannot use a plain index on email.",
            expected_rule_ids=["PG_FUNCTION_ON_COLUMN"],
            expected_candidate_table="customers",
            expected_candidate_columns=["lower(email)"],
        ),
        SampleQuery(
            id="select_star_wide_join",
            title="SELECT * across a four-table join",
            sql="SELECT * FROM order_items i JOIN orders o ON o.id = i.order_id "
            "JOIN customers c ON c.id = o.customer_id JOIN products p ON p.id = i.product_id "
            "WHERE o.status = $1 AND o.created_at >= $2",
            params=["refunded", "2023-06-01"],
            description="Every column of four tables is fetched although a handful would do.",
            expected_rule_ids=["PG_WIDE_RESULT"],
        ),
        SampleQuery(
            id="correlated_subquery",
            title="Correlated subquery run once per customer",
            sql="SELECT c.id, (SELECT max(o.created_at) FROM orders o WHERE o.customer_id = c.id) AS last_order "
            "FROM customers c WHERE c.segment = $1 AND c.id <= $2",
            params=["regular", 150],
            description="The subquery scans orders again for every customer returned by the outer query.",
            expected_rule_ids=["PG_REPEATED_NESTED_LOOP", "PG_SEQ_SCAN_FILTER_WASTE"],
            expected_candidate_table="orders",
            expected_candidate_columns=["customer_id"],
        ),
        SampleQuery(
            id="correlated_filter_misestimate",
            title="Correlated filters the planner underestimates",
            sql="SELECT o.id, o.total_amount FROM orders o JOIN customers c ON c.id = o.customer_id "
            "WHERE c.country = $1 AND o.currency = $2",
            params=["JP", "JPY"],
            description="Country and currency are correlated; the planner multiplies their selectivities as if independent.",
            expected_rule_ids=["PG_CARDINALITY_MISESTIMATE"],
        ),
    ]


def write_fixture(path: pathlib.Path, scale: str = "small") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([q.model_dump() for q in build_catalog(scale)], indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_fixture(pathlib.Path(__file__).resolve().parents[5] / "fixtures" / "sample_queries" / "catalog.json")
