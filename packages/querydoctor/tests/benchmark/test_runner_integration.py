import psycopg
import pytest
from labdsn import BENCH_ADMIN_DSN, BENCH_SAMPLE_DSN

from querydoctor.benchmark import BenchmarkConfig, BenchmarkResult
from querydoctor.benchmark.checksum import result_checksum
from querydoctor.benchmark.runner import (
    BenchmarkBusy,
    BenchmarkCancelled,
    BenchmarkFailed,
    BenchmarkTimeout,
    run_benchmark,
)
from querydoctor.benchmark.sandbox import drop_stale
from querydoctor.lab.catalog import build_catalog

pytestmark = pytest.mark.integration

Q1 = build_catalog("small")[0]
COVERING = "CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)"
FAST = BenchmarkConfig(runs=5, warmups=1)


def leftover_databases():
    with psycopg.connect(BENCH_ADMIN_DSN, autocommit=True) as conn:
        return [r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'bench\\_%'")]


@pytest.fixture(autouse=True)
def clean():
    drop_stale(BENCH_ADMIN_DSN, max_age_s=0)
    yield
    leftovers = leftover_databases()
    drop_stale(BENCH_ADMIN_DSN, max_age_s=0)
    assert leftovers == [], f"sandbox leaked: {leftovers}"


# ---- result checksum ----------------------------------------------------------------------------------------------

def test_checksum_is_stable_and_sensitive_to_the_data():
    with psycopg.connect(BENCH_SAMPLE_DSN) as conn:
        a = result_checksum(conn, "SELECT id, country FROM customers WHERE id <= $1", [50], ordered=False)
        b = result_checksum(conn, "SELECT id, country FROM customers WHERE id <= $1", [50], ordered=False)
        c = result_checksum(conn, "SELECT id, country FROM customers WHERE id <= $1", [51], ordered=False)
    assert a == b and a != c


def test_unordered_checksums_ignore_row_order_but_ordered_ones_do_not():
    with psycopg.connect(BENCH_SAMPLE_DSN) as conn:
        asc = "SELECT id FROM customers WHERE id <= 20 ORDER BY id"
        desc = "SELECT id FROM customers WHERE id <= 20 ORDER BY id DESC"
        assert result_checksum(conn, asc, [], ordered=False) == result_checksum(conn, desc, [], ordered=False)
        assert result_checksum(conn, asc, [], ordered=True) != result_checksum(conn, desc, [], ordered=True)


def test_checksum_of_an_empty_result_is_defined():
    with psycopg.connect(BENCH_SAMPLE_DSN) as conn:
        assert result_checksum(conn, "SELECT id FROM customers WHERE id < 0", [], ordered=False)


# ---- the benchmark itself -----------------------------------------------------------------------------------------

def test_a_covering_index_makes_the_headline_query_measurably_faster_and_leaves_no_trace():
    stages: list[str] = []
    result = run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST, on_progress=stages.append)
    assert isinstance(result, BenchmarkResult)
    assert result.before.n == 5 and result.after.n == 5
    assert len(result.before_samples) == 5 and len(result.after_samples) == 5
    assert result.after.median_ms < result.before.median_ms
    assert result.improvement_pct > 20
    assert result.index_size_bytes > 0 and result.index_build_ms > 0
    assert result.checksum_match is True
    assert result.plan_fingerprint_before != result.plan_fingerprint_after
    assert result.pg_version.startswith("PostgreSQL 16") and "work_mem" in result.settings
    assert result.dataset_snapshot and result.ddl == COVERING
    assert all(s.rows is not None for s in result.after_samples)

    expected = ["creating_sandbox", "warmup_before", "measuring_before", "creating_index", "analyzing",
                "warmup_after", "measuring_after", "comparing", "teardown"]
    positions = [stages.index(s) for s in expected]
    assert positions == sorted(positions)
    assert leftover_databases() == []


def test_benchmarks_of_the_same_candidate_share_a_dataset_snapshot_id():
    a = run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST)
    b = run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST)
    assert a.dataset_snapshot == b.dataset_snapshot


def test_an_invalid_candidate_ddl_fails_cleanly_and_still_tears_down():
    with pytest.raises(BenchmarkFailed):
        run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, "CREATE INDEX ON orders (no_such_column)", FAST)
    with pytest.raises(BenchmarkFailed):
        run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, "DROP TABLE orders", FAST)  # never run arbitrary DDL
    with pytest.raises(BenchmarkFailed):
        run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, "CREATE INDEX ON orders (id); DROP TABLE orders", FAST)


def test_a_failing_query_fails_cleanly():
    with pytest.raises(BenchmarkFailed):
        run_benchmark(BENCH_ADMIN_DSN, "SELECT * FROM no_such_table", [], COVERING, FAST)


def test_cancellation_between_steps_stops_the_benchmark_and_tears_down():
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(BenchmarkCancelled):
        run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST, cancelled=cancelled)


def test_a_benchmark_that_exceeds_its_time_limit_is_stopped():
    with pytest.raises(BenchmarkTimeout):
        run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST.model_copy(update={"timeout_s": 0}))


def test_concurrency_is_capped_and_recovers_when_a_slot_frees():
    holders = [psycopg.connect(BENCH_ADMIN_DSN, autocommit=True) for _ in range(2)]
    try:
        for i, h in enumerate(holders):
            assert h.execute("SELECT pg_try_advisory_lock(%s)", (7100 + i,)).fetchone()[0]
        with pytest.raises(BenchmarkBusy):
            run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST)
        holders[0].execute("SELECT pg_advisory_unlock(%s)", (7100,))
        assert run_benchmark(BENCH_ADMIN_DSN, Q1.sql, Q1.params, COVERING, FAST).checksum_match is True
    finally:
        for h in holders:
            h.close()
