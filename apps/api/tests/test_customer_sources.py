"""The customer boundary: identities, ownership, source routing and bounded workload samples."""

import json
import socket
from types import SimpleNamespace

from fastapi.testclient import TestClient
from querydoctor.lab.mongo_catalog import MONGO_CATALOG

from app.config import Settings
from app.main import create_app
from app.services import connections as connection_service

ALICE = {"Authorization": "Bearer " + "a" * 40}
BOB = {"Authorization": "Bearer " + "b" * 40}
SPEC = next(q for q in MONGO_CATALOG if q.id == "mongo_collscan_by_customer")


def resolver(mapping):
    def resolve(host, port, *args, **kwargs):
        addresses = mapping[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in (addresses if isinstance(addresses, list) else [addresses])]
    return resolve


def new_project(client):
    return client.post("/api/projects", json={"name": "Private workload"}, headers=ALICE).json()["id"]


def test_authenticated_projects_hide_connections_analyses_and_snapshots(settings, bus, stub):
    mapping = {"db.example.com": "93.184.216.34"}
    configured = settings.model_copy(update={"auth_tokens": {"alice": "a" * 40, "bob": "b" * 40},
                                             "allow_custom_connections": True})
    app = create_app(configured, event_bus=bus, pipeline_deps=stub.deps(bus), connection_resolver=resolver(mapping))
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        project = client.post("/api/projects", json={"name": "Alice's workload"}, headers=ALICE).json()["id"]
        assert all(p["id"] != project for p in client.get("/api/projects", headers=BOB).json())
        assert client.get(f"/api/projects/{project}/connections", headers=BOB).status_code == 404
        assert client.post("/api/analyses", json={"project_id": project, "sql": "SELECT 1"}, headers=BOB).status_code == 404

        connection = client.post(f"/api/projects/{project}/connections", headers=ALICE, json={
            "name": "source", "host": "db.example.com", "port": 5432, "database": "shop",
            "username": "reader", "password": "sensitive-secret",
        }).json()
        assert connection["engine"] == "postgres" and "sensitive-secret" not in json.dumps(connection)
        assert client.delete(f"/api/connections/{connection['id']}", headers=BOB).status_code == 404

        analysis = client.post("/api/analyses", headers=ALICE, json={
            "project_id": project, "sql": "SELECT id FROM orders WHERE customer_id = $1", "params": [4242],
        }).json()["id"]
        assert client.get(f"/api/analyses/{analysis}", headers=BOB).status_code == 404
        assert client.get(f"/api/analyses/{analysis}/report", headers=BOB).status_code == 404
        report = client.get(f"/api/analyses/{analysis}/report", headers=ALICE).text
        assert "4242" not in report and "sensitive-secret" not in report

        assert client.post(f"/api/projects/{project}/members", headers=BOB, json={"subject": "bob"}).status_code == 404
        assert client.post(f"/api/projects/{project}/members", headers=ALICE, json={"subject": "bob"}).status_code == 201
        assert client.get(f"/api/analyses/{analysis}", headers=BOB).status_code == 200


def test_custom_mongodb_analysis_uses_selected_connection_and_rechecks_dns(settings, bus, stub):
    from test_mongo_api import MongoStub

    mongo = MongoStub()
    mapping = {"db.example.com": "93.184.216.34"}
    configured = settings.model_copy(update={"allow_custom_connections": True,
                                             "auth_tokens": {"alice": "a" * 40}})
    deps = stub.deps(bus, mongo_connect=mongo.connect, mongo_explain=mongo.explain,
                     mongo_collect_schema=mongo.collect_schema, mongo_target_uri="mongodb://demo/qd_sample")
    deps.connection_mongo = lambda session, analysis: mongo.connect("selected-customer-source")
    app = create_app(configured, event_bus=bus, pipeline_deps=deps, connection_resolver=resolver(mapping))
    with TestClient(app) as client:
        project = new_project(client)
        connection = client.post(f"/api/projects/{project}/connections", headers=ALICE, json={
            "name": "Mongo source", "engine": "mongodb", "host": "db.example.com", "port": 27017,
            "database": "shop", "username": "reader", "password": "top-secret", "auth_database": "admin",
        })
        assert connection.status_code == 201 and connection.json()["engine"] == "mongodb"
        cid = connection.json()["id"]
        response = client.post("/api/analyses", headers=ALICE, json={
            "project_id": project, "engine": "mongodb", "connection_id": cid,
            "sql": SPEC.sql, "params": list(SPEC.params),
        })
        assert response.status_code == 202 and mongo.connected == ["selected-customer-source"] * 2
        result = client.get(f"/api/analyses/{response.json()['id']}", headers=ALICE).json()
        assert result["status"] == "completed" and "top-secret" not in json.dumps(result)

        mapping["db.example.com"] = "127.0.0.1"
        rebound = client.post("/api/analyses", headers=ALICE, json={
            "project_id": project, "engine": "mongodb", "connection_id": cid,
            "sql": SPEC.sql, "params": list(SPEC.params),
        })
        assert rebound.status_code == 400 and mongo.connected == ["selected-customer-source"] * 2


