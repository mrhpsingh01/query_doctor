import json
import pathlib
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo.rules import CollectionInfo, IndexSpec
from querydoctor.mongo.schema import MongoSchema
from test_benchmarks_api import make_result

from app.main import create_app
from app.services.benchmarks import BenchmarkDeps

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "mongo_plans"
BY_ID = {q.id: q for q in MONGO_CATALOG}
SPEC = BY_ID["mongo_collscan_by_customer"]


def raw_plan(name="mongo_collscan_by_customer.stats"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class MongoStub:
    """Programmable stand-in for the MongoDB server: connection, explain and schema."""

    def __init__(self):
        self.raw = raw_plan()
        self.explain_calls: list[dict] = []
        self.connected: list[str] = []
        self.runner_calls: list[dict] = []

    @contextmanager
    def connect(self, uri):
        self.connected.append(uri)
        yield object()

    def explain(self, conn, vq, params, *, execute):
        self.explain_calls.append({"kind": vq.query.kind, "collection": vq.query.collection, "execute": execute})
        return self.raw

    def collect_schema(self, conn, collections):
        return MongoSchema(collections={
            "orders": CollectionInfo(count=100_000, indexes=[IndexSpec(name="status_1", keys=[("status", 1)])]),
            "customers": CollectionInfo(count=10_000), "events": CollectionInfo(count=500_000),
        })

    def runner(self, bench_uri, vq, params, candidate, config, on_progress=lambda s: None, *, cancelled=lambda: False, source_db="qd_sample"):
        self.runner_calls.append({"uri": bench_uri, "collections": vq.collections, "table": candidate.table, "columns": [(c.name, c.desc) for c in candidate.columns]})
        for stage in ("creating_sandbox", "measuring_before", "creating_index", "measuring_after", "comparing", "teardown"):
            on_progress(stage)
        return make_result(improvement=90.0)


@pytest.fixture
def mongo():
    return MongoStub()


@pytest.fixture
def make_client(settings, bus, stub, mongo):
    def build():
        deps = stub.deps(
            bus, mongo_connect=mongo.connect, mongo_explain=mongo.explain, mongo_collect_schema=mongo.collect_schema,
            mongo_target_uri="mongodb://stub/qd_sample",
        )
        bench = BenchmarkDeps(bench_dsn="pg-stub", bus=bus, mongo_bench_uri="mongodb://bench-stub/", mongo_run=mongo.runner)
        return TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps, benchmark_deps=bench))

    return build


def analyse(client, spec=None, params=None, **extra):
    q = spec or SPEC
    pid = client.get("/api/projects").json()[0]["id"]
    body = {"sql": q.sql, "params": list(q.params if params is None else params), "execute": False, "project_id": pid, "engine": "mongodb", **extra}
    return client.post("/api/analyses", json=body)


def test_a_mongodb_analysis_completes_with_findings_candidates_and_a_mongodb_plan(make_client, mongo):
    with make_client() as client:
        r = analyse(client)
        assert r.status_code == 202
        body = client.get(f"/api/analyses/{r.json()['id']}").json()
    assert body["status"] == "completed" and body["engine"] == "mongodb"
    assert body["plan"]["engine"] == "mongodb" and body["plan"]["root"]["node_type"] in ("COLLSCAN", "FETCH")
    assert "MONGO_COLLSCAN" in [f["rule_id"] for f in body["findings"]]
    ideas = [c for c in body["candidates"] if not c["dropped"]]
    assert [c["ddl"] for c in ideas] == ['db.orders.createIndex({"customer_id": 1})']
    assert {c["validation"] for c in ideas} == {"suggested"} and ideas[0]["planner_score"] is None and ideas[0]["prediction"] is None
    assert "no hypothetical indexes" in " ".join(ideas[0]["rationale"]).lower()
    assert mongo.connected and set(mongo.connected) == {"mongodb://stub/qd_sample"}
    assert body["sql"].startswith("{") and "customer_id" in body["sql"]  # the canonical JSON spec is what is stored


def test_the_execute_flag_selects_execution_statistics(make_client, mongo):
    with make_client() as client:
        analyse(client, execute=True)
    assert mongo.explain_calls[0]["execute"] is True and mongo.explain_calls[0]["collection"] == "orders"


