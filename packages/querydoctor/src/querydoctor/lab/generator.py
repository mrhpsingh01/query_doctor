"""Deterministic, skewed e-commerce sample data.

Realistic skew matters because uniform fake data produces unrealistic plans: a few customers place many orders,
products have a long-tail popularity, order volume is seasonal, statuses are uneven and currency follows the
customer's country (so filters on both are correlated and mislead the planner's independence assumption).

``generate`` yields ``(table_name, batches)`` where ``batches`` is an iterator of ``list[tuple]`` rows in the column
order declared in ``lab.schema_sql.COLUMNS``. Timestamps are ISO-8601 strings (PostgreSQL parses them on COPY).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import numpy as np

COUNTS: dict[str, dict[str, int]] = {
    "small": {"customers": 10_000, "orders": 100_000, "order_items": 300_000, "events": 500_000},
    "full": {"customers": 100_000, "orders": 1_000_000, "order_items": 3_000_000, "events": 5_000_000},
}
PRODUCT_DIVISOR = 5  # products = customers / 5
TABLE_ORDER = ["customers", "products", "orders", "order_items", "payments", "events"]
BATCH_SIZE = 50_000
CUSTOMER_ZIPF = 0.8
PRODUCT_ZIPF = 1.0

COUNTRIES = [  # (code, weight, currency)
    ("US", 0.30, "USD"), ("GB", 0.10, "GBP"), ("DE", 0.10, "EUR"), ("FR", 0.07, "EUR"), ("ES", 0.04, "EUR"),
    ("IT", 0.04, "EUR"), ("IN", 0.10, "INR"), ("BR", 0.06, "BRL"), ("CA", 0.06, "CAD"), ("AU", 0.04, "AUD"),
    ("JP", 0.05, "JPY"),
]
SEGMENTS = (["regular", "vip", "new"], [0.80, 0.10, 0.10])
STATUSES = (["completed", "shipped", "pending", "processing", "cancelled", "refunded"], [0.62, 0.12, 0.10, 0.06, 0.06, 0.04])
PAID_STATUSES = {"completed", "shipped", "refunded", "processing"}
SHIPPED_STATUSES = {"completed", "shipped"}
PAYMENT_METHODS = (["card", "paypal", "bank_transfer"], [0.70, 0.20, 0.10])
EVENT_TYPES = (["page_view", "add_to_cart", "search", "checkout", "login"], [0.60, 0.15, 0.12, 0.05, 0.08])
CATEGORIES = ["books", "electronics", "home", "toys", "garden", "sports", "beauty", "grocery", "pets", "auto", "music", "tools"]
YEARS = ([2021, 2022, 2023], [0.25, 0.35, 0.40])
MONTH_WEIGHTS = [0.070, 0.065, 0.075, 0.075, 0.080, 0.080, 0.080, 0.080, 0.085, 0.090, 0.110, 0.135]  # peaks in December


def _norm(weights: list[float]) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    return w / w.sum()


def _rng(seed: int, table: str) -> np.random.Generator:
    return np.random.default_rng([seed, TABLE_ORDER.index(table)])


def _zipf(n: int, s: float) -> np.ndarray:
    w = 1.0 / np.arange(1, n + 1, dtype=float) ** s
    return w / w.sum()


def _timestamps(rng: np.random.Generator, n: int, *, sort: bool = True) -> np.ndarray:
    years = rng.choice(YEARS[0], size=n, p=_norm(YEARS[1]))
    months = rng.choice(12, size=n, p=_norm(MONTH_WEIGHTS))
    base = ((years - 1970) * 12 + months).astype("datetime64[M]").astype("datetime64[s]")
    offset = rng.integers(0, 28, size=n) * 86_400 + rng.integers(0, 86_400, size=n)
    ts = base + offset.astype("timedelta64[s]")
    return np.sort(ts) if sort else ts


def _scale(scale: str) -> dict[str, int]:
    if scale not in COUNTS:
        raise ValueError(f"unknown scale {scale!r}; expected one of {sorted(COUNTS)}")
    return COUNTS[scale]


@lru_cache(maxsize=4)
def _customers(scale: str, seed: int) -> dict[str, Any]:
    n = _scale(scale)["customers"]
    rng = _rng(seed, "customers")
    codes = [c[0] for c in COUNTRIES]
    country_idx = rng.choice(len(codes), size=n, p=_norm([c[1] for c in COUNTRIES]))
    return {
        "n": n,
        "country_idx": country_idx,
        "segment": rng.choice(SEGMENTS[0], size=n, p=_norm(SEGMENTS[1])),
        "created_at": _timestamps(rng, n),
        "weights": _zipf(n, CUSTOMER_ZIPF),
        "perm": rng.permutation(n) + 1,  # activity rank -> customer id (heavy hitters are not ids 1..k)
    }


@lru_cache(maxsize=4)
def _products(scale: str, seed: int) -> dict[str, Any]:
    n = _scale(scale)["customers"] // PRODUCT_DIVISOR
    rng = _rng(seed, "products")
    return {
        "n": n,
        "category": rng.choice(CATEGORIES, size=n),
        "price": np.round(rng.lognormal(3.2, 0.9, size=n), 2),
        "weights": _zipf(n, PRODUCT_ZIPF),
        "perm": rng.permutation(n) + 1,
    }


@lru_cache(maxsize=4)
def _orders(scale: str, seed: int) -> dict[str, Any]:
    n = _scale(scale)["orders"]
    cust = _customers(scale, seed)
    rng = _rng(seed, "orders")
    customer_id = cust["perm"][rng.choice(cust["n"], size=n, p=cust["weights"])]
    status = rng.choice(STATUSES[0], size=n, p=_norm(STATUSES[1]))
    created_at = _timestamps(rng, n)
    delay = (rng.integers(1, 8, size=n) * 86_400).astype("timedelta64[s]")
    shipped_at = np.where(np.isin(status, list(SHIPPED_STATUSES)), created_at + delay, np.datetime64("NaT", "s"))
    currencies = np.array([c[2] for c in COUNTRIES])
    return {
        "n": n,
        "customer_id": customer_id,
        "status": status,
        "total_amount": np.round(rng.lognormal(3.6, 0.8, size=n), 2),
        "currency": currencies[cust["country_idx"][customer_id - 1]],
        "created_at": created_at,
        "shipped_at": shipped_at,
    }


def top_customers(scale: str, k: int = 1, seed: int = 42) -> list[int]:
    """Ids of the k most active customers (the heavy hitters of the Zipf distribution)."""
    return [int(x) for x in _customers(scale, seed)["perm"][:k]]


def _fmt(col: np.ndarray) -> list:
    if col.dtype.kind == "M":
        return [None if s == "NaT" else s for s in np.datetime_as_string(col, unit="s").tolist()]
    return col.tolist()


def _rows(columns: list[np.ndarray], batch: int = BATCH_SIZE) -> Iterator[list[tuple]]:
    n = len(columns[0])
    for start in range(0, n, batch):
        yield list(zip(*(_fmt(col[start : start + batch]) for col in columns), strict=True))


def _ids(n: int) -> np.ndarray:
    return np.arange(1, n + 1)


def _table_customers(scale: str, seed: int) -> Iterator[list[tuple]]:
    c = _customers(scale, seed)
    ids = _ids(c["n"])
    codes = np.array([x[0] for x in COUNTRIES])
    emails = np.char.add(np.char.add("User", ids.astype(str)), "@Example.com")
    return _rows([ids, emails, codes[c["country_idx"]], c["created_at"], c["segment"]])


def _table_products(scale: str, seed: int) -> Iterator[list[tuple]]:
    p = _products(scale, seed)
    ids = _ids(p["n"])
    return _rows([ids, np.char.add("Product ", ids.astype(str)), p["category"], p["price"]])


def _table_orders(scale: str, seed: int) -> Iterator[list[tuple]]:
    o = _orders(scale, seed)
    return _rows(
        [_ids(o["n"]), o["customer_id"], o["status"], o["total_amount"], o["currency"], o["created_at"], o["shipped_at"]]
    )


def _table_order_items(scale: str, seed: int) -> Iterator[list[tuple]]:
    n = _scale(scale)["order_items"]
    n_orders = _scale(scale)["orders"]
    prod = _products(scale, seed)
    rng = _rng(seed, "order_items")
    order_id = np.sort(rng.integers(1, n_orders + 1, size=n))  # items are inserted roughly in order-id order
    product_id = prod["perm"][rng.choice(prod["n"], size=n, p=prod["weights"])]
    quantity = rng.choice([1, 2, 3, 4, 5], size=n, p=_norm([0.55, 0.25, 0.10, 0.06, 0.04]))
    unit_price = np.round(prod["price"][product_id - 1] * rng.uniform(0.9, 1.1, size=n), 2)
    return _rows([_ids(n), order_id, product_id, quantity, unit_price])


def _table_payments(scale: str, seed: int) -> Iterator[list[tuple]]:
    o = _orders(scale, seed)
    rng = _rng(seed, "payments")
    paid = np.isin(o["status"], list(PAID_STATUSES))
    order_id = np.flatnonzero(paid) + 1
    n = len(order_id)
    paid_at = o["created_at"][paid] + (rng.integers(1, 120, size=n) * 60).astype("timedelta64[s]")
    method = rng.choice(PAYMENT_METHODS[0], size=n, p=_norm(PAYMENT_METHODS[1]))
    return _rows([_ids(n), order_id, o["total_amount"][paid], method, paid_at])


def _table_events(scale: str, seed: int) -> Iterator[list[tuple]]:
    n = _scale(scale)["events"]
    cust = _customers(scale, seed)
    rng = _rng(seed, "events")
    customer_id = cust["perm"][rng.choice(cust["n"], size=n, p=cust["weights"])]
    event_type = rng.choice(EVENT_TYPES[0], size=n, p=_norm(EVENT_TYPES[1]))
    payload = np.char.add(np.char.add('{"v": ', rng.integers(0, 1000, size=n).astype(str)), "}")
    return _rows([_ids(n), customer_id, event_type, payload, _timestamps(rng, n)])


_BUILDERS = {
    "customers": _table_customers,
    "products": _table_products,
    "orders": _table_orders,
    "order_items": _table_order_items,
    "payments": _table_payments,
    "events": _table_events,
}


def generate(
    scale: str, seed: int = 42, tables: list[str] | None = None
) -> Iterator[tuple[str, Iterator[list[tuple]]]]:
    """Yield ``(table, batches)`` in FK-safe order for the requested scale."""
    _scale(scale)
    wanted = tables or TABLE_ORDER
    unknown = set(wanted) - set(TABLE_ORDER)
    if unknown:
        raise ValueError(f"unknown tables: {sorted(unknown)}")
    for name in TABLE_ORDER:
        if name in wanted:
            yield name, _BUILDERS[name](scale, seed)
