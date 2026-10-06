from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient
from querydoctor.safety import validate_query
from querydoctor.workload import StatementRow

from app.main import create_app
from app.services.workload import WorkloadDeps

ORDERS = "SELECT id, total_amount FROM orders WHERE customer_id = $1"
USERS = "SELECT id FROM customers WHERE email = $1"


def stmt(query, calls, mean):
    return StatementRow(
        query=query, calls=calls, total_ms=calls * mean, mean_ms=mean, stddev_ms=0.0, min_ms=mean, max_ms=mean, rows=calls,
        shared_hit_blocks=1, shared_read_blocks=0, temp_blocks=0,
    )


class Target:
    """Programmable stand-in for the monitored database's statistics."""

    def __init__(self):
        self.rows: list[StatementRow] = []
        self.error: Exception | None = None
        self.demo_calls: list[str] = []

    @contextmanager
    def connect(self, dsn):
        if self.error:
            raise self.error
        yield object()

    def reader(self, conn, limit=500):
        return self.rows[:limit]

    def deps(self) -> WorkloadDeps:
        return WorkloadDeps(target_dsn="stub", connect=self.connect, reader=self.reader, demo_runner=self.demo_calls.append)


@pytest.fixture
def target() -> Target:
    return Target()


@pytest.fixture
def api(settings, bus, stub, target) -> TestClient:
    app = create_app(settings, event_bus=bus, pipeline_deps=stub.deps(bus), workload_deps=target.deps())
    with TestClient(app) as c:
        yield c


def test_import_returns_201_with_the_snapshot_counts(api, target):
    target.rows += [stmt(ORDERS, 100, 5.0), stmt(USERS, 10, 1.0), stmt("DELETE FROM orders WHERE id = $1", 3, 1.0)]
    r = api.post("/api/projects/1/workload/import")
    assert r.status_code == 201
    body = r.json()
    assert (body["query_count"], body["skipped_count"], body["engine"], body["project_id"]) == (2, 1, "postgres", 1)


def test_dashboard_has_the_ranked_queries_panels_and_shares(api, target):
    target.rows += [stmt(ORDERS, 10_000, 5.0), stmt(USERS, 1, 5000.0)]
    api.post("/api/projects/1/workload/import")
    body = api.get("/api/projects/1/workload").json()
    assert body["snapshot"]["query_count"] == 2 and body["compare_to"] is None and body["regressions"] == []
    assert [q["rank"] for q in body["queries"]] == [1, 2]
    assert abs(sum(q["share"] for q in body["queries"]) - 1.0) < 1e-9
    assert set(body["panels"]) == {
        "highest_total_time", "slowest_p95", "most_frequent", "highest_io", "most_unstable", "largest_regression",
        "missing_validation",
    }
    assert body["queries"][0]["fingerprint"] == validate_query(ORDERS).fingerprint


def test_dashboard_before_any_import_is_empty_not_an_error(api):
    body = api.get("/api/projects/1/workload").json()
    assert body["snapshot"] is None and body["queries"] == [] and body["regressions"] == []
    assert all(v == [] for v in body["panels"].values())


def test_dashboard_selects_snapshots_and_rejects_unknown_ones(api, target):
    target.rows[:] = [stmt(ORDERS, 10, 1.0)]
    first = api.post("/api/projects/1/workload/import").json()
    target.rows[:] = [stmt(ORDERS, 20, 1.0)]
    second = api.post("/api/projects/1/workload/import").json()
    assert api.get("/api/projects/1/workload").json()["snapshot"]["id"] == second["id"]
    picked = api.get(f"/api/projects/1/workload?snapshot={first['id']}&compare={second['id']}").json()
    assert picked["snapshot"]["id"] == first["id"] and picked["compare_to"]["id"] == second["id"]
    assert api.get("/api/projects/1/workload?snapshot=9999").status_code == 404
    assert api.get("/api/projects/1/workload?snapshot=abc").status_code == 422


def test_unknown_project_is_404_on_every_workload_route(api):
    assert api.post("/api/projects/99/workload/import").status_code == 404
    assert api.get("/api/projects/99/workload").status_code == 404
    assert api.get("/api/projects/99/fingerprints").status_code == 404
    assert api.get("/api/projects/99/workload/queries/abc").status_code == 404
    assert api.post("/api/projects/99/workload/demo", json={"mode": "baseline"}).status_code == 404


def test_detail_404_for_an_unknown_fingerprint_and_200_for_a_known_one(api, target):
    target.rows += [stmt(ORDERS, 100, 5.0)]
    api.post("/api/projects/1/workload/import")
    assert api.get("/api/projects/1/workload/queries/" + "0" * 16).status_code == 404
    ok = api.get(f"/api/projects/1/workload/queries/{validate_query(ORDERS).fingerprint}")
    assert ok.status_code == 200 and ok.json()["trend"][0]["calls"] == 100


def test_demo_returns_202_and_runs_the_runner_with_the_mode(api, target):
    r = api.post("/api/projects/1/workload/demo", json={"mode": "regress"})
    assert r.status_code == 202 and r.json() == {"status": "queued", "mode": "regress"}
    assert api.post("/api/projects/1/workload/demo", json={"mode": "baseline"}).status_code == 202
    assert target.demo_calls == ["regress", "baseline"]


@pytest.mark.parametrize("body", [{"mode": "explode"}, {}, {"mode": "baseline; DROP TABLE x"}])
def test_demo_rejects_an_invalid_mode(api, target, body):
    assert api.post("/api/projects/1/workload/demo", json=body).status_code == 422
    assert target.demo_calls == []


def test_fingerprints_endpoint_lists_analysed_queries(api):
    api.post("/api/analyses", json={"project_id": 1, "sql": "SELECT id FROM orders WHERE customer_id = $1", "params": [7]})
    body = api.get("/api/projects/1/fingerprints").json()
    assert len(body) == 1 and body[0]["analysis_count"] == 1 and body[0]["fingerprint"]


def test_a_failing_reader_gives_502_with_a_message_and_persists_nothing(api, target):
    target.error = psycopg.OperationalError('connection to server at "127.0.0.1" failed: password=hunter2')
    r = api.post("/api/projects/1/workload/import")
    assert r.status_code == 502
    assert "pg_stat_statements" in r.json()["detail"] and "hunter2" not in r.text
    target.error = None
    assert api.get("/api/projects/1/workload").json()["snapshot"] is None


def test_a_reader_that_fails_after_connecting_also_gives_502(api, target):
    def boom(conn, limit=500):
        raise psycopg.errors.InsufficientPrivilege("permission denied for view pg_stat_statements")

    target.reader = boom
    api.app.state.workload_deps.reader = boom
    assert api.post("/api/projects/1/workload/import").status_code == 502
    assert api.get("/api/projects/1/workload").json()["snapshot"] is None
