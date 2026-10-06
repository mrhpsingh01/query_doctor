from __future__ import annotations

from collections.abc import Sequence

import psycopg

from querydoctor.schema import ColumnStats, IndexInfo, SchemaSnapshot, TableInfo

_TABLES = """
SELECT c.oid, c.relname, GREATEST(c.reltuples, 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname = ANY(%s)
"""
_COLUMNS = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
"""
_INDEXES = """
SELECT t.relname, ic.relname, i.indisunique, i.indnkeyatts, pg_get_expr(i.indpred, i.indrelid),
       pg_relation_size(i.indexrelid),
       ARRAY(SELECT pg_get_indexdef(i.indexrelid, k, true) FROM generate_series(1, i.indnatts) k ORDER BY k)
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s) AND i.indisvalid
ORDER BY t.relname, ic.relname
"""
_EXTRAS = """
SELECT pg_total_relation_size(c.oid) - COALESCE(pg_indexes_size(c.oid), 0),
       COALESCE(s.n_tup_ins + s.n_tup_upd + s.n_tup_del, 0),
       COALESCE(s.seq_tup_read + s.idx_tup_fetch, 0)
FROM pg_class c LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid WHERE c.oid = %s
"""
_COLUMN_STATS = """
SELECT attname, n_distinct, null_frac, correlation FROM pg_stats WHERE schemaname = 'public' AND tablename = %s
"""


def distinct_fraction(n_distinct: float, rows: int) -> float:
    """pg_stats stores n_distinct as a count (>= 0) or as minus a fraction of the row count (< 0); return the fraction."""
    if n_distinct < 0:
        return min(-n_distinct, 1.0)
    if rows <= 0:
        return 1.0
    return min(n_distinct / rows, 1.0)


def _write_ratio(writes: int, reads: int) -> float:
    total = writes + reads
    return writes / total if total else 0.0


def collect_schema(conn: psycopg.Connection, tables: Sequence[str], *, extras: bool = False) -> SchemaSnapshot:
    """Row estimates, column types and indexes for the named public tables (unknown names are omitted).

    ``extras=True`` also reads relation size, write ratio and per-column statistics (inputs of the recommendation ranker)."""
    names = sorted({t.lower() for t in tables})
    snapshot = SchemaSnapshot()
    try:
        with conn.cursor() as cur:
            for oid, name, rows in cur.execute(_TABLES, (names,)).fetchall():
                columns = dict(cur.execute(_COLUMNS, (oid,)).fetchall())
                snapshot.tables[name] = TableInfo(name=name, row_estimate=int(rows), columns=columns)
                snapshot.indexes[name] = []
                if extras:
                    size, writes, reads = cur.execute(_EXTRAS, (oid,)).fetchone()
                    info = snapshot.tables[name]
                    info.size_bytes, info.write_ratio = int(size), _write_ratio(int(writes), int(reads))
                    for column, n_distinct, null_frac, correlation in cur.execute(_COLUMN_STATS, (name,)).fetchall():
                        info.column_stats[column] = ColumnStats(
                            n_distinct_fraction=distinct_fraction(float(n_distinct), int(rows)), null_frac=float(null_frac),
                            correlation=float(correlation) if correlation is not None else 0.0,
                        )
            for table, index, unique, nkey, predicate, size, cols in cur.execute(_INDEXES, (names,)).fetchall():
                snapshot.indexes[table].append(
                    IndexInfo(
                        name=index, table=table, columns=list(cols[:nkey]), include=list(cols[nkey:]),
                        unique=unique, predicate=predicate, size_bytes=int(size),
                    )
                )
    finally:
        conn.rollback()
    return snapshot
