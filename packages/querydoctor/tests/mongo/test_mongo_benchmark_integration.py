import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from labdsn import MONGO_BENCH_URI, MONGO_ROOT_URI
from pymongo import MongoClient

from querydoctor.benchmark import BenchmarkConfig, BenchmarkResult
from querydoctor.benchmark.runner import BenchmarkBusy, BenchmarkCancelled, BenchmarkFailed, BenchmarkTimeout
from querydoctor.candidates import IndexCandidate, IndexColumn
from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.lab.mongo_seed import seed_mongo
from querydoctor.mongo import validate_mongo
from querydoctor.mongo.benchmark import checksum_pipeline, result_checksum, run_mongo_benchmark
from querydoctor.mongo.candidates import display_ddl
from querydoctor.mongo.sandbox import LOCK_DB, MongoSandbox, acquire_slot, drop_stale, is_sandbox_name, leftover_sandboxes

pytestmark = pytest.mark.integration
BY_ID = {q.id: q for q in MONGO_CATALOG}
FAST = BenchmarkConfig(runs=5, warmups=1)


@pytest.fixture(scope="module", autouse=True)
def lab():
    seed_mongo(MONGO_ROOT_URI, "small", if_empty=True)


@pytest.fixture
def client():
    with MongoClient(MONGO_BENCH_URI, serverSelectionTimeoutMS=3000) as c:
        for name in leftover_sandboxes(c):
            c.drop_database(name)
        c[LOCK_DB]["slots"].delete_many({})
        yield c
        leftovers = leftover_sandboxes(c)
        for name in leftovers:
            c.drop_database(name)
        c[LOCK_DB]["slots"].delete_many({})
        assert leftovers == [], f"sandbox leaked: {leftovers}"


def candidate(table, *keys):
    ddl = display_ddl(table, list(keys))
    return IndexCandidate(id=IndexCandidate.make_id(ddl), table=table, columns=[IndexColumn(name=f, desc=d == -1) for f, d in keys], ddl=ddl, rationale=["test"])


def headline():
    q = BY_ID["mongo_collscan_by_customer"]
    return validate_mongo(q.sql, list(q.params)), list(q.params), candidate("orders", ("customer_id", 1))


def test_a_real_index_makes_the_collection_scan_measurably_faster_and_leaves_no_trace(client):
    vq, params, idea = headline()
    stages: list[str] = []
    result = run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST, on_progress=stages.append)
    assert isinstance(result, BenchmarkResult) and result.before.n == 5 and result.after.n == 5
    assert result.improvement_pct >= 20 and result.after.median_ms < result.before.median_ms
    assert result.checksum_match is True and result.index_used is True
    assert result.index_size_bytes > 0 and result.index_build_ms > 0 and result.ddl == idea.ddl
    assert result.plan_fingerprint_before != result.plan_fingerprint_after
    assert result.pg_version.startswith("MongoDB 7") and result.dataset_snapshot and all(s.rows is not None for s in result.after_samples)
    expected = ["creating_sandbox", "warmup_before", "measuring_before", "creating_index", "analyzing", "warmup_after", "measuring_after", "comparing", "teardown"]
    positions = [stages.index(s) for s in expected]
    assert positions == sorted(positions)
    assert leftover_sandboxes(client) == []


def test_the_source_database_is_never_written_to(client):
    vq, params, idea = headline()
    source = client["qd_sample"]
    def snapshot():
        names = [c for c in source.list_collection_names() if not c.startswith("system.")]  # the profiler's collection is not ours
        return {c: (source[c].estimated_document_count(), sorted(i["name"] for i in source[c].list_indexes())) for c in names}

    before = snapshot()
    run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST)
    after = snapshot()
    assert before == after  # no new collections, no new index, same counts


def test_the_sandbox_starts_with_the_sources_existing_indexes_so_the_baseline_is_fair(client):
    with MongoSandbox(client) as box:
        box.create(["orders"])
        names = {i["name"] for i in box.db["orders"].list_indexes()}
        assert {"_id_", "status_1", "created_at_1_customer_id_1"} <= names and "customer_id_1" not in names
        assert box.db["orders"].estimated_document_count() == client["qd_sample"]["orders"].estimated_document_count()


def test_a_find_and_an_aggregate_checksum_the_same_documents_in_any_order(client):
    q = validate_mongo('{"find": "customers", "filter": {"_id": {"$lte": 40}}}', [])
    reversed_ = validate_mongo('{"find": "customers", "filter": {"_id": {"$lte": 40}}, "sort": {"_id": -1}}', [])
    db = client["qd_sample"]
    assert result_checksum(db, q) == result_checksum(db, reversed_) and result_checksum(db, q).startswith("40:")
    other = validate_mongo('{"find": "customers", "filter": {"_id": {"$lte": 41}}}', [])
    assert result_checksum(db, other) != result_checksum(db, q)
    agg = validate_mongo('{"aggregate": "customers", "pipeline": [{"$match": {"_id": {"$lte": 40}}}]}', [])
    assert result_checksum(db, agg) == result_checksum(db, q)
    empty = validate_mongo('{"find": "customers", "filter": {"_id": -1}}', [])
    assert result_checksum(db, empty) == "0:0"
    collection, pipeline = checksum_pipeline(q)
    assert collection == "customers" and "$toHashedIndexKey" in str(pipeline[-1])


