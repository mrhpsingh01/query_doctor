"""A deterministic demo workload, so the workload dashboard has realistic traffic to import.

Baseline traffic is shaped like production: one *moderately slow* query called very often (it tops the total-time
ranking), several heavy queries called rarely (they top the per-call ranking), and cheap primary-key lookups. ``regress``
re-runs one query fingerprint against the large event types, a shift in data volume the dashboard should flag.

    python -m querydoctor.lab.workload --dsn postgresql://qd_readonly:...@127.0.0.1:5433/querydoctor_sample \\
        --admin-dsn postgresql://querydoctor:...@127.0.0.1:5433/querydoctor_sample --reset --mode baseline
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from typing import Any

import psycopg

from querydoctor.db.protocol import execute_single
from querydoctor.lab.catalog import build_catalog
from querydoctor.lab.generator import COUNTS, top_customers

DATES = ("2021-01-01", "2022-01-01", "2022-06-01", "2023-01-01", "2023-06-01")
STATUSES = ("pending", "completed", "shipped", "cancelled", "refunded", "processing")
RARE_EVENTS = ("checkout", "login", "search")  # baseline traffic for the large-sort query: small result sets
BIG_EVENTS = ("page_view", "add_to_cart")  # regression traffic: the same SQL over far more rows
COUNTRY_CURRENCY = (("JP", "JPY"), ("DE", "EUR"), ("US", "USD"), ("GB", "GBP"), ("BR", "BRL"))

# query id -> baseline calls at scale_calls = 1.0
BASELINE_CALLS = {
    "recent_orders_by_customer": 1500,  # the hot one: ~6 ms x 1500 dominates total time
    "orders_by_status_and_date": 60,
    "orders_payments_join": 100,
    "unnecessary_large_sort": 20,
    "function_on_indexed_column": 120,
    "select_star_wide_join": 15,
    "correlated_subquery": 6,
    "correlated_filter_misestimate": 40,
    "pk_order_lookup": 6000,
    "pk_customer_lookup": 6000,
}
CHEAP_SQL = {
    "pk_order_lookup": "SELECT id, status, total_amount FROM orders WHERE id = $1",
    "pk_customer_lookup": "SELECT id, email, country FROM customers WHERE id = $1",
}


@dataclass(frozen=True)
class PlannedCall:
    query_id: str
    sql: str
    params: tuple[Any, ...]


def _customers(scale: str, rng: random.Random):
    pool = top_customers(scale, max(COUNTS[scale]["customers"] // 5, 10))
    return lambda: pool[min(int(rng.paretovariate(1.1)) - 1, len(pool) - 1)] if rng.random() < 0.6 else rng.choice(pool)


def _params(query_id: str, scale: str, rng: random.Random, pick_customer) -> tuple[Any, ...]:
    n = COUNTS[scale]
    if query_id == "recent_orders_by_customer":
        return (pick_customer(), rng.choice(DATES))
    if query_id == "orders_by_status_and_date":
        month = rng.randint(1, 11)
        return (rng.choice(STATUSES), f"2023-{month:02d}-01", f"2023-{month + 1:02d}-01")
    if query_id == "orders_payments_join":
        return (pick_customer(),)
    if query_id == "unnecessary_large_sort":
        return (rng.choice(RARE_EVENTS),)
    if query_id == "function_on_indexed_column":
        return (f"user{rng.randint(1, n['customers'])}@example.com",)
    if query_id == "select_star_wide_join":
        return (rng.choice(("refunded", "cancelled")), rng.choice(("2023-06-01", "2023-09-01")))
    if query_id == "correlated_subquery":
        return ("regular", rng.randint(100, 150))
    if query_id == "correlated_filter_misestimate":
        return rng.choice(COUNTRY_CURRENCY)
    if query_id == "pk_order_lookup":
        return (rng.randint(1, n["orders"]),)
    if query_id == "pk_customer_lookup":
        return (rng.randint(1, n["customers"]),)
    raise KeyError(query_id)


def workload_plan(scale: str, mode: str, *, seed: int = 7, scale_calls: float = 1.0) -> list[PlannedCall]:
    """The ordered list of calls for a workload. Pure and deterministic: same arguments, same list."""
    rng = random.Random(seed)
    pick_customer = _customers(scale, rng)
    catalog = {q.id: q.sql for q in build_catalog(scale)}
    sql_for = {**catalog, **CHEAP_SQL}
    calls: list[PlannedCall] = []
    if mode == "baseline":
        for query_id, base in BASELINE_CALLS.items():
            for _ in range(max(1, round(base * scale_calls))):
                calls.append(PlannedCall(query_id, sql_for[query_id], _params(query_id, scale, rng, pick_customer)))
        rng.shuffle(calls)  # mixed traffic, not one query after another
    elif mode == "regress":
        query_id = "unnecessary_large_sort"
        for _ in range(max(25, round(25 * scale_calls))):  # >= the regression detector's 20-call minimum
            calls.append(PlannedCall(query_id, sql_for[query_id], (rng.choice(BIG_EVENTS),)))
    else:
        raise ValueError(f"unknown workload mode {mode!r}")
    return calls


def reset_statistics(admin_dsn: str) -> None:
    """Clear pg_stat_statements (needs a privileged role): used before a baseline run so counts are clean."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_stat_statements_reset()")


def run_workload(dsn: str, scale: str, mode: str, *, seed: int = 7, scale_calls: float = 1.0) -> dict[str, int]:
    """Execute the planned calls with the read-only role. Returns calls made per query id."""
    done: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=False) as conn:
        for call in workload_plan(scale, mode, seed=seed, scale_calls=scale_calls):
            execute_single(conn, call.sql, call.params)
            conn.rollback()
            done[call.query_id] = done.get(call.query_id, 0) + 1
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate demo traffic for the workload dashboard")
    parser.add_argument("--dsn", required=True, help="read-only role used to run the queries")
    parser.add_argument("--admin-dsn", help="privileged role, only needed with --reset")
    parser.add_argument("--reset", action="store_true", help="reset pg_stat_statements first")
    parser.add_argument("--mode", choices=["baseline", "regress"], default="baseline")
    parser.add_argument("--scale", choices=sorted(COUNTS), default="small")
    parser.add_argument("--scale-calls", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.reset:
        if not args.admin_dsn:
            parser.error("--reset needs --admin-dsn")
        reset_statistics(args.admin_dsn)
    done = run_workload(args.dsn, args.scale, args.mode, scale_calls=args.scale_calls)
    print(f"workload {args.mode}: {sum(done.values()):,} calls across {len(done)} queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
