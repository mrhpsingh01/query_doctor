"""Fixes from the final whole-branch review: credentials never echoed by validation errors, and cancelling an analysis of a
custom connection never signals the sample database's backends."""

from fastapi.testclient import TestClient
from test_analyses_api import SQL, project_id

from app.main import create_app
from app.models import Analysis, QuerySnapshot

PASSWORD = "S3cretDummyPw-12345"


def test_a_validation_error_never_echoes_the_submitted_values(client):
    r = client.post(
        "/api/projects/1/connections",
        json={"name": "x", "host": "db.example.com", "port": 5432, "username": "reader", "password": PASSWORD},  # no database
    )
    assert r.status_code == 422
    assert PASSWORD not in r.text and "reader" not in r.text
    errors = r.json()["detail"]
    assert errors and all(set(e) >= {"loc", "msg", "type"} and "input" not in e for e in errors)
    assert any(e["loc"][-1] == "database" for e in errors)  # what to fix is still said


def test_a_validation_error_on_an_analysis_does_not_echo_the_query_either(client):
    secret = "SELECT * FROM users WHERE ssn = '078-05-1120'"
    r = client.post("/api/analyses", json={"sql": secret, "params": "not-a-list", "project_id": project_id(client)})
    assert r.status_code == 422 and "078-05-1120" not in r.text


def test_cancelling_an_analysis_of_a_custom_connection_does_not_signal_the_sample_target(settings, bus, stub):
    cancelled: list[tuple] = []
    deps = stub.deps(bus, cancel_backend=lambda dsn, pid: cancelled.append((dsn, pid)))
    with TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps)) as client:
        session = client.app.state.sessionmaker()
        snapshot = QuerySnapshot(project_id=project_id(client), sql=SQL, normalized_sql=SQL, fingerprint="f", params=[1])
        custom = Analysis(project_id=project_id(client), snapshot=snapshot, status="running", backend_pid=777, connection_id=5)
        sample = Analysis(project_id=project_id(client), snapshot=snapshot, status="running", backend_pid=888)
        session.add_all([custom, sample])
        session.commit()
        for analysis in (custom, sample):
            assert client.post(f"/api/analyses/{analysis.id}/cancel").status_code == 200
        assert cancelled == [("stub", 888)]  # only the pid that belongs to the sample database
        assert client.get(f"/api/analyses/{custom.id}").json()["status"] == "cancelled"
