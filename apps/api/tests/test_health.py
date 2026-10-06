def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sample_project_is_seeded_on_startup(client):
    from app.main import ensure_sample_project

    projects = client.get("/api/projects").json()
    assert [p["name"] for p in projects] == ["Sample shop"]
    ensure_sample_project(client.app.state.sessionmaker)  # a second startup must not duplicate it
    assert len(client.get("/api/projects").json()) == 1
