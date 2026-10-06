"""End-to-end against the real stack: `docker compose -f infrastructure/compose.yaml up -d --build`."""

import httpx
import pytest
from stack import BASE, analyse, plan_nodes, project_id

pytestmark = [pytest.mark.integration, pytest.mark.stack]


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=30) as client:
        yield client


@pytest.fixture(scope="module")
def sample_query(http):
    return http.get("/api/sample-queries").json()[0]


def test_plan_only_analysis_of_a_catalog_query(http, sample_query):
    body = analyse(http, sample_query["sql"], sample_query["params"])
    assert body["status"] == "completed", body["error"]
    assert body["execute"] is False and body["plan"]["executed"] is False
    assert any(n["node_type"] == "Seq Scan" and n["relation_name"] == "orders" for n in plan_nodes(body["plan"]["root"]))
    assert "PG_SEQ_SCAN_FILTER_WASTE" in [f["rule_id"] for f in body["findings"]]


def test_execute_analysis_returns_actuals(http, sample_query):
    body = analyse(http, sample_query["sql"], sample_query["params"], execute=True)
    assert body["status"] == "completed", body["error"]
    assert body["plan"]["executed"] is True and body["execution_time_ms"] > 0
    assert any(n["actual_rows"] is not None for n in plan_nodes(body["plan"]["root"]))


def test_destructive_sql_is_rejected(http):
    response = http.post("/api/analyses", json={"sql": "DROP TABLE orders", "params": [], "project_id": project_id(http)})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "not_select"


def test_missing_table_ends_failed_with_a_message(http):
    body = analyse(http, "SELECT * FROM no_such_table", [])
    assert body["status"] == "failed" and "no_such_table" in body["error"]


def test_sse_replays_history_for_a_finished_analysis(http, sample_query):
    body = analyse(http, sample_query["sql"], sample_query["params"])
    types = []
    with http.stream("GET", f"/api/analyses/{body['id']}/events") as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                types.append(__import__("json").loads(line[6:])["type"])
    assert types[0] == "validating_query" and types[-1] == "completed"


def test_a_runaway_query_hits_the_statement_timeout_and_fails_cleanly(http):
    """A cartesian product of a 500k-row table would run for ages; the 10 s session limit must stop it, the analysis must
    end `failed` with a readable reason (never hang), and the stack must keep serving afterwards."""
    body = analyse(http, "SELECT count(*) FROM events a, events b", [], execute=True, timeout=60)
    assert body["status"] == "failed"
    assert "timed out" in body["error"]
    assert http.get("/api/health").json() == {"status": "ok"}
    follow_up = analyse(http, "SELECT count(*) FROM customers", [])
    assert follow_up["status"] == "completed"
