"""Read pg_stat_statements and merge its rows by query fingerprint."""

from __future__ import annotations

import math

import psycopg
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querydoctor.safety import ValidationError, validate_query
from querydoctor.workload.models import StatementRow, WorkloadQuery

Z95 = 1.645  # normal approximation: pg_stat_statements has no percentiles

_COLUMNS = (
    "query, calls, total_exec_time, mean_exec_time, stddev_exec_time, min_exec_time, max_exec_time, rows, "
    "shared_blks_hit, shared_blks_read, temp_blks_read + temp_blks_written"
)
_SQL = f"""
SELECT {_COLUMNS} FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) AND calls > 0
ORDER BY total_exec_time DESC LIMIT %s
"""


def read_statements(conn: psycopg.Connection, limit: int = 500) -> list[StatementRow]:
    """The heaviest statements of the current database. Needs the pg_read_all_stats privilege to see every role's text."""
    try:
        rows = conn.execute(_SQL, (limit,)).fetchall()
    finally:
        conn.rollback()
    return [
        StatementRow(
            query=q, calls=int(calls), total_ms=float(total), mean_ms=float(mean), stddev_ms=float(std or 0.0),
            min_ms=float(lo), max_ms=float(hi), rows=int(r), shared_hit_blocks=int(hit), shared_read_blocks=int(read),
            temp_blocks=int(temp or 0),
        )
        for q, calls, total, mean, std, lo, hi, r, hit, read, temp in rows
    ]


def _touches_system_catalog(sql: str) -> bool:
    """True when the query reads a system catalog (pg_*, pg_catalog.*, information_schema.*): QueryDoctor's own plumbing."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except (SqlglotError, ValueError):
        return True
    return any(
        (t.db or "").lower() in ("pg_catalog", "information_schema") or t.name.lower().startswith("pg_")
        for t in tree.find_all(exp.Table)
    )


def merge_statements(rows: list[StatementRow]) -> tuple[list[WorkloadQuery], int]:
    """Group statements by fingerprint. Returns ``(queries by total time desc, number of skipped statements)``.

    Skipped: statements that never ran, are not a single SELECT, cannot be parsed, touch no table, or read system
    catalogs (QueryDoctor's own plumbing). The validator's fingerprint of pgss's ``$n`` text equals the fingerprint of
    the same query with literals, which is what links workload rows to analyses.
    """
    groups: dict[str, list[tuple[StatementRow, str, list[str]]]] = {}
    skipped = 0
    for row in rows:
        if row.calls <= 0:
            skipped += 1
            continue
        try:
            vq = validate_query(row.query)
        except ValidationError:
            skipped += 1
            continue
        if not vq.tables or _touches_system_catalog(vq.sql):
            skipped += 1
            continue
        groups.setdefault(vq.fingerprint, []).append((row, vq.normalized_sql, vq.tables))

    merged: list[WorkloadQuery] = []
    for fingerprint, members in groups.items():
        calls = sum(r.calls for r, _n, _t in members)
        total = sum(r.total_ms for r, _n, _t in members)
        mean = total / calls
        # population variance of the union of the members' populations
        second_moment = sum(r.calls * (r.stddev_ms**2 + r.mean_ms**2) for r, _n, _t in members) / calls
        stddev = math.sqrt(max(second_moment - mean**2, 0.0))
        heaviest = max(members, key=lambda m: m[0].total_ms)
        merged.append(
            WorkloadQuery(
                fingerprint=fingerprint, normalized_sql=members[0][1], sample_sql=heaviest[0].query, calls=calls,
                total_ms=total, mean_ms=mean, stddev_ms=stddev, min_ms=min(r.min_ms for r, _n, _t in members),
                max_ms=max(r.max_ms for r, _n, _t in members), rows=sum(r.rows for r, _n, _t in members),
                shared_hit_blocks=sum(r.shared_hit_blocks for r, _n, _t in members),
                shared_read_blocks=sum(r.shared_read_blocks for r, _n, _t in members),
                temp_blocks=sum(r.temp_blocks for r, _n, _t in members), p95_est_ms=mean + Z95 * stddev,
                tables=members[0][2],
            )
        )
    merged.sort(key=lambda q: q.total_ms, reverse=True)
    return merged, skipped
