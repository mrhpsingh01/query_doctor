"""DDL for the sample shop. Only primary-key indexes exist: foreign-key columns are deliberately left unindexed
(and no FK constraints are declared) so the catalog's slow-query patterns are real."""

from __future__ import annotations

TABLE_DDL: dict[str, str] = {
    "customers": """CREATE TABLE customers (
        id integer PRIMARY KEY, email text NOT NULL, country text NOT NULL,
        created_at timestamp NOT NULL, segment text NOT NULL)""",
    "products": """CREATE TABLE products (
        id integer PRIMARY KEY, name text NOT NULL, category text NOT NULL, price numeric(10,2) NOT NULL)""",
    "orders": """CREATE TABLE orders (
        id integer PRIMARY KEY, customer_id integer NOT NULL, status text NOT NULL,
        total_amount numeric(12,2) NOT NULL, currency text NOT NULL,
        created_at timestamp NOT NULL, shipped_at timestamp)""",
    "order_items": """CREATE TABLE order_items (
        id integer PRIMARY KEY, order_id integer NOT NULL, product_id integer NOT NULL,
        quantity integer NOT NULL, unit_price numeric(10,2) NOT NULL)""",
    "payments": """CREATE TABLE payments (
        id integer PRIMARY KEY, order_id integer NOT NULL, amount numeric(12,2) NOT NULL,
        method text NOT NULL, paid_at timestamp NOT NULL)""",
    "events": """CREATE TABLE events (
        id integer PRIMARY KEY, customer_id integer NOT NULL, event_type text NOT NULL,
        payload text NOT NULL, created_at timestamp NOT NULL)""",
}

# Column order of the rows yielded by lab.generator.generate()
COLUMNS: dict[str, list[str]] = {
    "customers": ["id", "email", "country", "created_at", "segment"],
    "products": ["id", "name", "category", "price"],
    "orders": ["id", "customer_id", "status", "total_amount", "currency", "created_at", "shipped_at"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price"],
    "payments": ["id", "order_id", "amount", "method", "paid_at"],
    "events": ["id", "customer_id", "event_type", "payload", "created_at"],
}

DROP_ALL = "DROP TABLE IF EXISTS " + ", ".join(reversed(list(TABLE_DDL))) + " CASCADE"
