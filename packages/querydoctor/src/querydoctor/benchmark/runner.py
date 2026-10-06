"""The benchmark protocol (spec section 6.4), run inside a throwaway clone of the sample database.

    clone -> warm up + measure the original query -> create the REAL index -> ANALYZE -> warm up + measure again
          -> compare medians, variance and result checksums -> drop the clone (always)

Timings are the ``Execution Time`` reported by EXPLAIN (ANALYZE, BUFFERS); instrumentation overhead is identical on both
sides, so the comparison is fair even though absolute numbers read slightly high.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any

import psycopg
from psycopg import sql as psql

from querydoctor.benchmark.checksum import has_order_by, result_checksum
from querydoctor.benchmark.models import BenchmarkConfig, BenchmarkResult, RunSample
from querydoctor.benchmark.reliability import compare_outputs, reliability_warnings
from querydoctor.benchmark.sandbox import Sandbox
from querydoctor.benchmark.stats import improvement_pct, summarize
from querydoctor.db import DbError, explain, guarded_connection
from querydoctor.plan_parser import iter_nodes, parse_explain

log = logging.getLogger(__name__)

MAX_CONCURRENT_BENCHMARKS = 2
LOCK_BASE = 7100  # advisory-lock keys 7100..7100+MAX-1 are the benchmark slots on the bench instance
STATEMENT_TIMEOUT_MS = 60_000
_DDL_RE = re.compile(r'^CREATE INDEX ON ("(?:[^"]|"")+"|[a-z_][a-z0-9_$]*) \(', re.IGNORECASE)
_SETTINGS = ("shared_buffers", "work_mem", "effective_cache_size", "random_page_cost", "max_parallel_workers_per_gather")


class BenchmarkFailed(Exception):
    """The benchmark could not be completed (the message is safe to show a user)."""


class BenchmarkCancelled(BenchmarkFailed):
    pass


class BenchmarkTimeout(BenchmarkFailed):
    pass


class BenchmarkBusy(BenchmarkFailed):
    """All benchmark slots are in use."""


def _validate_ddl(ddl: str) -> str:
    """Only plain, single-statement CREATE INDEX is ever executed; returns the table name it targets."""
    match = _DDL_RE.match(ddl.strip())
    if not match or ";" in ddl or "--" in ddl or "/*" in ddl:
        raise BenchmarkFailed("The index definition is not a plain CREATE INDEX statement, so it was not run.")
    table = match.group(1)
    return table[1:-1].replace('""', '"') if table.startswith('"') else table


def _acquire_slot(admin_dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    for slot in range(MAX_CONCURRENT_BENCHMARKS):
        if conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_BASE + slot,)).fetchone()[0]:
            return conn
    conn.close()
    raise BenchmarkBusy("Too many benchmarks are running right now. Try again in a moment.")


def _sample(raw: list[dict]) -> RunSample:
    plan = raw[0]["Plan"]
    temp = (plan.get("Temp Read Blocks") or 0) + (plan.get("Temp Written Blocks") or 0)
    return RunSample(
        execution_ms=float(raw[0]["Execution Time"]), planning_ms=raw[0].get("Planning Time"),
        rows=int(plan.get("Actual Rows", 0)), shared_hit_blocks=plan.get("Shared Hit Blocks"),
        shared_read_blocks=plan.get("Shared Read Blocks"), temp_blocks=temp,
    )


def run_benchmark(
    bench_dsn: str,
    sql: str,
    params: Sequence[Any],
    candidate_ddl: str,
    config: BenchmarkConfig,
    on_progress: Callable[[str], None] = lambda stage: None,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    template: str = "querydoctor_sample",
) -> BenchmarkResult:
    table = _validate_ddl(candidate_ddl)
    deadline = time.monotonic() + config.timeout_s

    def check() -> None:
        if cancelled():
            raise BenchmarkCancelled("The benchmark was cancelled.")
        if time.monotonic() >= deadline:
            raise BenchmarkTimeout(f"The benchmark did not finish within {config.timeout_s} seconds.")

    slot = _acquire_slot(bench_dsn)
    sandbox = Sandbox(bench_dsn, template)
    try:
        on_progress("creating_sandbox")
        sandbox.create()
        with guarded_connection(sandbox.dsn, role_kind="bench_admin", statement_timeout_ms=STATEMENT_TIMEOUT_MS) as conn:
            return _measure_both(conn, sql, params, candidate_ddl, table, config, on_progress, check, template)
    except BenchmarkFailed:
        raise
    except (psycopg.Error, DbError) as exc:
        message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
        raise BenchmarkFailed(f"The benchmark failed: {message}") from exc
    finally:
        on_progress("teardown")
        with suppress(Exception):
            sandbox.drop()
        slot.close()


def _runs(conn, sql, params, n, warmups, check, on_progress, warm_stage, measure_stage):
    on_progress(warm_stage)
    raw = None
    for _ in range(warmups):
        check()
        raw = explain(conn, sql, params, execute=True)
    on_progress(measure_stage)
    samples: list[RunSample] = []
    for _ in range(n):
        check()
        raw = explain(conn, sql, params, execute=True)
        samples.append(_sample(raw))
    return samples, raw


def _checksums(conn, sql, params, ordered: bool) -> tuple[str, str | None]:
    """(set checksum, row-order checksum or None when the query has no ORDER BY)."""
    return (
        result_checksum(conn, sql, params, ordered=False),
        result_checksum(conn, sql, params, ordered=True) if ordered else None,
    )


def _measure_both(conn, sql, params, ddl, table, config, on_progress, check, template) -> BenchmarkResult:
    version = conn.execute("SELECT version()").fetchone()[0].split(" on ")[0]
    settings = dict(conn.execute("SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", (list(_SETTINGS),)).fetchall())
    tables = conn.execute(
        "SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
    ).fetchall()
    snapshot = hashlib.sha1(f"{template}:{tables}".encode()).hexdigest()[:12]
    ordered = has_order_by(sql)
    conn.rollback()
    conn.read_only = True  # the measured query can never write; only the DDL step below switches this off

    before_samples, before_raw = _runs(conn, sql, params, config.runs, config.warmups, check, on_progress, "warmup_before", "measuring_before")
    checks_before = _checksums(conn, sql, params, ordered)

    check()
    on_progress("creating_index")
    indexes_before = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    conn.rollback()
    conn.read_only = False
    conn.autocommit = True
    started = time.perf_counter()
    try:
        conn.execute(ddl)
    except psycopg.Error as exc:
        raise BenchmarkFailed(f"The index could not be created: {exc.diag.message_primary or exc}") from exc
    build_ms = (time.perf_counter() - started) * 1000
    created = [r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'") if r[0] not in indexes_before]
    size = conn.execute("SELECT pg_relation_size(%s::regclass)", (created[0],)).fetchone()[0] if created else 0

    check()
    on_progress("analyzing")
    conn.execute(psql.SQL("ANALYZE {}").format(psql.Identifier(table)))
    conn.autocommit = False
    conn.read_only = True

    after_samples, after_raw = _runs(conn, sql, params, config.runs, config.warmups, check, on_progress, "warmup_after", "measuring_after")
    checks_after = _checksums(conn, sql, params, ordered)

    on_progress("comparing")
    before = summarize([s.execution_ms for s in before_samples])
    after = summarize([s.execution_ms for s in after_samples])
    first_ratio = max(
        before_samples[0].execution_ms / before.median_ms if before.median_ms else 1.0,
        after_samples[0].execution_ms / after.median_ms if after.median_ms else 1.0,
    )
    match, output_notes = compare_outputs(checks_before[0], checks_after[0], checks_before[1], checks_after[1])
    after_plan = parse_explain(after_raw)
    index_used = bool(created) and any(n.index_name == created[0] for n in iter_nodes(after_plan.root))
    warnings = reliability_warnings(before, after, config=config, checksum_match=match, first_run_ratio=first_ratio)
    warnings += output_notes
    if not index_used:
        warnings.append("The new index was not used by the query's final plan, so any speed difference is not attributable to it.")
    return BenchmarkResult(
        before=before, after=after, before_samples=before_samples, after_samples=after_samples,
        improvement_pct=improvement_pct(before.median_ms, after.median_ms), index_size_bytes=int(size),
        index_build_ms=build_ms, plan_fingerprint_before=parse_explain(before_raw).fingerprint,
        plan_fingerprint_after=after_plan.fingerprint, checksum_match=match, index_used=index_used, warnings=warnings,
        pg_version=version, settings=settings, dataset_snapshot=snapshot, ddl=ddl,
    )
