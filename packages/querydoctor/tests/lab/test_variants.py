from collections import Counter

import psycopg
import pytest
from labdsn import BENCH_ADMIN_DSN, BENCH_SUPER_DSN

from querydoctor.benchmark.sandbox import Sandbox
from querydoctor.lab.generator import COUNTS, ZIPF, generate
from querydoctor.lab.variants import VARIANTS, ensure_variant, variant_db_name


def top_one_percent_share(variant: str) -> float:
    (_, batches), = generate(variant, tables=["orders"])
    per_customer = Counter(row[1] for batch in batches for row in batch)
    n_customers = COUNTS[variant]["customers"]
    top = sum(count for _, count in per_customer.most_common(max(n_customers // 100, 1)))
    return top / COUNTS[variant]["orders"]


def test_variants_are_the_five_the_ranker_trains_on():
    assert VARIANTS == ["tiny", "small", "medium", "skew_low", "skew_high"]
    assert variant_db_name("tiny") == "qd_variant_tiny"


def test_counts_per_variant():
    assert COUNTS["tiny"] == {"customers": 1_000, "orders": 10_000, "order_items": 30_000, "events": 50_000}
    assert COUNTS["medium"] == {"customers": 30_000, "orders": 300_000, "order_items": 900_000, "events": 1_500_000}
    assert COUNTS["skew_low"] == COUNTS["skew_high"] == COUNTS["small"]  # the skew variants differ only in distribution
    assert COUNTS["small"]["orders"] == 100_000  # existing scales are untouched


def test_zipf_exponents_per_variant():
    assert ZIPF["skew_low"] == 0.5 and ZIPF["skew_high"] == 1.1
    assert ZIPF["small"] == ZIPF["tiny"] == ZIPF["medium"] == 0.8  # the default customer skew


def test_a_higher_zipf_concentrates_orders_on_fewer_customers():
    low, high = top_one_percent_share("skew_low"), top_one_percent_share("skew_high")
    assert high > low + 0.15, (low, high)


def test_same_seed_is_deterministic_for_a_variant():
    def head(seed):
        (_, batches), = generate("tiny", seed=seed, tables=["orders"])
        return next(iter(batches))[:50]

    assert head(42) == head(42) and head(42) != head(43)


@pytest.mark.integration
def test_ensure_variant_is_idempotent_seeds_the_right_rows_and_installs_hypopg():
    name = ensure_variant(BENCH_SUPER_DSN, "tiny")
    assert name == "qd_variant_tiny"
    with psycopg.connect(BENCH_SUPER_DSN, autocommit=True) as conn:
        oid = conn.execute("SELECT oid FROM pg_database WHERE datname = %s", (name,)).fetchone()[0]
    assert ensure_variant(BENCH_SUPER_DSN, "tiny") == name  # second call changes nothing
    with psycopg.connect(BENCH_SUPER_DSN, autocommit=True) as conn:
        assert conn.execute("SELECT oid FROM pg_database WHERE datname = %s", (name,)).fetchone()[0] == oid

    with Sandbox(BENCH_ADMIN_DSN, template=name) as clone:  # the way a dataset run uses it
        with psycopg.connect(clone.dsn) as conn:
            assert conn.execute("SELECT count(*) FROM customers").fetchone()[0] == 1_000
            assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 10_000
            assert conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'hypopg'").fetchone() is not None
            assert conn.execute("SELECT relowner = (SELECT oid FROM pg_roles WHERE rolname = 'qd_bench') FROM pg_class WHERE relname = 'orders'").fetchone()[0]
    with psycopg.connect(BENCH_SUPER_DSN, autocommit=True) as conn:
        assert conn.execute(r"SELECT count(*) FROM pg_database WHERE datname LIKE 'bench\_%'").fetchone()[0] == 0


@pytest.mark.integration
def test_an_unknown_variant_is_rejected_before_touching_the_database():
    with pytest.raises(ValueError):
        ensure_variant(BENCH_SUPER_DSN, "huge")