def test_a_lookup_pipeline_is_benchmarked_with_an_index_on_the_target_collection(client):
    q = BY_ID["mongo_lookup_without_index"]
    vq = validate_mongo(q.sql, list(q.params))
    result = run_mongo_benchmark(MONGO_BENCH_URI, vq, list(q.params), candidate("orders", ("customer_id", 1)), BenchmarkConfig(runs=5, warmups=0))
    assert result.checksum_match is True and result.index_used is False or result.improvement_pct > 50
    assert result.improvement_pct > 50
    assert leftover_sandboxes(client) == []


@pytest.mark.parametrize("names", [("$where",), ("",), ("a\x00b",)])
def test_a_hostile_candidate_is_refused_before_anything_is_created(client, names):
    vq, params, _ = headline()
    bad = IndexCandidate(id="x", table="orders", columns=[IndexColumn(name=n) for n in names], ddl='db.orders.dropDatabase()', rationale=["x"])
    with pytest.raises(BenchmarkFailed):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, bad, FAST)
    assert leftover_sandboxes(client) == []


def test_an_index_on_a_collection_the_query_does_not_use_is_refused(client):
    vq, params, _ = headline()
    with pytest.raises(BenchmarkFailed):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, candidate("events", ("customer_id", 1)), FAST)


def test_the_index_keys_come_from_the_structure_not_the_display_string(client):
    vq, params, idea = headline()
    idea.ddl = "db.orders.dropDatabase()  // poisoned display text"
    result = run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST)
    assert result.index_used is True and client["qd_sample"]["orders"].estimated_document_count() > 0


def test_a_failure_in_the_middle_still_removes_the_sandbox(client, monkeypatch):
    import querydoctor.mongo.benchmark as module

    vq, params, idea = headline()
    calls = {"n": 0}
    original = module.explain_query

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("boom")
        return original(*a, **k)

    monkeypatch.setattr(module, "explain_query", flaky)
    with pytest.raises(RuntimeError):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST)
    assert leftover_sandboxes(client) == []


def test_cancellation_stops_the_benchmark_and_removes_the_sandbox(client):
    vq, params, idea = headline()
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(BenchmarkCancelled):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST, cancelled=cancelled)
    assert leftover_sandboxes(client) == []


def test_a_benchmark_over_its_time_limit_is_stopped_and_cleaned_up(client):
    vq, params, idea = headline()
    with pytest.raises(BenchmarkTimeout):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST.model_copy(update={"timeout_s": 0}))
    assert leftover_sandboxes(client) == []


def test_a_progress_callback_that_fails_does_not_prevent_cleanup(client):
    vq, params, idea = headline()

    def boom(stage):
        if stage == "creating_index":
            raise RuntimeError("the metadata database is unreachable")

    with pytest.raises(RuntimeError):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST, on_progress=boom)
    assert leftover_sandboxes(client) == []


def test_at_most_two_benchmarks_run_at_once_and_a_dead_holders_slot_is_reclaimed(client):
    first, second = acquire_slot(client), acquire_slot(client)
    vq, params, idea = headline()
    with pytest.raises(BenchmarkBusy):
        run_mongo_benchmark(MONGO_BENCH_URI, vq, params, idea, FAST)
    first.release()
    third = acquire_slot(client)  # the freed slot is available again
    assert {second.slot, third.slot} == {0, 1}
    for lease in (second, third):
        lease.release()
    # a holder that died leaves an expired slot: it is reclaimed immediately, not after the TTL sweep
    client[LOCK_DB]["slots"].insert_one({"_id": 0, "owner": "dead", "expires_at": (datetime.now(UTC) - timedelta(seconds=5)).replace(tzinfo=None)})
    client[LOCK_DB]["slots"].insert_one({"_id": 1, "owner": "dead2", "expires_at": (datetime.now(UTC) - timedelta(seconds=5)).replace(tzinfo=None)})
    acquire_slot(client).release()


def test_stale_sandboxes_are_dropped_and_fresh_ones_are_not(client):
    with MongoSandbox(client) as fresh:
        fresh.create(["customers"])
        assert drop_stale(client, max_age_s=3600) == 0 and fresh.name in client.list_database_names()
        assert drop_stale(client, max_age_s=0) == 1
    orphan = f"bench_{uuid.uuid4().hex[:12]}"
    client[orphan]["x"].insert_one({"a": 1})  # no marker at all: treated as ancient
    assert drop_stale(client, max_age_s=3600) == 1 and orphan not in client.list_database_names()


def test_only_names_of_the_sandbox_shape_are_ever_dropped(client):
    assert is_sandbox_name("bench_0123456789ab")
    for name in ("qd_sample", "bench_", "bench_ZZ23456789ab", "bench_0123456789abc", "admin", "xbench_0123456789ab"):
        assert not is_sandbox_name(name)
    box = MongoSandbox(client)
    box.name = "qd_sample"
    box.drop()  # refuses silently: the source database is still there
    assert "qd_sample" in client.list_database_names()
