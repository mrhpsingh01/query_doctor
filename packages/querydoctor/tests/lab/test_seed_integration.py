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
