"""Create the sample schema and bulk-load deterministic data.

    python -m querydoctor.lab.seed --dsn postgresql://querydoctor:querydoctor@127.0.0.1:5433/querydoctor_sample --scale small

With ``--with-workload --workload-dsn <read-only dsn>`` a freshly seeded database also gets one baseline run of demo
traffic (after a pg_stat_statements reset), so the workload dashboard has something to import.
"""

from __future__ import annotations

import argparse
import sys
import time

import psycopg

from querydoctor.lab.generator import COUNTS, generate
from querydoctor.lab.schema_sql import COLUMNS, DROP_ALL, POST_LOAD_DDL, TABLE_DDL


def _grant_stats_visibility(conn: psycopg.Connection, log) -> None:
    """Let the read-only analysis role read pg_stat_statements for every role (the workload importer needs it).
    Only a superuser can grant; on the bench instance (no such role) or without the privilege this is skipped."""
    try:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'qd_readonly'").fetchone():
            conn.execute("GRANT pg_read_all_stats TO qd_readonly")
    except psycopg.Error as exc:
        log(f"  could not grant pg_read_all_stats ({exc.diag.message_primary}); workload import will be limited")


def _already_seeded(conn: psycopg.Connection) -> bool:
    for table in TABLE_DDL:
        exists = conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0]
        if exists is None:
            return False
    return conn.execute("SELECT EXISTS (SELECT 1 FROM orders)").fetchone()[0]


def seed(
    dsn: str, scale: str = "small", seed: int = 42, *, if_empty: bool = False, log=lambda msg: None
) -> dict[str, int]:
    """(Re)create the schema and load it. Returns row counts per table ({} if skipped by ``if_empty``).

    The role must own/create tables. ``if_empty`` leaves a complete, populated database untouched.
    """
    counts: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        if if_empty and _already_seeded(conn):
            log("database already seeded; skipping")
            return counts
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
        for ddl in POST_LOAD_DDL:
            conn.execute(ddl)
        _grant_stats_visibility(conn, log)
        conn.execute("ANALYZE")
    return counts


def warm_workload(admin_dsn: str, readonly_dsn: str, scale: str, *, scale_calls: float = 1.0) -> dict[str, int]:
    """Reset pg_stat_statements (clearing the seeding statements) and run the baseline demo traffic once."""
    from querydoctor.lab.workload import reset_statistics, run_workload

    reset_statistics(admin_dsn)
    return run_workload(readonly_dsn, scale, "baseline", scale_calls=scale_calls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the QueryDoctor sample database")
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--scale", choices=sorted(COUNTS), default="small")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--if-empty", action="store_true", help="skip when the database is already populated")
    parser.add_argument("--with-workload", action="store_true", help="run the baseline demo traffic after seeding")
    parser.add_argument("--workload-dsn", help="read-only dsn the demo traffic runs as (required with --with-workload)")
    args = parser.parse_args(argv)
    if args.with_workload and not args.workload_dsn:
        parser.error("--with-workload needs --workload-dsn")
    print(f"Seeding scale={args.scale} seed={args.seed}")
    counts = seed(args.dsn, args.scale, args.seed, if_empty=args.if_empty, log=print)
    print("done:", counts)
    if args.with_workload and counts:  # skipped by --if-empty: the database already had its traffic history
        done = warm_workload(args.dsn, args.workload_dsn, args.scale)
        print(f"baseline workload: {sum(done.values()):,} calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