def test_an_aggregate_analysis_works_too(make_client, mongo):
    mongo.raw = raw_plan("mongo_unbounded_group.stats")
    with make_client() as client:
        body = client.get(f"/api/analyses/{analyse(client, BY_ID['mongo_unbounded_group']).json()['id']}").json()
    assert body["status"] == "completed" and "MONGO_UNBOUNDED_AGG" in [f["rule_id"] for f in body["findings"]]


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ('{"find": "orders", "filter": {"$where": "sleep(5000)"}}', "denied_operator"),
        ('{"aggregate": "orders", "pipeline": [{"$lookup": {"from": "customers", "pipeline": [{"$out": "stolen"}], "as": "c"}}]}', "denied_stage"),
        ('{"aggregate": "orders", "pipeline": [{"$merge": {"into": "x"}}]}', "denied_stage"),
        ('{"find": "orders", "filter": ' + '{"$and": [' * 15 + '{"a": 1}' + "]}" * 15 + "}", "too_deep"),
        ('{"find": "orders", "filter": {"a": "' + "x" * 60_000 + '"}}', "too_large"),
        ("{not json", "invalid_json"),
        ('{"mapReduce": "orders"}', "unknown_key"),
        ('{"find": "orders", "filter": {"a": {"$param": 1}}}', "param_mismatch"),
    ],
    ids=["where", "nested_out", "merge", "too_deep", "too_large", "bad_json", "map_reduce", "param_mismatch"],
)
def test_hostile_mongodb_specs_are_refused_with_a_code_before_anything_runs(make_client, mongo, spec, code):
    with make_client() as client:
        pid = client.get("/api/projects").json()[0]["id"]
        r = client.post("/api/analyses", json={"sql": spec, "params": [], "execute": True, "project_id": pid, "engine": "mongodb"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == code, r.text
    assert mongo.explain_calls == [] and mongo.connected == []


def test_an_unknown_engine_is_refused_and_custom_connections_are_postgres_only(make_client):
    with make_client() as client:
        pid = client.get("/api/projects").json()[0]["id"]
        assert client.post("/api/analyses", json={"sql": "x", "params": [], "project_id": pid, "engine": "oracle"}).status_code == 422
        r = client.post("/api/analyses", json={"sql": SPEC.sql, "params": SPEC.params, "project_id": pid, "engine": "mongodb", "connection_id": 1})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "not_supported"


def test_postgresql_analyses_are_unaffected_and_history_shows_each_engine(make_client, mongo):
    with make_client() as client:
        pid = client.get("/api/projects").json()[0]["id"]
        analyse(client)
        pg = client.post("/api/analyses", json={"sql": "SELECT id FROM orders WHERE customer_id = $1", "params": [4], "project_id": pid}).json()
        listed = client.get(f"/api/projects/{pid}/analyses").json()
        assert {a["engine"] for a in listed} == {"postgres", "mongodb"}
        assert client.get(f"/api/analyses/{pg['id']}").json()["engine"] == "postgres"


def test_the_sample_catalog_is_served_per_engine(make_client):
    with make_client() as client:
        default = client.get("/api/sample-queries").json()
        mongo = client.get("/api/sample-queries?engine=mongodb").json()
        assert {q["engine"] for q in default} == {"postgres"} and len(default) == 8
        assert len(mongo) == 8 and {q["engine"] for q in mongo} == {"mongodb"} and all(q["id"].startswith("mongo_") for q in mongo)
        assert json.loads(mongo[0]["sql"])  # the spec is JSON text
        assert client.get("/api/sample-queries?engine=oracle").status_code == 422


def test_approving_a_benchmark_routes_to_the_mongodb_runner_with_the_candidate_structure(make_client, mongo):
    with make_client() as client:
        analysis = client.get(f"/api/analyses/{analyse(client).json()['id']}").json()
        idea = next(c for c in analysis["candidates"] if not c["dropped"])
        r = client.post(f"/api/analyses/{analysis['id']}/candidates/{idea['id']}/benchmark")
        assert r.status_code == 202
        run = client.get(f"/api/benchmarks/{r.json()['id']}").json()
        refreshed = client.get(f"/api/analyses/{analysis['id']}").json()
    assert run["status"] == "completed" and run["result"]["improvement_pct"] == 90.0
    assert mongo.runner_calls == [{"uri": "mongodb://bench-stub/", "collections": ["orders"], "table": "orders", "columns": [("customer_id", False)]}]
    assert next(c for c in refreshed["candidates"] if c["id"] == idea["id"])["validation"] == "benchmark_validated"


def test_cleaning_up_mongodb_sandboxes_never_raises():
    from app.workers.janitor import cleanup_stale_mongo_sandboxes

    assert cleanup_stale_mongo_sandboxes("") == 0
    assert cleanup_stale_mongo_sandboxes("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200") == 0  # nothing listens there
