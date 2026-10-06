import json

import pytest
from querydoctor.db import ExplainFailed
from sqlalchemy import select

from app.models import Analysis, AuditEvent, QuerySnapshot

SQL = "SELECT id, total_amount FROM orders WHERE customer_id = $1"
STAGES = ["validating_query", "collecting_schema", "generating_plan", "diagnosing", "testing_candidates", "completed"]


def project_id(client) -> int:
    return client.get("/api/projects").json()[0]["id"]


def submit(client, **overrides):
    body = {"sql": SQL, "params": [4242], "execute": False, "project_id": project_id(client), **overrides}
    return client.post("/api/analyses", json=body)


def sse_events(client, analysis_id):
    events = []
    with client.stream("GET", f"/api/analyses/{analysis_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_submit_runs_and_returns_the_full_analysis(client):
    response = submit(client)
    assert response.status_code == 202
    analysis_id = response.json()["id"]
    body = client.get(f"/api/analyses/{analysis_id}").json()
    assert body["status"] == "completed"
    assert body["plan"]["root"]["node_type"] == "Sort"
    assert body["plan"]["root"]["children"][0]["relation_name"] == "orders"
    assert [f["rule_id"] for f in body["findings"]] == ["PG_SEQ_SCAN_FILTER_WASTE"]
    assert body["findings"][0]["severity"] == "critical"
    assert body["candidates"] == []
    assert body["params"] == [4242] and body["fingerprint"]


def test_progress_events_are_streamed_in_order_and_late_subscribers_get_history(client):
    analysis_id = submit(client).json()["id"]
    assert [e["type"] for e in sse_events(client, analysis_id)] == STAGES
    assert [e["type"] for e in sse_events(client, analysis_id)] == STAGES  # second, late subscriber


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DROP TABLE orders", "not_select"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT pg_sleep(1)", "denied_function"),
        ("EXPLAIN SELECT 1", "explain_not_allowed"),
        ("SELECT '" + "a" * 20_001 + "'", "too_long"),
    ],
)
def test_invalid_sql_is_a_422_with_a_code(client, sql, code):
    response = submit(client, sql=sql, params=[])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert client.get(f"/api/projects/{project_id(client)}/analyses").json() == []


def test_param_count_mismatch_is_422(client):
    for params in ([], [1, 2]):
        response = submit(client, params=params)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "param_mismatch"


def test_non_scalar_params_are_422_not_500(client):
    response = submit(client, params=[{"a": 1}])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "param_mismatch"


def test_unknown_project_is_404(client):
    assert submit(client, project_id=9999).status_code == 404


def test_execute_true_is_audited_and_false_is_not(client, stub):
    plain = submit(client).json()["id"]
    executed = submit(client, execute=True).json()["id"]
    assert True in [c["execute"] for c in stub.explain_calls]  # (candidates add a planner-only EXPLAIN afterwards)
    session = client.app.state.sessionmaker()
    types = lambda aid: [e.event_type for e in session.scalars(select(AuditEvent).where(AuditEvent.analysis_id == aid))]
    assert "execute_explain_requested" not in types(plain)
    assert "execute_explain_requested" in types(executed)
    assert "analysis_started" in types(plain)


def test_target_failure_ends_failed_with_message_and_never_hangs(client, stub):
    stub.explain_error = ExplainFailed('relation "nope" does not exist')
    analysis_id = submit(client).json()["id"]
    body = client.get(f"/api/analyses/{analysis_id}").json()
    assert body["status"] == "failed" and "nope" in body["error"]
    assert sse_events(client, analysis_id)[-1]["type"] == "failed"


def insert_analysis(client, status: str) -> int:
    session = client.app.state.sessionmaker()
    snapshot = QuerySnapshot(project_id=project_id(client), sql=SQL, normalized_sql=SQL, fingerprint="f", params=[1])
    analysis = Analysis(project_id=project_id(client), snapshot=snapshot, status=status)
    session.add(analysis)
    session.commit()
    return analysis.id


@pytest.mark.parametrize("status", ["queued", "running"])
def test_cancel_queued_or_running_analysis(client, status):
    analysis_id = insert_analysis(client, status)
    response = client.post(f"/api/analyses/{analysis_id}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    session = client.app.state.sessionmaker()
    audited = [e.event_type for e in session.scalars(select(AuditEvent).where(AuditEvent.analysis_id == analysis_id))]
    assert "job_cancelled" in audited
    assert [e["type"] for e in sse_events(client, analysis_id)] == ["cancelled"]
    assert client.get(f"/api/analyses/{analysis_id}").json()["status"] == "cancelled"


def test_cancel_finished_is_conflict_and_unknown_is_404(client):
    done = submit(client).json()["id"]
    assert client.post(f"/api/analyses/{done}/cancel").status_code == 409
    assert client.post("/api/analyses/9999/cancel").status_code == 404


def test_project_history_is_newest_first(client):
    first = submit(client).json()["id"]
    second = submit(client).json()["id"]
    listing = client.get(f"/api/projects/{project_id(client)}/analyses").json()
    assert [a["id"] for a in listing] == [second, first]
    assert listing[0]["status"] == "completed" and listing[0]["fingerprint"] and listing[0]["finding_count"] == 1


def test_get_unknown_analysis_is_404(client):
    assert client.get("/api/analyses/9999").status_code == 404


def test_sample_queries_endpoint_returns_the_catalog(client):
    queries = client.get("/api/sample-queries").json()
    assert len(queries) == 8
    assert queries[0]["id"] == "recent_orders_by_customer" and queries[0]["params"]


def test_connection_test_rejects_hosts_outside_the_allowlist(client):
    body = {"host": "169.254.169.254", "port": 5432, "database": "d", "username": "u", "password": "p"}
    assert client.post("/api/connections/test", json=body).status_code == 400
    assert client.post("/api/connections/test", json={**body, "host": "evil.example.com"}).status_code == 400
    assert client.post("/api/connections/test", json={**body, "host": "localhost", "port": 22}).status_code == 400


def test_connection_test_allows_sample_hosts(client):
    client.app.state.connection_tester = lambda **kw: (True, "ok")
    body = {"host": "target-postgres", "port": 5432, "database": "querydoctor_sample", "username": "qd_readonly", "password": "x"}
    response = client.post("/api/connections/test", json=body)
    assert response.status_code == 200 and response.json() == {"ok": True, "message": "ok"}
    assert "password" not in response.text


def test_analysis_and_history_expose_candidates_with_their_evidence_level(settings, bus, stub):
    from fastapi.testclient import TestClient
    from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

    from app.main import create_app

    deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95)}))
    with TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps)) as c:
        pid = c.get("/api/projects").json()[0]["id"]
        body = {"sql": HEADLINE_SQL, "params": [4242, "2022-01-01"], "execute": False, "project_id": pid}
        analysis_id = c.post("/api/analyses", json=body).json()["id"]
        detail = c.get(f"/api/analyses/{analysis_id}").json()
        validated = [x for x in detail["candidates"] if x["validation"] == "planner_validated"]
        assert validated and validated[0]["rank"] == 1 and validated[0]["planner_score"] == 0.95
        assert validated[0]["comparison"]["removed_operators"] == ["Seq Scan"]
        assert all(x["drop_reason"] for x in detail["candidates"] if x["dropped"])
        history = c.get(f"/api/projects/{pid}/analyses").json()
        assert history[0]["best_validation"] == "planner_validated"
