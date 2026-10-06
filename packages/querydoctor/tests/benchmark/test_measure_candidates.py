import psycopg
import pytest
from labdsn import BENCH_ADMIN_DSN

from querydoctor.benchmark import BenchmarkConfig
from querydoctor.benchmark.runner import (
    BenchmarkBusy,
    BenchmarkCancelled,
    BenchmarkTimeout,
    CandidateMeasurement,
    measure_candidates,
)
from querydoctor.benchmark.sandbox import drop_stale
from querydoctor.lab.catalog import build_catalog

pytestmark = pytest.mark.integration

Q1 = build_catalog("small")[0]
GOOD = "CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)"
USELESS = "CREATE INDEX ON payments (method)"
INVALID = "CREATE INDEX ON orders (no_such_column)"
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


def measure(ddls, **kw):
    return measure_candidates(BENCH_ADMIN_DSN, Q1.sql, Q1.params, ddls, FAST, **kw)


def test_good_useless_and_invalid_candidates_are_measured_against_one_shared_baseline():
    good, useless, invalid = measure([GOOD, USELESS, INVALID])
    assert [m.ddl for m in (good, useless, invalid)] == [GOOD, USELESS, INVALID]
    assert all(isinstance(m, CandidateMeasurement) for m in (good, useless, invalid))
    assert good.error is None and good.result.improvement_pct > 20
    assert good.result.index_used is True and good.result.checksum_match is True
    assert useless.error is None and useless.result.index_used is False
    assert invalid.result is None and invalid.error and "no_such_column" in invalid.error
    # one baseline: identical values, not merely similar ones
    assert good.result.before == useless.result.before
    assert good.result.before_samples == useless.result.before_samples
    assert good.result.plan_fingerprint_before == useless.result.plan_fingerprint_before


def test_each_index_is_dropped_before_the_next_candidate_so_they_cannot_influence_each_other():
    good, useless = measure([GOOD, USELESS])
    # the useless index changes nothing: its plan equals the original plan, which it could not if GOOD were still there
    assert useless.result.plan_fingerprint_after == useless.result.plan_fingerprint_before
    assert good.result.plan_fingerprint_after != good.result.plan_fingerprint_before


def test_the_order_of_candidates_does_not_change_a_good_candidates_result_materially():
    useless, good = measure([USELESS, GOOD])
    assert good.result.improvement_pct > 20 and good.result.index_used is True
    assert useless.result.index_used is False


def test_the_baseline_is_measured_once_however_many_candidates():
    stages: list[str] = []
    measure([GOOD, USELESS, INVALID], on_progress=stages.append)
    assert stages.count("measuring_before") == 1 and stages.count("creating_sandbox") == 1
    assert stages.count("measuring_after") == 2  # the invalid candidate never reaches measurement
    assert stages[-1] == "teardown"


def test_ddl_that_is_not_a_plain_create_index_is_reported_per_candidate_and_never_run():
    results = measure(["DROP TABLE orders", "CREATE INDEX ON orders (id); DROP TABLE orders", GOOD])
    assert results[0].result is None and results[0].error and "not a plain CREATE INDEX" in results[0].error
    assert results[1].result is None and results[1].error
    assert results[2].error is None  # the orders table still exists, and the good candidate was measured
    assert results[2].result.improvement_pct > 20


def test_measured_queries_run_in_read_only_transactions(monkeypatch):
    import querydoctor.benchmark.runner as runner_module

    seen = []
    original = runner_module.explain

    def spy(conn, sql, params, *, execute):
        seen.append(conn.execute("SHOW transaction_read_only").fetchone()[0])
        return original(conn, sql, params, execute=execute)

    monkeypatch.setattr(runner_module, "explain", spy)
    measure([GOOD, USELESS])
    assert len(seen) >= 3 * (FAST.runs + FAST.warmups) and set(seen) == {"on"}


def test_no_candidates_means_no_work():
    assert measure([]) == []
    assert leftover_databases() == []


def test_cancellation_between_candidates_stops_everything_and_tears_down():
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 10  # past the baseline (6 checks), inside the first candidate

    with pytest.raises(BenchmarkCancelled):
        measure([GOOD, USELESS], cancelled=cancelled)


def test_a_baseline_that_exceeds_the_time_limit_is_stopped():
    with pytest.raises(BenchmarkTimeout):
        measure_candidates(BENCH_ADMIN_DSN, Q1.sql, Q1.params, [GOOD], FAST.model_copy(update={"timeout_s": 0}))


def test_all_slots_busy_is_reported():
    holders = [psycopg.connect(BENCH_ADMIN_DSN, autocommit=True) for _ in range(2)]
    try:
        for i, h in enumerate(holders):
            assert h.execute("SELECT pg_try_advisory_lock(%s)", (7100 + i,)).fetchone()[0]
        with pytest.raises(BenchmarkBusy):
            measure([GOOD])
    finally:
        for h in holders:
            h.close()


def test_a_progress_callback_that_fails_still_tears_the_sandbox_down():
    def boom(stage):
        if stage == "analyzing":
            raise RuntimeError("the metadata database is unreachable")

    with pytest.raises(RuntimeError):
        measure([GOOD], on_progress=boom)
