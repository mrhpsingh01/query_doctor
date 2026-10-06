from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo import validate_mongo
from querydoctor.workload import WorkloadQuery
from test_workload_api import ORDERS, Target, stmt

from app.main import create_app
from app.services.workload import WorkloadDeps

BY_ID = {q.id: q for q in MONGO_CATALOG}
COLLSCAN = BY_ID["mongo_collscan_by_customer"]
SORT = BY_ID["mongo_blocking_sort"]


def wq(spec, calls, mean, **kw):
    vq = validate_mongo(spec.sql, list(spec.params))
    return WorkloadQuery(
        fingerprint=vq.fingerprint, normalized_sql=vq.shape, sample_sql=vq.canonical_json, calls=calls, total_ms=calls * mean, mean_ms=mean,
        stddev_ms=0.0, min_ms=mean, max_ms=mean, rows=calls, shared_hit_blocks=0, shared_read_blocks=1000, temp_blocks=0, p95_est_ms=mean,
        tables=vq.collections, **kw,
    )


class MongoTarget:
    """Stand-in for the MongoDB profiler."""

    def __init__(self):
        self.queries: list[WorkloadQuery] = []
        self.skipped = 0
        self.error: Exception | None = None
        self.connected: list[str] = []
        self.limits: list[int] = []
        self.demo_calls: list[str] = []

    @contextmanager
    def connect(self, uri):
        self.connected.append(uri)
        if self.error:
            raise self.error
        yield object()

    def reader(self, conn, limit):
        self.limits.append(limit)
        return list(self.queries), self.skipped


@pytest.fixture
def mongo():
    return MongoTarget()


@pytest.fixture
def pg():
    return Target()


@pytest.fixture
def api(settings, bus, stub, mongo, pg):
    deps = WorkloadDeps(
        target_dsn="stub", connect=pg.connect, reader=pg.reader, demo_runner=pg.demo_calls.append,
        mongo_uri="mongodb://stub/qd_sample", mongo_connect=mongo.connect, mongo_reader=mongo.reader, mongo_demo_runner=mongo.demo_calls.append,
    )
    with TestClient(create_app(settings, event_bus=bus, pipeline_deps=stub.deps(bus), workload_deps=deps)) as c:
        yield c


def test_importing_the_mongodb_profile_stores_an_engine_tagged_snapshot(api, mongo):
    mongo.queries, mongo.skipped = [wq(COLLSCAN, 400, 13.0), wq(SORT, 20, 60.0)], 3
    r = api.post("/api/projects/1/workload/import?engine=mongodb")
    assert r.status_code == 201
    body = r.json()
    assert (body["engine"], body["query_count"], body["skipped_count"]) == ("mongodb", 2, 3)
    assert mongo.connected == ["mongodb://stub/qd_sample"] and mongo.limits and mongo.limits[0] >= 1000


def test_the_mongodb_dashboard_ranks_by_total_time_and_is_separate_from_postgres(api, mongo, pg):
    mongo.queries = [wq(SORT, 20, 60.0), wq(COLLSCAN, 400, 13.0)]
    api.post("/api/projects/1/workload/import?engine=mongodb")
    pg.rows += [stmt(ORDERS, 10, 1.0)]
    api.post("/api/projects/1/workload/import")
    m = api.get("/api/projects/1/workload?engine=mongodb").json()
    p = api.get("/api/projects/1/workload").json()
    assert m["snapshot"]["engine"] == "mongodb" and p["snapshot"]["engine"] == "postgres"
    assert [q["fingerprint"] for q in m["queries"]] == [wq(COLLSCAN, 1, 1).fingerprint, wq(SORT, 1, 1).fingerprint]
    assert [q["tables"] for q in p["queries"]] == [["orders"]]
    assert m["compare_to"] is None and p["compare_to"] is None  # snapshots of another engine are never the comparison


def test_a_second_mongodb_import_flags_a_regression_against_the_first(api, mongo):
    mongo.queries = [wq(SORT, 30, 20.0)]
    api.post("/api/projects/1/workload/import?engine=mongodb")
    mongo.queries = [wq(SORT, 60, (30 * 20.0 + 30 * 80.0) / 60)]  # cumulative: the 30 new calls average 80 ms, four times slower
    api.post("/api/projects/1/workload/import?engine=mongodb")
    body = api.get("/api/projects/1/workload?engine=mongodb").json()
    assert [r["fingerprint"] for r in body["regressions"]] == [wq(SORT, 1, 1).fingerprint] and body["regressions"][0]["ratio"] > 2


def test_an_empty_profile_gives_an_empty_snapshot(api, mongo):
    r = api.post("/api/projects/1/workload/import?engine=mongodb")
    assert r.status_code == 201 and r.json()["query_count"] == 0
    assert api.get("/api/projects/1/workload?engine=mongodb").json()["queries"] == []


def test_a_mongodb_connection_failure_is_a_502_that_does_not_echo_the_uri(api, mongo):
    from pymongo.errors import ServerSelectionTimeoutError

    mongo.error = ServerSelectionTimeoutError("mongodb://qd_readonly:secret@host/qd_sample timed out")
    r = api.post("/api/projects/1/workload/import?engine=mongodb")
    assert r.status_code == 502 and "secret" not in r.text and "profiler" in r.json()["detail"].lower()


def test_an_unauthorised_profile_read_is_the_same_502(api, mongo):
    from pymongo.errors import OperationFailure

    mongo.error = OperationFailure("not authorized on qd_sample to execute command", 13)
    r = api.post("/api/projects/1/workload/import?engine=mongodb")
    assert r.status_code == 502 and "not authorized" not in r.text


def test_demo_traffic_is_routed_per_engine(api, mongo, pg):
    assert api.post("/api/projects/1/workload/demo", json={"mode": "regress", "engine": "mongodb"}).status_code == 202
    assert api.post("/api/projects/1/workload/demo", json={"mode": "baseline"}).status_code == 202
    assert mongo.demo_calls == ["regress"] and pg.demo_calls == ["baseline"]


def test_an_unknown_engine_is_refused(api):
    assert api.post("/api/projects/1/workload/import?engine=oracle").status_code == 422
    assert api.get("/api/projects/1/workload?engine=oracle").status_code == 422
    assert api.post("/api/projects/1/workload/demo", json={"mode": "baseline", "engine": "oracle"}).status_code == 422


def test_the_query_detail_finds_the_catalog_sample_for_a_mongodb_fingerprint(api, mongo):
    mongo.queries = [wq(COLLSCAN, 40, 13.0)]
    api.post("/api/projects/1/workload/import?engine=mongodb")
    body = api.get(f"/api/projects/1/workload/queries/{wq(COLLSCAN, 1, 1).fingerprint}").json()
    assert body["sample"]["query_id"] == "mongo_collscan_by_customer" and body["trend"][0]["calls"] == 40
