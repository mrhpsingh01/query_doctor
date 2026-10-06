"""Create the sample schema and bulk-load deterministic data.

    python -m querydoctor.lab.seed --dsn postgresql://querydoctor:querydoctor@localhost:5433/querydoctor_sample --scale small
"""

from __future__ import annotations

import argparse
import sys
import time

import psycopg

from querydoctor.lab.generator import generate
from querydoctor.lab.schema_sql import COLUMNS, DROP_ALL, TABLE_DDL


def seed(dsn: str, scale: str = "small", seed: int = 42, *, log=lambda msg: None) -> dict[str, int]:
    """(Re)create the schema and load it. Returns row counts per table. The role must own/create tables."""
    counts: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(DROP_ALL)
        for ddl in TABLE_DDL.values():
            conn.execute(ddl)
        for table, batches in generate(scale, seed):
            started, loaded = time.perf_counter(), 0
            cols = ", ".join(COLUMNS[table])
            with conn.cursor().copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
                for batch in batches:
                    for row in batch:
                        copy.write_row(row)
                    loaded += len(batch)
            counts[table] = loaded
            log(f"  {table}: {loaded:,} rows in {time.perf_counter() - started:.1f}s")
        conn.execute("ANALYZE")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the QueryDoctor sample database")
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--scale", choices=["small", "full"], default="small")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    print(f"Seeding scale={args.scale} seed={args.seed}")
    counts = seed(args.dsn, args.scale, args.seed, log=print)
    print("done:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
