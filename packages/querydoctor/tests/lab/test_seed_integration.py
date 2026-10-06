import psycopg
import pytest
from labdsn import TARGET_ADMIN_DSN

from querydoctor.lab.seed import seed

pytestmark = pytest.mark.integration


def test_seed_small_into_target_loads_rows_and_only_pk_indexes():
    counts = seed(TARGET_ADMIN_DSN, "small")
    assert counts["orders"] == 100_000
    with psycopg.connect(TARGET_ADMIN_DSN) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 100_000
        names = [r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")]
        # Primary keys plus exactly one deliberate secondary index (a plain email index that lower(email) defeats).
        assert sorted(n for n in names if not n.endswith("_pkey")) == ["idx_customers_email"], names
        analyzed = conn.execute("SELECT reltuples FROM pg_class WHERE relname = 'orders'").fetchone()[0]
        assert analyzed > 90_000  # ANALYZE ran


def test_if_empty_skips_an_already_seeded_database_but_seeds_a_missing_one():
    seed(TARGET_ADMIN_DSN, "small")
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM payments WHERE id = 1")  # a mark that a reload would erase
    assert seed(TARGET_ADMIN_DSN, "small", if_empty=True) == {}
    with psycopg.connect(TARGET_ADMIN_DSN, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM payments WHERE id = 1").fetchone()[0] == 0
        conn.execute("DROP TABLE orders CASCADE")
    assert seed(TARGET_ADMIN_DSN, "small", if_empty=True)["orders"] == 100_000  # missing table => reseed


def test_readonly_role_can_read_pg_stat_statements_for_all_roles():
    """The workload importer reads other roles' statements through the read-only role, which needs pg_read_all_stats."""
    from labdsn import TARGET_RO_DSN

    seed(TARGET_ADMIN_DSN, "small")
    with psycopg.connect(TARGET_RO_DSN) as conn:
        assert conn.execute("SELECT pg_has_role('qd_readonly', 'pg_read_all_stats', 'member')").fetchone()[0] is True
        # with the privilege the query text of every role is visible (without it, other roles' text is <insufficient privilege>)
        texts = [r[0] for r in conn.execute("SELECT query FROM pg_stat_statements WHERE query LIKE 'COPY%' LIMIT 5")]
        assert texts and all("insufficient privilege" not in t for t in texts)


def test_with_workload_needs_a_workload_dsn():
    from querydoctor.lab.seed import main

    with pytest.raises(SystemExit):
        main(["--dsn", TARGET_ADMIN_DSN, "--with-workload"])


def test_warm_workload_resets_statistics_and_leaves_demo_traffic_to_import():
    from labdsn import TARGET_RO_DSN

    from querydoctor.lab.seed import warm_workload
    from querydoctor.workload import merge_statements, read_statements

    seed(TARGET_ADMIN_DSN, "small")
    done = warm_workload(TARGET_ADMIN_DSN, TARGET_RO_DSN, "small", scale_calls=0.1)
    assert done["recent_orders_by_customer"] >= 100
    with psycopg.connect(TARGET_RO_DSN) as conn:
        queries, _skipped = merge_statements(read_statements(conn, limit=200))
    assert max(q.calls for q in queries) >= 500  # the cheap lookups
    assert not any("COPY" in q.sample_sql for q in queries)  # the seeding statements were reset away
