"""The eight MongoDB catalog queries against the live stack: each yields its expected findings and index idea, and a real
benchmark in a throwaway database validates the ideas that can help."""

import time

import httpx
import pytest
from pymongo import MongoClient
from stack import BASE, project_id

from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo.sandbox import leftover_sandboxes

pytestmark = [pytest.mark.integration, pytest.mark.stack]
BENCH_URI = "mongodb://qd_bench:qd_bench@127.0.0.1:27018/?authSource=admin"
TERMINAL = {"completed", "failed", "cancelled"}
# the ideas a benchmark can validate: the last two catalog queries (a match after a group, an index that is already used)
# need their pipeline or filter rewritten first, so an extra index alone does not make them faster
BENCHMARKED = {"mongo_collscan_by_customer", "mongo_status_and_date_range", "mongo_blocking_sort", "mongo_range_before_equality", "mongo_lookup_without_index"}


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=60) as client:
        yield client


def wait(http, path, key="status", timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.get(path).json()
        if body[key] in TERMINAL:
            return body
        time.sleep(1)
    pytest.fail(f"{path} did not finish in {timeout}s")


def analyse(http, q):
    r = http.post("/api/analyses", json={"sql": q.sql, "params": q.params, "execute": True, "project_id": project_id(http), "engine": "mongodb"})
    assert r.status_code == 202, r.text
    return wait(http, f"/api/analyses/{r.json()['id']}")


@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
def test_each_catalog_query_has_its_findings_and_index_idea(http, query):
    body = analyse(http, query)
    assert body["status"] == "completed", body["error"]
    assert body["engine"] == "mongodb" and body["plan"]["engine"] == "mongodb"
    fired = {f["rule_id"] for f in body["findings"]}
    assert set(query.expected_rule_ids) <= fired, (sorted(fired), query.expected_rule_ids)
    ideas = [c for c in body["candidates"] if not c["dropped"]]
    if query.expected_candidate_table:
        assert any(c["table_name"] == query.expected_candidate_table and [col["name"] for col in c["columns"]] == query.expected_candidate_columns for c in ideas), [c["ddl"] for c in ideas]
    assert {c["validation"] for c in ideas} <= {"suggested"}


@pytest.mark.parametrize("query", [q for q in MONGO_CATALOG if q.id in BENCHMARKED], ids=lambda q: q.id)
def test_a_real_benchmark_validates_the_expected_index_and_leaves_nothing_behind(http, query):
    body = analyse(http, query)
    idea = next(c for c in body["candidates"] if not c["dropped"] and c["table_name"] == query.expected_candidate_table and [col["name"] for col in c["columns"]] == query.expected_candidate_columns)
    r = http.post(f"/api/analyses/{body['id']}/candidates/{idea['id']}/benchmark", json={"runs": 5})
    assert r.status_code in (200, 202), r.text
    run = wait(http, f"/api/benchmarks/{r.json()['id']}", timeout=300)
    assert run["status"] == "completed", run["error"]
    result = run["result"]
    assert result["improvement_pct"] > 20 and result["checksum_match"] is True and result["index_used"] is True, result["warnings"]
    assert result["index_size_bytes"] > 0 and result["pg_version"].startswith("MongoDB")
    refreshed = http.get(f"/api/analyses/{body['id']}").json()
    assert next(c for c in refreshed["candidates"] if c["id"] == idea["id"])["validation"] == "benchmark_validated"
    with MongoClient(BENCH_URI, serverSelectionTimeoutMS=3000) as client:
        assert leftover_sandboxes(client) == []
