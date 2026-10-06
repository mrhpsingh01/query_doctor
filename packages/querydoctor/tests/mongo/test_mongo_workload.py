from datetime import datetime

import pytest
from labdsn import MONGO_RO_URI, MONGO_ROOT_URI
from pymongo import MongoClient

from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo import validate_mongo
from querydoctor.mongo.workload import read_profile

BY_ID = {q.id: q for q in MONGO_CATALOG}


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, flt):
        self.flt = flt
        return self

    def sort(self, *a):
        return self

    def limit(self, n):
        self.n = n
        self.docs = self.docs[:n]
        return iter(self.docs)


class FakeDb:
    def __init__(self, docs):
        self.profile = FakeCollection(docs)

    def __getitem__(self, name):
        assert name == "system.profile"
        return self.profile


def entry(command, millis=5, ns="qd_sample.orders", op="query", **kw):
    return {"op": op, "ns": ns, "command": command, "millis": millis, "ts": datetime(2026, 1, 1), **kw}


def find(customer, millis=5, **kw):
    return entry({"find": "orders", "filter": {"customer_id": customer}, "lsid": {"id": 1}, "$db": "qd_sample", "maxTimeMS": 10000}, millis, **kw)


def read(*docs, limit=100):
    return read_profile(FakeDb(list(docs)), limit)


def test_the_same_query_with_different_literals_is_one_shape_with_grouped_statistics():
    queries, skipped = read(
        find(1, 10, docsExamined=1000, keysExamined=0, nreturned=3), find(2, 20, docsExamined=3000, keysExamined=0, nreturned=7),
        find(3, 30, docsExamined=2000, keysExamined=0, nreturned=0),
    )
    assert skipped == 0 and len(queries) == 1
    q = queries[0]
    assert (q.calls, q.total_ms, q.mean_ms, q.min_ms, q.max_ms) == (3, 60, 20, 10, 30)
    assert q.stddev_ms == pytest.approx(8.164966, rel=1e-5)  # population standard deviation of 10, 20, 30
    assert q.shared_read_blocks == 6000 and q.rows == 10 and q.tables == ["orders"] and q.p95_est_ms == pytest.approx(20 + 1.645 * q.stddev_ms)
    assert '"customer_id":"?"' in q.normalized_sql.replace(" ", "") and "1" not in q.normalized_sql.replace("orders", "")


def test_the_fingerprint_matches_the_fingerprint_of_an_analysis_of_the_same_query():
    spec = BY_ID["mongo_collscan_by_customer"]
    analysed = validate_mongo(spec.sql, list(spec.params))
    queries, _ = read(find(77, 4))
    assert queries[0].fingerprint == analysed.fingerprint


def test_different_shapes_are_kept_apart_and_ranked_by_total_time():
    other = entry({"aggregate": "events", "pipeline": [{"$group": {"_id": "$customer_id", "n": {"$sum": 1}}}], "cursor": {}}, 500, ns="qd_sample.events", op="command")
    queries, _ = read(find(1, 10), find(2, 10), other)
    assert [q.tables for q in queries] == [["events"], ["orders"]] and queries[0].total_ms == 500
    assert queries[0].fingerprint != queries[1].fingerprint


def test_the_heaviest_member_provides_the_sample_query():
    queries, _ = read(find(1, 5), find(99, 90), find(3, 10))
    assert '"customer_id":99' in queries[0].sample_sql.replace(" ", "")


def test_system_internal_and_non_read_commands_are_ignored_and_counted():
    ignored = [
        entry({"insert": "orders", "documents": [{"a": 1}]}, op="insert"),
        entry({"listIndexes": "orders"}, op="command"),
        entry({"explain": {"find": "orders", "filter": {"a": 1}}, "verbosity": "executionStats"}, op="command"),
        entry({"getMore": 123, "collection": "orders"}, op="getmore"),
        entry({"find": "system.profile", "filter": {}}, ns="qd_sample.system.profile"),
        entry({"find": 5}),
        entry({}),
    ]
    queries, skipped = read(*ignored, find(1))
    assert len(queries) == 1 and skipped == len(ignored)


def test_unusable_specs_are_skipped_not_fatal():
    hostile = entry({"find": "orders", "filter": {"$where": "true"}})
    queries, skipped = read(hostile, find(1))
    assert len(queries) == 1 and skipped == 1


def test_bson_values_such_as_dates_and_object_ids_do_not_break_normalization():
    from bson import ObjectId

    a = entry({"find": "orders", "filter": {"created_at": {"$gte": datetime(2022, 1, 1)}, "_id": ObjectId()}})
    b = entry({"find": "orders", "filter": {"created_at": {"$gte": datetime(2023, 5, 5)}, "_id": ObjectId()}})
    queries, skipped = read(a, b)
    assert len(queries) == 1 and queries[0].calls == 2 and skipped == 0


def test_an_empty_profile_gives_an_empty_result():
    assert read() == ([], 0)


def test_the_limit_bounds_how_many_entries_are_read():
    queries, _ = read(*[find(i) for i in range(50)], limit=10)
    assert queries[0].calls == 10


# ---- live: demo traffic -> profile -> ranking ------------------------------------------------------------------------

@pytest.mark.integration
def test_after_the_demo_traffic_a_moderately_slow_frequent_query_ranks_first_by_total_time():
    from querydoctor.lab.workload import reset_mongo_profile, run_mongo_workload

    reset_mongo_profile(MONGO_ROOT_URI)
    done = run_mongo_workload(MONGO_RO_URI, "small", "baseline", seed=7, scale_calls=0.1)
    assert done["mongo_collscan_by_customer"] >= 80 and done["mongo_pk_customer_lookup"] >= 500
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        queries, _skipped = read_profile(client["qd_sample"], 20_000)
    fp = lambda qid: validate_mongo(BY_ID[qid].sql, list(BY_ID[qid].params)).fingerprint  # noqa: E731
    by_fp = {q.fingerprint: q for q in queries}
    hot, heavy = by_fp[fp("mongo_collscan_by_customer")], by_fp[fp("mongo_unbounded_group")]
    assert heavy.mean_ms > 3 * hot.mean_ms  # the unbounded group is far slower per call ...
    assert queries[0].fingerprint == hot.fingerprint  # ... but the frequent query costs the most overall
    assert len(queries) >= 8


@pytest.mark.integration
def test_the_regress_mode_makes_one_shape_slower_between_two_reads():
    from querydoctor.lab.workload import reset_mongo_profile, run_mongo_workload
    from querydoctor.workload.analysis import detect_regressions

    reset_mongo_profile(MONGO_ROOT_URI)
    run_mongo_workload(MONGO_RO_URI, "small", "baseline", seed=7, scale_calls=0.1)
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        before, _ = read_profile(client["qd_sample"], 20_000)
        run_mongo_workload(MONGO_RO_URI, "small", "regress", seed=7, scale_calls=0.1)
        after, _ = read_profile(client["qd_sample"], 20_000)
    found = detect_regressions(before, after)
    sort_fp = validate_mongo(BY_ID["mongo_blocking_sort"].sql, list(BY_ID["mongo_blocking_sort"].params)).fingerprint
    assert sort_fp in [r.fingerprint for r in found] and max(r.ratio for r in found) >= 1.5


def test_the_mongodb_adapter_collects_the_workload_from_the_profiler():
    from querydoctor.adapters import get_adapter

    adapter = get_adapter("mongodb")
    queries, skipped = adapter.collect_workload(FakeDb([find(1, 12), find(2, 8)]), 100)
    assert (len(queries), queries[0].calls, queries[0].total_ms, skipped) == (1, 2, 20, 0)
