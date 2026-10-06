import psycopg
import pytest
from conftest import TARGET_ADMIN_DSN

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