def test_imported_profiler_records_window_coverage_and_observed_sample_p95(settings, bus, stub):
    mapping = {"db.example.com": "93.184.216.34"}
    configured = settings.model_copy(update={"allow_custom_connections": True,
                                             "auth_tokens": {"alice": "a" * 40}})
    app = create_app(configured, event_bus=bus, pipeline_deps=stub.deps(bus), connection_resolver=resolver(mapping))
    with TestClient(app) as client:
        project = new_project(client)
        cid = client.post(f"/api/projects/{project}/connections", headers=ALICE, json={
            "name": "Mongo source", "engine": "mongodb", "host": "db.example.com", "port": 27017,
            "database": "shop", "username": "reader", "password": "top-secret",
        }).json()["id"]
        entries = [{"op": "query", "ns": "shop.orders", "command": {"find": "orders", "filter": {"customer_id": 4242}},
                    "millis": duration, "docsExamined": 100} for duration in (12, 30)]
        response = client.post(f"/api/projects/{project}/workload/import-profiler", headers=ALICE, json={
            "connection_id": cid, "capture_start": "2026-10-13T10:00:00Z", "capture_end": "2026-10-13T10:05:00Z",
            "coverage": "sampled", "engine_version": "7.0.0", "entries": entries,
        })
        assert response.status_code == 201, response.text
        snapshot = response.json()
        assert snapshot["source_connection_id"] == cid and snapshot["capture_method"] == "imported_profiler"
        assert snapshot["coverage"] == "sampled" and snapshot["capture_start"] and snapshot["capture_end"]
        dashboard = client.get(f"/api/projects/{project}/workload?engine=mongodb", headers=ALICE).json()
        assert dashboard["queries"][0]["p95_observed_ms"] == 30
        assert "4242" not in json.dumps(dashboard)

        other = client.post(f"/api/projects/{project}/connections", headers=ALICE, json={
            "name": "Second Mongo source", "engine": "mongodb", "host": "db.example.com", "port": 27017,
            "database": "other", "username": "reader", "password": "another-secret",
        }).json()["id"]
        second = client.post(f"/api/projects/{project}/workload/import-profiler", headers=ALICE, json={
            "connection_id": other, "capture_start": "2026-10-13T10:00:00Z", "capture_end": "2026-10-13T10:05:00Z",
            "coverage": "sampled", "engine_version": "7.0.0", "entries": entries,
        }).json()["id"]
        assert client.get(f"/api/projects/{project}/workload?engine=mongodb&snapshot={second}&compare={snapshot['id']}", headers=ALICE).status_code == 404
        assert client.get(f"/api/projects/{project}/workload?engine=mongodb&connection_id={cid}", headers=ALICE).json()["snapshot"]["id"] == snapshot["id"]


def test_mongodb_worker_connects_to_checked_ip_and_rejects_rebound_name(settings, bus, stub, monkeypatch):
    mapping = {"db.example.com": "93.184.216.34"}
    configured = settings.model_copy(update={"allow_custom_connections": True,
                                             "auth_tokens": {"alice": "a" * 40}})
    app = create_app(configured, event_bus=bus, pipeline_deps=stub.deps(bus), connection_resolver=resolver(mapping))
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __getitem__(self, database):
            return database

        def close(self):
            pass

    monkeypatch.setattr(connection_service, "MongoClient", FakeClient)
    with TestClient(app) as client:
        project = new_project(client)
        cid = client.post(f"/api/projects/{project}/connections", headers=ALICE, json={
            "name": "Mongo source", "engine": "mongodb", "host": "db.example.com", "port": 27017,
            "database": "shop", "username": "reader", "password": "top-secret",
        }).json()["id"]
        with app.state.sessionmaker() as session:
            with connection_service.open_mongo(configured, resolver(mapping), session,
                                                SimpleNamespace(connection_id=cid, project_id=project)) as db:
                assert db == "shop"
            assert calls[0]["host"] == "93.184.216.34" and calls[0]["directConnection"] is True
            mapping["db.example.com"] = "127.0.0.1"
            from fastapi import HTTPException
            import pytest

            with pytest.raises(HTTPException):
                with connection_service.open_mongo(configured, resolver(mapping), session,
                                                    SimpleNamespace(connection_id=cid, project_id=project)):
                    pass
            assert len(calls) == 1
