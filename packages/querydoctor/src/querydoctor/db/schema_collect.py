from __future__ import annotations

from collections.abc import Sequence

import psycopg

from querydoctor.schema import IndexInfo, SchemaSnapshot, TableInfo

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


def collect_schema(conn: psycopg.Connection, tables: Sequence[str]) -> SchemaSnapshot:
    """Row estimates, column types and indexes for the named public tables (unknown names are omitted)."""
    names = sorted({t.lower() for t in tables})
    snapshot = SchemaSnapshot()
    try:
        with conn.cursor() as cur:
            for oid, name, rows in cur.execute(_TABLES, (names,)).fetchall():
                columns = dict(cur.execute(_COLUMNS, (oid,)).fetchall())
                snapshot.tables[name] = TableInfo(name=name, row_estimate=int(rows), columns=columns)
                snapshot.indexes[name] = []
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
