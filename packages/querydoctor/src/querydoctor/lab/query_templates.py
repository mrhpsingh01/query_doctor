"""Query templates over the sample shop, with parameter samplers: the queries the ranker's dataset is built from.

Each template is a fixed SQL shape ($1..$n placeholders); an *instance* is a template with sampled parameters on one
data variant. The templates deliberately span equality, range, join, sort, group, subquery, expression and ``IN``
queries with low and high selectivity, so a model sees both queries an index helps and queries it cannot.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from querydoctor.lab.generator import COUNTS, PRODUCT_DIVISOR, top_customers
from querydoctor.safety import validate_query

DATA_SEED = 42  # the seed every variant is generated with
STATUSES = ["completed", "shipped", "pending", "processing", "cancelled", "refunded"]
RARE_STATUSES = ["pending", "processing", "cancelled", "refunded"]
COUNTRIES = ["US", "GB", "DE", "FR", "ES", "IT", "IN", "BR", "CA", "AU", "JP"]
SEGMENTS = ["regular", "vip", "new"]
METHODS = ["card", "paypal", "bank_transfer"]
EVENT_TYPES = ["page_view", "add_to_cart", "search", "checkout", "login"]
RARE_EVENT_TYPES = ["checkout", "login", "search"]
CATEGORIES = ["books", "electronics", "home", "toys", "garden", "sports", "beauty", "grocery", "pets", "auto", "music", "tools"]


@dataclass(frozen=True)
class Variant:
    """What a sampler needs to know about one data variant."""

    name: str
    customers: int
    orders: int
    products: int
    heavy: tuple[int, ...]  # the most active customers
    light: tuple[int, ...]  # customers from the long tail


@lru_cache(maxsize=8)
def variant_info(name: str) -> Variant:
    counts = COUNTS[name]
    n = counts["customers"]
    ranked = top_customers(name, max(n // 5, 10), DATA_SEED)
    return Variant(
        name=name, customers=n, orders=counts["orders"], products=max(n // PRODUCT_DIVISOR, 1),
        heavy=tuple(ranked[: max(n // 100, 3)]), light=tuple(ranked[-max(n // 10, 5):]),
    )


@dataclass(frozen=True)
class QueryTemplate:
    id: str
    sql: str
    sampler: Callable[[random.Random, Variant], list]


@dataclass(frozen=True)
class QueryInstance:
    template_id: str
    variant: str
    sql: str
    params: list
    fingerprint: str
    query_id: str  # fingerprint:variant:parameter-hash: groups the candidates evaluated for one concrete query


# ---- sampling helpers ---------------------------------------------------------------------------------------------

def _customer(r: random.Random, v: Variant) -> int:
    pick = r.random()
    return r.choice(v.heavy) if pick < 0.35 else r.choice(v.light) if pick < 0.65 else r.randint(1, v.customers)


def _date(r: random.Random) -> str:
    return f"{r.choice((2021, 2022, 2023))}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"


def _window(r: random.Random, days: tuple[int, ...] = (1, 3, 7, 30)) -> list[str]:
    from datetime import date, timedelta

    start = date(r.choice((2021, 2022, 2023)), r.randint(1, 12), r.randint(1, 28))
    return [start.isoformat(), (start + timedelta(days=r.choice(days))).isoformat()]


def _order_id(r: random.Random, v: Variant) -> int:
    return r.randint(1, v.orders)


def _date_after_2022(r: random.Random) -> str:
    return f"{r.choice((2022, 2023))}-{r.randint(1, 12):02d}-01"


T = QueryTemplate
TEMPLATES: list[QueryTemplate] = [
    # orders
    T("orders_by_customer", "SELECT id, total_amount FROM orders WHERE customer_id = $1", lambda r, v: [_customer(r, v)]),
    T("orders_by_customer_recent",
      "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC",
      lambda r, v: [_customer(r, v), _date(r)]),
    T("orders_by_status", "SELECT id, customer_id FROM orders WHERE status = $1", lambda r, v: [r.choice(RARE_STATUSES)]),
    T("orders_by_status_date_window",
      "SELECT id, customer_id, total_amount FROM orders WHERE status = $1 AND created_at >= $2 AND created_at < $3",
      lambda r, v: [r.choice(STATUSES), *_window(r, (7, 30))]),
    T("orders_date_range", "SELECT id, total_amount FROM orders WHERE created_at >= $1 AND created_at < $2",
      lambda r, v: _window(r, (1, 3))),
    T("orders_by_currency_status", "SELECT id, total_amount FROM orders WHERE currency = $1 AND status = $2",
      lambda r, v: [r.choice(["JPY", "BRL", "AUD", "CAD", "INR"]), r.choice(RARE_STATUSES)]),
    T("orders_top_by_amount",
      "SELECT id, customer_id, total_amount FROM orders WHERE status = $1 ORDER BY total_amount DESC LIMIT 20",
      lambda r, v: [r.choice(STATUSES)]),
    T("orders_by_customer_status", "SELECT id, created_at FROM orders WHERE customer_id = $1 AND status = $2",
      lambda r, v: [_customer(r, v), r.choice(STATUSES)]),
    T("orders_by_customer_list", "SELECT id, total_amount FROM orders WHERE customer_id IN ($1, $2, $3)",
      lambda r, v: [_customer(r, v) for _ in range(3)]),
    T("orders_by_id_list", "SELECT id, status FROM orders WHERE id IN ($1, $2, $3)",
      lambda r, v: [_order_id(r, v) for _ in range(3)]),
    T("orders_shipped_window", "SELECT id, shipped_at FROM orders WHERE shipped_at >= $1 AND shipped_at < $2",
      lambda r, v: _window(r, (1, 3))),
    T("orders_amount_range", "SELECT id, customer_id FROM orders WHERE total_amount > $1 AND total_amount < $2",
      lambda r, v: (lambda lo: [lo, lo + r.choice((5, 50))])(r.choice((20, 40, 200, 400)))),
    T("orders_count_by_status", "SELECT status, count(*) FROM orders WHERE created_at >= $1 GROUP BY status",
      lambda r, v: [_date_after_2022(r)]),
    T("orders_spend_by_customer",
      "SELECT customer_id, sum(total_amount) FROM orders WHERE status = $1 GROUP BY customer_id ORDER BY 2 DESC LIMIT 10",
      lambda r, v: [r.choice(RARE_STATUSES)]),
    T("orders_by_day_expression", "SELECT id, total_amount FROM orders WHERE date_trunc('day', created_at) = $1",
      lambda r, v: [_date(r)]),
    T("orders_by_year_expression", "SELECT id FROM orders WHERE extract(year FROM created_at) = $1 AND status = $2",
      lambda r, v: [r.choice((2021, 2022, 2023)), r.choice(RARE_STATUSES)]),
    # payments
    T("payments_by_order", "SELECT amount, method FROM payments WHERE order_id = $1", lambda r, v: [_order_id(r, v)]),
    T("payments_by_method_window",
      "SELECT id, amount FROM payments WHERE method = $1 AND paid_at >= $2 AND paid_at < $3",
      lambda r, v: [r.choice(METHODS), *_window(r, (1, 7))]),
    T("payments_sum_by_method", "SELECT method, sum(amount) FROM payments WHERE paid_at >= $1 GROUP BY method",
      lambda r, v: [_date_after_2022(r)]),
    T("payments_join_orders",
      "SELECT o.id, p.amount, p.method FROM orders o JOIN payments p ON p.order_id = o.id WHERE o.customer_id = $1",
      lambda r, v: [_customer(r, v)]),
    # order_items
    T("items_by_order", "SELECT product_id, quantity FROM order_items WHERE order_id = $1", lambda r, v: [_order_id(r, v)]),
    T("items_by_product", "SELECT order_id, quantity FROM order_items WHERE product_id = $1",
      lambda r, v: [r.randint(1, v.products)]),
    T("items_join_orders_by_customer",
      "SELECT i.product_id, i.quantity FROM order_items i JOIN orders o ON o.id = i.order_id WHERE o.customer_id = $1",
      lambda r, v: [_customer(r, v)]),
    T("items_by_product_quantity", "SELECT order_id FROM order_items WHERE quantity >= $1 AND product_id = $2",
      lambda r, v: [r.randint(2, 5), r.randint(1, v.products)]),
    # events
    T("events_recent_by_customer",
      "SELECT id, event_type, created_at FROM events WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 50",
      lambda r, v: [_customer(r, v)]),
    T("events_by_type_window",
      "SELECT id, customer_id FROM events WHERE event_type = $1 AND created_at >= $2 AND created_at < $3",
      lambda r, v: [r.choice(EVENT_TYPES), *_window(r, (1, 3))]),
    T("events_by_customer_type", "SELECT id, created_at FROM events WHERE customer_id = $1 AND event_type = $2",
      lambda r, v: [_customer(r, v), r.choice(EVENT_TYPES)]),
    T("events_count_by_type",
      "SELECT event_type, count(*) FROM events WHERE created_at >= $1 AND created_at < $2 GROUP BY event_type",
      lambda r, v: _window(r, (1, 7))),
    T("events_sorted_payload",
      "SELECT id, customer_id, payload, created_at FROM events WHERE event_type = $1 ORDER BY payload",
      lambda r, v: [r.choice(RARE_EVENT_TYPES)]),
    # customers
    T("customers_by_country_segment", "SELECT id, email FROM customers WHERE country = $1 AND segment = $2",
      lambda r, v: [r.choice(COUNTRIES), r.choice(SEGMENTS)]),
    T("customers_by_lower_email", "SELECT id, email FROM customers WHERE lower(email) = $1",
      lambda r, v: [f"user{r.randint(1, v.customers)}@example.com"]),
    T("customers_created_window", "SELECT id, email FROM customers WHERE created_at >= $1 AND created_at < $2 ORDER BY created_at",
      lambda r, v: _window(r, (3, 7))),
    T("customers_newest_in_segment", "SELECT id, email FROM customers WHERE segment = $1 ORDER BY created_at DESC LIMIT 20",
      lambda r, v: [r.choice(SEGMENTS)]),
    # products
    T("products_by_category_price", "SELECT id, name FROM products WHERE category = $1 AND price < $2",
      lambda r, v: [r.choice(CATEGORIES), r.choice((10, 25, 60))]),
    T("products_by_name", "SELECT id, price FROM products WHERE name = $1", lambda r, v: [f"Product {r.randint(1, v.products)}"]),
    T("products_priciest_in_category", "SELECT id, name, price FROM products WHERE category = $1 ORDER BY price DESC LIMIT 10",
      lambda r, v: [r.choice(CATEGORIES)]),
    # subqueries and joins
    T("correlated_last_order",
      "SELECT c.id, (SELECT max(o.created_at) FROM orders o WHERE o.customer_id = c.id) AS last_order "
      "FROM customers c WHERE c.segment = $1 AND c.id <= $2",
      lambda r, v: [r.choice(SEGMENTS), r.randint(30, 120)]),
    T("customers_with_big_orders",
      "SELECT c.id, c.email FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id AND o.total_amount > $1) AND c.id <= $2",
      lambda r, v: [r.choice((200, 400)), r.randint(30, 120)]),
    T("orders_join_customers_country",
      "SELECT o.id, o.total_amount FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.country = $1 AND o.status = $2",
      lambda r, v: [r.choice(COUNTRIES), r.choice(["refunded", "cancelled"])]),
    T("orders_of_segment_customers",
      "SELECT id FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE segment = $1 AND id <= $2)",
      lambda r, v: [r.choice(SEGMENTS), r.randint(20, 80)]),
]


def _hash(params: list) -> str:
    return hashlib.sha1(json.dumps(params, default=str).encode()).hexdigest()[:8]


def sample_instances(variant: str, n_per_template: int, seed: int = 0) -> list[QueryInstance]:
    """``n_per_template`` distinct parameterisations of every template for one data variant. Deterministic."""
    info = variant_info(variant)
    instances: list[QueryInstance] = []
    for template in TEMPLATES:
        fingerprint = validate_query(template.sql).fingerprint
        rng = random.Random(f"{seed}:{variant}:{template.id}")
        seen: set[str] = set()
        for _ in range(n_per_template * 20):  # a template with few distinct values may yield fewer than asked
            if len(seen) == n_per_template:
                break
            params = list(template.sampler(rng, info))
            key = _hash(params)
            if key in seen:
                continue
            seen.add(key)
            instances.append(
                QueryInstance(
                    template_id=template.id, variant=variant, sql=template.sql, params=params, fingerprint=fingerprint,
                    query_id=f"{fingerprint}:{variant}:{key}",
                )
            )
    return instances
