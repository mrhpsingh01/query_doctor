"""Load the sample shop into MongoDB, document-shaped, from the same deterministic generator as the SQL lab.

    python -m querydoctor.lab.mongo_seed --uri mongodb://querydoctor:querydoctor@127.0.0.1:27018/?authSource=admin --scale small

Collections: ``customers``, ``orders`` (with the order's items and payment embedded) and ``events``. Timestamps are
ISO-8601 strings, which sort and compare correctly as text. Only a few indexes exist, on purpose: the catalog's slow
queries need them missing, mis-ordered or redundant (see ``lab.mongo_catalog``). The profiler is switched to level 2 so
the workload importer can read ``system.profile``.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from typing import Any

from pymongo import ASCENDING, MongoClient

from querydoctor.lab.generator import COUNTS, generate
from querydoctor.lab.schema_sql import COLUMNS

DB = "qd_sample"
BATCH = 5_000
PROFILER_BYTES = 64 * 1024 * 1024
META = "_qd_meta"

# The deliberate indexes. Anything else (notably orders.customer_id) is missing on purpose.
INDEXES: dict[str, list[tuple[list[tuple[str, int]], str]]] = {
    "customers": [([("email", ASCENDING)], "email_1")],
    "orders": [
        ([("status", ASCENDING)], "status_1"),  # low cardinality: unselective
        ([("created_at", ASCENDING), ("customer_id", ASCENDING)], "created_at_1_customer_id_1"),  # range before equality
    ],
    "events": [
        ([("event_type", ASCENDING)], "event_type_1"),  # redundant: a prefix of the next index
        ([("event_type", ASCENDING), ("created_at", ASCENDING)], "event_type_1_created_at_1"),
    ],
}


def _docs(table: str, rows: list[tuple]) -> list[dict[str, Any]]:
    cols = COLUMNS[table]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def _insert(collection, docs: list[dict[str, Any]]) -> int:
    for start in range(0, len(docs), BATCH):
        collection.insert_many(docs[start : start + BATCH], ordered=False)
    return len(docs)


def _already_seeded(db, scale: str) -> bool:
    marker = db[META].find_one({"_id": "seed"})
    return bool(marker and marker.get("complete") and marker.get("scale") == scale)


def seed_mongo(
    uri: str, scale: str = "small", *, db: str = DB, seed: int = 42, if_empty: bool = False, profile: bool = True, log=lambda msg: None
) -> dict[str, int]:
    """(Re)create the collections and load them. Returns document counts ({} if skipped by ``if_empty``)."""
    if scale not in COUNTS:
        raise ValueError(f"unknown scale {scale!r}; expected one of {sorted(COUNTS)}")
    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        target = client[db]
        if if_empty and _already_seeded(target, scale):
            log("mongo database already seeded; skipping")
            return {}
        for name in ("customers", "orders", "events", META):
            target.drop_collection(name)

        started = time.perf_counter()
        items: dict[int, list[dict[str, Any]]] = defaultdict(list)
        payments: dict[int, dict[str, Any]] = {}
        order_rows: list[tuple] = []
        counts: dict[str, int] = {}
        for table, batches in generate(scale, seed):  # foreign-key order: orders arrive before their items and payments
            rows = [row for batch in batches for row in batch]
            if table in ("customers", "events"):
                counts[table] = _insert(target[table], [{"_id": d.pop("id"), **d} for d in _docs(table, rows)])
            elif table == "orders":
                order_rows = rows
            elif table == "order_items":
                for d in _docs(table, rows):
                    items[d["order_id"]].append({"product_id": d["product_id"], "quantity": d["quantity"], "unit_price": d["unit_price"]})
            elif table == "payments":
                for d in _docs(table, rows):
                    payments[d["order_id"]] = {"amount": d["amount"], "method": d["method"], "paid_at": d["paid_at"]}
        order_docs = []
        for d in _docs("orders", order_rows):  # an order carries its items and payment
            oid = d.pop("id")
            order_docs.append({"_id": oid, **d, "items": items.get(oid, []), "payment": payments.get(oid)})
        counts["orders"] = _insert(target["orders"], order_docs)
        log(f"  loaded {sum(counts.values()):,} documents in {time.perf_counter() - started:.1f}s")

        for collection, indexes in INDEXES.items():
            for keys, name in indexes:
                target[collection].create_index(keys, name=name)
        if profile:
            target.command("profile", 0)
            target.drop_collection("system.profile")
            target.create_collection("system.profile", capped=True, size=PROFILER_BYTES)
            target.command("profile", 2)
        target[META].insert_one({"_id": "seed", "scale": scale, "complete": True, "counts": counts})
        return counts
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the QueryDoctor sample MongoDB database")
    parser.add_argument("--uri", required=True, help="a privileged user (root): creating indexes and the profiler needs it")
    parser.add_argument("--scale", choices=sorted(COUNTS), default="small")
    parser.add_argument("--db", default=DB)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--if-empty", action="store_true")
    args = parser.parse_args(argv)
    print(f"Seeding MongoDB scale={args.scale}")
    counts = seed_mongo(args.uri, args.scale, db=args.db, seed=args.seed, if_empty=args.if_empty, log=print)
    print("done:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
