import psycopg
import pytest
from labdsn import TARGET_ADMIN_DSN, TARGET_RO_DSN

from querydoctor.db import (
    ExplainFailed,
    ExplainTimeout,
    ParamMismatch,
    UnsafeRole,
    collect_schema,
    explain,
    guarded_connection,
)
from querydoctor.lab.catalog import build_catalog
from querydoctor.plan_parser import iter_nodes, parse_explain

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    with guarded_connection(TARGET_RO_DSN) as c:
        yield c


def test_session_is_read_only_with_timeouts(conn):
    assert conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
    assert conn.execute("SHOW statement_timeout").fetchone()[0] == "10s"
    assert conn.execute("SHOW lock_timeout").fetchone()[0] == "1s"
    assert conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0] == "15s"
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("CREATE TABLE should_not_exist (a int)")


def test_superuser_connection_is_refused():
    with pytest.raises(UnsafeRole):
        with guarded_connection(TARGET_ADMIN_DSN):
            pass


def test_explain_planner_only_with_params(conn):
    q = build_catalog("small")[0]
    raw = explain(conn, q.sql, q.params, execute=False)
    plan = parse_explain(raw)
    assert plan.executed is False
    assert any(n.node_type == "Seq Scan" and n.relation_name == "orders" for n in iter_nodes(plan.root))


def test_explain_analyze_returns_actuals_and_changes_nothing(conn):
    q = build_catalog("small")[0]
    plan = parse_explain(explain(conn, q.sql, q.params, execute=True))
    assert plan.executed is True
    assert plan.execution_time_ms > 0
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 100_000
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS  # fresh txn after rollback


def test_params_are_reusable_and_percent_signs_survive(conn):
    raw = explain(conn, "SELECT id FROM customers WHERE email LIKE $1 AND id % 2 = 0 AND id < $2", ["User4%", 1000], execute=False)
    assert parse_explain(raw).root.node_type


def test_zero_param_query(conn):
    assert parse_explain(explain(conn, "SELECT count(*) FROM orders", [], execute=False)).root


def test_param_count_mismatch(conn):
    with pytest.raises(ParamMismatch):
        explain(conn, "SELECT id FROM orders WHERE id = $1 AND customer_id = $2", [1], execute=False)
    with pytest.raises(ParamMismatch):
        explain(conn, "SELECT id FROM orders WHERE id = $1", [1, 2], execute=False)


def test_unsupported_param_type_rejected_before_database(conn):
    with pytest.raises(ParamMismatch):
        explain(conn, "SELECT id FROM orders WHERE id = $1", [{"a": 1}], execute=False)


def test_unknown_table_raises_explain_failed(conn):
    with pytest.raises(ExplainFailed) as exc:
        explain(conn, "SELECT * FROM no_such_table", [], execute=False)
    assert "no_such_table" in str(exc.value)


def test_statement_timeout_maps_to_explain_timeout():
    with guarded_connection(TARGET_RO_DSN, statement_timeout_ms=150) as c:
        with pytest.raises(ExplainTimeout):
            explain(c, "SELECT count(*) FROM events a, events b", [], execute=True)
        # connection is usable again
        assert c.execute("SELECT 1").fetchone()[0] == 1


def test_collect_schema_orders_has_only_primary_key(conn):
    snap = collect_schema(conn, ["orders", "customers", "does_not_exist"])
    assert set(snap.tables) == {"orders", "customers"}
    assert snap.tables["orders"].row_estimate > 90_000
    assert snap.tables["orders"].columns["customer_id"] == "integer"
    orders_idx = snap.indexes["orders"]
    assert [i.name for i in orders_idx] == ["orders_pkey"]
    assert orders_idx[0].columns == ["id"] and orders_idx[0].unique is True and orders_idx[0].size_bytes > 0
    assert [i.columns for i in snap.indexes["customers"] if i.name == "idx_customers_email"] == [["email"]]


def test_catalog_queries_show_distinct_slow_plan_patterns(conn):
    """At least five catalog queries must visibly scan an expected table sequentially."""
    expected_scans = {
        "recent_orders_by_customer": "orders",
        "orders_by_status_and_date": "orders",
        "orders_payments_join": "payments",
        "unnecessary_large_sort": "events",
        "function_on_indexed_column": "customers",
        "correlated_subquery": "orders",
        "correlated_filter_misestimate": "orders",
    }
    hits = []
    for q in build_catalog("small"):
        if q.id not in expected_scans:
            continue
        plan = parse_explain(explain(conn, q.sql, q.params, execute=False))
        if any(n.node_type == "Seq Scan" and n.relation_name == expected_scans[q.id] for n in iter_nodes(plan.root)):
            hits.append(q.id)
    assert len(hits) >= 5, hits
