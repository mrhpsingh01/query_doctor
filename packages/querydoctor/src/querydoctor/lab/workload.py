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
from querydoctor.lab.mongo_catalog import build_mongo_catalog

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


# ---- MongoDB traffic -------------------------------------------------------------------------------------------------

MONGO_BASELINE_CALLS = {
    "mongo_collscan_by_customer": 800,  # the hot one: ~13 ms x 800 dominates total time
    "mongo_status_and_date_range": 600,
    "mongo_blocking_sort": 20,
    "mongo_range_before_equality": 40,
    "mongo_lookup_without_index": 4,
    "mongo_match_after_group": 4,
    "mongo_unbounded_group": 3,
    "mongo_unselective_status_index": 100,
    "mongo_pk_customer_lookup": 6000,
}
MONGO_CHEAP_SPEC = '{"find": "customers", "filter": {"_id": {"$param": 1}}, "limit": 1}'


def _mongo_params(query_id: str, scale: str, rng: random.Random, pick_customer) -> list[Any]:
    n = COUNTS[scale]
    if query_id == "mongo_status_and_date_range":
        month, day = rng.randint(1, 11), rng.randint(1, 20)
        return ["refunded", f"2023-{month:02d}-{day:02d}", f"2023-{month:02d}-{day + 7:02d}"]
    if query_id == "mongo_collscan_by_customer":
        return [pick_customer()]
    if query_id == "mongo_blocking_sort":
        return [rng.choice(RARE_EVENTS)]
    if query_id == "mongo_range_before_equality":
        return [rng.choice(DATES[:3]), "2023-06-01", pick_customer()]
    if query_id == "mongo_lookup_without_index":
        return ["regular", rng.randint(3, 6)]  # a handful of customers: each costs a scan of orders
    if query_id == "mongo_match_after_group":
        return [pick_customer()]
    if query_id == "mongo_unbounded_group":
        return []
    if query_id == "mongo_unselective_status_index":
        return ["completed"]
    if query_id == "mongo_pk_customer_lookup":
        return [rng.randint(1, n["customers"])]
    raise KeyError(query_id)


def mongo_workload_plan(scale: str, mode: str, *, seed: int = 7, scale_calls: float = 1.0) -> list[PlannedCall]:
    """Like ``workload_plan`` for the MongoDB catalog: ``sql`` holds the JSON spec, ``params`` its placeholders."""
    rng = random.Random(seed)
    pick_customer = _customers(scale, rng)
    spec_for = {q.id: q.sql for q in build_mongo_catalog(scale)} | {"mongo_pk_customer_lookup": MONGO_CHEAP_SPEC}
    calls: list[PlannedCall] = []
    if mode == "baseline":
        for query_id, base in MONGO_BASELINE_CALLS.items():
            for _ in range(max(1, round(base * scale_calls))):
                calls.append(PlannedCall(query_id, spec_for[query_id], tuple(_mongo_params(query_id, scale, rng, pick_customer))))
        rng.shuffle(calls)
    elif mode == "regress":
        for _ in range(max(25, round(25 * scale_calls))):
            calls.append(PlannedCall("mongo_blocking_sort", spec_for["mongo_blocking_sort"], (rng.choice(BIG_EVENTS),)))
    else:
        raise ValueError(f"unknown workload mode {mode!r}")
    return calls


def reset_mongo_profile(admin_uri: str, db: str = "qd_sample") -> None:
    """Empty the profiler's capped collection (needs a privileged user) and keep level 2, so a baseline run starts clean."""
    from pymongo import MongoClient

    from querydoctor.lab.mongo_seed import PROFILER_BYTES

    with MongoClient(admin_uri, serverSelectionTimeoutMS=5000) as client:
        target = client[db]
        target.command("profile", 0)
        target.drop_collection("system.profile")
        target.create_collection("system.profile", capped=True, size=PROFILER_BYTES)
        target.command("profile", 2)


def run_mongo_workload(uri: str, scale: str, mode: str, *, seed: int = 7, scale_calls: float = 1.0, db: str = "qd_sample") -> dict[str, int]:
    """Execute the planned calls with the read-only user. Returns calls made per query id."""
    from pymongo import MongoClient

    from querydoctor.mongo import build_command, validate_mongo

    done: dict[str, int] = {}
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        target = client[db]
        for call in mongo_workload_plan(scale, mode, seed=seed, scale_calls=scale_calls):
            vq = validate_mongo(call.sql, list(call.params))
            target.command(build_command(vq, 30_000))
            done[call.query_id] = done.get(call.query_id, 0) + 1
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate demo traffic for the workload dashboard")
    parser.add_argument("--engine", choices=["postgres", "mongodb"], default="postgres")
    parser.add_argument("--dsn", required=True, help="read-only role (PostgreSQL DSN or MongoDB URI) used to run the queries")
    parser.add_argument("--admin-dsn", help="privileged role, only needed with --reset")
    parser.add_argument("--reset", action="store_true", help="reset pg_stat_statements (or empty the MongoDB profiler) first")
    parser.add_argument("--mode", choices=["baseline", "regress"], default="baseline")
    parser.add_argument("--scale", choices=sorted(COUNTS), default="small")
    parser.add_argument("--scale-calls", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.reset:
        if not args.admin_dsn:
            parser.error("--reset needs --admin-dsn")
        (reset_mongo_profile if args.engine == "mongodb" else reset_statistics)(args.admin_dsn)
    runner = run_mongo_workload if args.engine == "mongodb" else run_workload
    done = runner(args.dsn, args.scale, args.mode, scale_calls=args.scale_calls)
    print(f"workload {args.mode}: {sum(done.values()):,} calls across {len(done)} queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
