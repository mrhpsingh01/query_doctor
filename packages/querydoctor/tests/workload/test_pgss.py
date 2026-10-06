import math

import pytest
from labdsn import TARGET_RO_DSN

from querydoctor.workload import StatementRow, WorkloadQuery, merge_statements, read_statements


def row(query, calls, total, mean, stddev, **kw):
    base = dict(min_ms=0.0, max_ms=mean * 2, rows=calls, shared_hit_blocks=0, shared_read_blocks=0, temp_blocks=0)
    return StatementRow(query=query, calls=calls, total_ms=total, mean_ms=mean, stddev_ms=stddev, **{**base, **kw})


def test_rows_for_the_same_query_shape_merge_with_pooled_statistics():
    merged, skipped = merge_statements([
        row("SELECT * FROM orders WHERE customer_id = $1", 100, 1000.0, 10.0, 2.0, min_ms=4.0, max_ms=30.0, rows=100,
            shared_hit_blocks=10, shared_read_blocks=1, temp_blocks=0),
        row("SELECT * FROM orders WHERE customer_id = 42", 300, 9000.0, 30.0, 5.0, min_ms=8.0, max_ms=90.0, rows=600,
            shared_hit_blocks=30, shared_read_blocks=2, temp_blocks=5),
    ])
    assert skipped == 0 and len(merged) == 1
    q = merged[0]
    assert isinstance(q, WorkloadQuery)
    assert (q.calls, q.total_ms, q.mean_ms) == (400, 10_000.0, 25.0)
    # population variance of the union: sum(n*(s^2+m^2))/N - mean^2 = (10400 + 277500)/400 - 625 = 94.75
    assert q.stddev_ms == pytest.approx(math.sqrt(94.75), abs=1e-9)
    assert (q.min_ms, q.max_ms, q.rows) == (4.0, 90.0, 700)
    assert (q.shared_hit_blocks, q.shared_read_blocks, q.temp_blocks) == (40, 3, 5)
    assert q.p95_est_ms == pytest.approx(25.0 + 1.645 * q.stddev_ms)
    assert q.sample_sql == "SELECT * FROM orders WHERE customer_id = 42"  # the heaviest contributing statement's text


def test_a_pgss_placeholder_query_and_the_same_query_with_literals_share_a_fingerprint():
    a, _ = merge_statements([row("SELECT id FROM orders WHERE customer_id = $1 AND status = $2", 1, 1.0, 1.0, 0.0)])
    b, _ = merge_statements([row("SELECT id FROM orders WHERE customer_id = 7 AND status = 'x'", 1, 1.0, 1.0, 0.0)])
    assert a[0].fingerprint == b[0].fingerprint


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO orders VALUES ($1)", "EXPLAIN (FORMAT JSON) SELECT 1 FROM orders", "SET statement_timeout = $1",
        "SELEC nonsense ((", "SELECT $1", "SELECT 1", "COPY orders TO STDOUT", "SELECT * FROM pg_stat_statements",
        "SELECT relname FROM pg_class WHERE oid = $1", "SELECT * FROM information_schema.tables",
    ],
)
def test_non_select_unparsable_tableless_and_catalog_statements_are_skipped_and_counted(query):
    merged, skipped = merge_statements([row(query, 5, 5.0, 1.0, 0.0), row("SELECT * FROM orders", 1, 1.0, 1.0, 0.0)])
    assert skipped == 1 and [q.normalized_sql for q in merged] == ["SELECT * FROM orders"]


def test_empty_input_and_zero_call_rows_are_safe():
    assert merge_statements([]) == ([], 0)
    merged, skipped = merge_statements([row("SELECT * FROM orders", 0, 0.0, 0.0, 0.0)])
    assert skipped == 1 and merged == []  # a statement that never ran carries no information


def test_results_are_ordered_by_total_time_descending():
    merged, _ = merge_statements([
        row("SELECT * FROM events", 1, 5.0, 5.0, 0.0), row("SELECT * FROM orders", 1000, 900.0, 0.9, 0.1),
        row("SELECT * FROM payments", 2, 40.0, 20.0, 1.0),
    ])
    assert [q.total_ms for q in merged] == [900.0, 40.0, 5.0]


@pytest.mark.integration
def test_read_statements_returns_the_labs_statements_ordered_by_total_time():
    import psycopg

    with psycopg.connect(TARGET_RO_DSN) as conn:
        conn.execute("SELECT count(*) FROM orders").fetchone()
        rows = read_statements(conn, limit=50)
    assert rows and all(isinstance(r, StatementRow) for r in rows)
    assert [r.total_ms for r in rows] == sorted((r.total_ms for r in rows), reverse=True)
    assert all(r.calls > 0 for r in rows)
