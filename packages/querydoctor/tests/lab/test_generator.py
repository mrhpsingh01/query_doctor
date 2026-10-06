from collections import Counter

import pytest

from querydoctor.lab.generator import COUNTS, generate, top_customers

CUSTOMERS, ORDERS, ITEMS, EVENTS = 10_000, 100_000, 300_000, 500_000


@pytest.fixture(scope="module")
def tables():
    return {name: [row for batch in batches for row in batch] for name, batches in generate("small")}


def test_row_counts_match_scale(tables):
    assert COUNTS["small"] == {"customers": CUSTOMERS, "orders": ORDERS, "order_items": ITEMS, "events": EVENTS}
    assert COUNTS["full"] == {"customers": 100_000, "orders": 1_000_000, "order_items": 3_000_000, "events": 5_000_000}
    assert len(tables["customers"]) == CUSTOMERS
    assert len(tables["orders"]) == ORDERS
    assert len(tables["order_items"]) == ITEMS
    assert len(tables["events"]) == EVENTS
    assert len(tables["products"]) == 2_000
    assert 0.70 * ORDERS < len(tables["payments"]) < 0.95 * ORDERS


def test_same_seed_is_identical_and_other_seed_differs():
    a = [r for b in generate("small", seed=42, tables=["orders"]) for r in next(iter(b[1]))[:100]]
    b = [r for b_ in generate("small", seed=42, tables=["orders"]) for r in next(iter(b_[1]))[:100]]
    c = [r for b_ in generate("small", seed=43, tables=["orders"]) for r in next(iter(b_[1]))[:100]]
    assert a == b
    assert a != c


def test_only_requested_tables_are_generated():
    assert [name for name, _ in generate("small", tables=["customers"])] == ["customers"]


def test_unknown_scale_rejected():
    with pytest.raises(ValueError):
        list(generate("huge"))


def test_customer_activity_is_zipf_skewed(tables):
    per_customer = Counter(row[1] for row in tables["orders"])
    top_1pct = sum(count for _, count in per_customer.most_common(CUSTOMERS // 100))
    assert top_1pct >= 0.20 * ORDERS
    assert top_customers("small", 1)[0] == per_customer.most_common(1)[0][0]


def test_order_statuses_are_uneven(tables):
    shares = sorted(c / ORDERS for c in Counter(row[2] for row in tables["orders"]).values())
    assert shares[-1] > 0.45
    assert shares[0] < 0.05


def test_orders_show_december_seasonality(tables):
    months = Counter(row[5].month if hasattr(row[5], "month") else int(row[5][5:7]) for row in tables["orders"])
    mean = ORDERS / 12
    assert months[12] > 1.3 * mean
    assert months[2] < mean


def test_foreign_keys_reference_existing_rows(tables):
    customer_ids = {row[0] for row in tables["customers"]}
    order_ids = {row[0] for row in tables["orders"]}
    assert {row[1] for row in tables["orders"]} <= customer_ids
    assert {row[1] for row in tables["order_items"]} <= order_ids
    assert {row[1] for row in tables["payments"]} <= order_ids
    assert {row[1] for row in tables["events"]} <= customer_ids
    product_ids = {row[0] for row in tables["products"]}
    assert {row[2] for row in tables["order_items"]} <= product_ids


def test_currency_is_correlated_with_customer_country(tables):
    country = {row[0]: row[2] for row in tables["customers"]}
    eur_countries = {"DE", "FR", "ES", "IT"}
    de_orders = [row for row in tables["orders"] if country[row[1]] == "DE"]
    assert de_orders
    assert sum(1 for row in de_orders if row[4] == "EUR") / len(de_orders) > 0.95
    assert all(country[row[1]] in eur_countries for row in tables["orders"] if row[4] == "EUR")


def test_pk_ids_are_dense_and_unique(tables):
    for name in ("customers", "orders", "order_items", "payments", "events", "products"):
        ids = [row[0] for row in tables[name]]
        assert ids == list(range(1, len(ids) + 1)), name
