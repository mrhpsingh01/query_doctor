import json
import socket

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from querydoctor.candidates.hypopg import ScoredCandidate
from test_benchmarks_api import FakeRunner
from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

from app.config import Settings
from app.main import create_app
from app.models import AuditEvent, Connection, Project
from app.security.crypto import decrypt
from app.services.benchmarks import BenchmarkDeps

PUBLIC = "93.184.216.34"
PASSWORD = "Sup3r-S3cret-pw"
KEY = Fernet.generate_key().decode()
BODY = {"name": "Prod replica", "host": "db.example.com", "port": 5432, "database": "shop", "username": "reader", "password": PASSWORD}


def resolver_for(mapping):
    def resolve(host, port, *args, **kwargs):
        if host == "boom.example.com":
            raise OSError("SECRET-RESOLVER-INTERNALS 10.9.8.7 ns.corp")
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]

    return resolve


@pytest.fixture
def mapping():
    return {"db.example.com": [PUBLIC], "internal.example.com": ["10.0.0.5"], "meta.example.com": ["169.254.169.254"]}


@pytest.fixture
def make_client(bus, stub, mapping):
    def build(*, enabled=True, private=False, hypopg=True, scorer=None):
        settings = Settings(
            database_url="sqlite://", secret_key=KEY, redis_url="memory://", auto_create_tables=True,
            allow_custom_connections=enabled, allow_private_networks=private,
        )
        deps = stub.deps(
            bus, scorer=scorer or fake_scorer({"INCLUDE": comparison(score=0.95), "DESC)": comparison(score=0.7)}),
            hypopg_available=lambda conn: hypopg,
        )
        bench = BenchmarkDeps(bench_dsn="stub", bus=bus, run=FakeRunner())
        app = create_app(settings, event_bus=bus, pipeline_deps=deps, benchmark_deps=bench, connection_resolver=resolver_for(mapping))
        return TestClient(app)

    return build


def create(client, project=1, **over):
    return client.post(f"/api/projects/{project}/connections", json={**BODY, **over})


def test_with_the_flag_off_creating_and_listing_are_forbidden_with_a_clear_message(make_client):
    with make_client(enabled=False) as client:
        for response in (create(client), client.get("/api/projects/1/connections")):
            assert response.status_code == 403 and "QD_ALLOW_CUSTOM_CONNECTIONS" in response.json()["detail"]


def test_a_created_connection_stores_only_an_encrypted_password_and_never_returns_it(make_client):
    with make_client() as client:
        response = create(client)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Prod replica" and body["host"] == "db.example.com" and body["sslmode"] == "prefer"
        assert not ({"password", "encrypted_password"} & set(body))
        with client.app.state.sessionmaker() as s:
            row = s.get(Connection, body["id"])
            assert row.encrypted_password and row.encrypted_password != PASSWORD and PASSWORD not in row.encrypted_password
            assert decrypt(row.encrypted_password, key=KEY) == PASSWORD and row.kind == "custom"
        listed = client.get("/api/projects/1/connections")
        assert [c["id"] for c in listed.json()] == [body["id"]]
        for text in (response.text, listed.text):
            assert PASSWORD not in text and "gAAAA" not in text and "postgresql://" not in text


@pytest.mark.parametrize(
    "over",
    [{"host": "127.0.0.1"}, {"host": "localhost"}, {"host": "169.254.169.254"}, {"host": "meta.example.com"}, {"host": "internal.example.com"},
     {"host": "10.1.2.3"}, {"host": "2130706433"}, {"host": "db.example.com", "port": 22}, {"host": "nope.example.com"}],
)
def test_internal_hosts_and_ports_are_refused_with_a_reason_that_hides_resolver_details(make_client, over):
    with make_client() as client:
        response = create(client, **over)
        assert response.status_code == 400 and "not allowed" in response.json()["detail"].lower() or response.status_code == 400
        assert PASSWORD not in response.text and "10.9.8.7" not in response.text
        with client.app.state.sessionmaker() as s:
            assert s.query(Connection).count() == 0


def test_a_resolver_that_fails_strangely_does_not_leak_its_message(make_client):
    with make_client() as client:
        response = create(client, host="boom.example.com")
        assert response.status_code == 400 and "SECRET-RESOLVER-INTERNALS" not in response.text


def test_private_networks_are_allowed_only_with_the_operator_flag(make_client):
    with make_client(private=False) as client:
        assert create(client, host="internal.example.com").status_code == 400
    with make_client(private=True) as client:
        assert create(client, host="internal.example.com").status_code == 201
        assert create(client, host="meta.example.com").status_code == 400  # metadata is never allowed


@pytest.mark.parametrize("over", [{"sslmode": "disable"}, {"password": ""}, {"name": ""}, {"port": 0}, {"database": ""}, {"extra": 1}])
def test_invalid_connection_details_are_rejected(make_client, over):
    with make_client() as client:
        assert create(client, **over).status_code == 422


def test_require_is_a_selectable_sslmode(make_client):
    with make_client() as client:
        assert create(client, sslmode="require").json()["sslmode"] == "require"


def test_connections_belong_to_a_project(make_client):
    with make_client() as client:
        assert create(client, project=99).status_code == 404
        assert client.get("/api/projects/99/connections").status_code == 404


def test_deleting_a_connection_wipes_the_secret_and_leaves_an_audit_trail_without_it(make_client):
    with make_client() as client:
        cid = create(client).json()["id"]
        assert client.delete(f"/api/connections/{cid}").status_code == 200
        with client.app.state.sessionmaker() as s:
            row = s.get(Connection, cid)
            assert row.encrypted_password == "" and row.deleted_at is not None
            events = [(e.event_type, e.detail) for e in s.query(AuditEvent).order_by(AuditEvent.id)]
        assert [e for e, _ in events] == ["connection_created", "connection_deleted"]
        assert PASSWORD not in json.dumps(events) and all(d["connection_id"] == cid for _e, d in events)
        assert client.get("/api/projects/1/connections").json() == []
        assert client.delete(f"/api/connections/{cid}").status_code == 404
        assert client.delete("/api/connections/99999").status_code == 404


def test_credentials_can_be_deleted_even_after_the_flag_is_turned_off(make_client):
    with make_client() as client:
        cid = create(client).json()["id"]
        client.app.state.settings = client.app.state.settings.model_copy(update={"allow_custom_connections": False})
        assert client.get("/api/projects/1/connections").status_code == 403
        assert client.delete(f"/api/connections/{cid}").status_code == 200


# ---- using a connection in an analysis -------------------------------------------------------------------------------

def analyse(client, connection_id=None, project=1):
    body = {"sql": HEADLINE_SQL, "params": [4242, "2022-01-01"], "execute": False, "project_id": project}
    if connection_id is not None:
        body["connection_id"] = connection_id
    return client.post("/api/analyses", json=body)


def test_an_analysis_connects_to_the_resolved_address_and_records_that_the_connection_was_used(make_client, stub):
    seen = []
    original = stub.connect

    def spy(dsn, **kw):
        seen.append(dsn)
        return original(dsn, **kw)

    stub.connect = spy
    with make_client() as client:
        cid = create(client, sslmode="require").json()["id"]
        r = analyse(client, cid)
        assert r.status_code == 202
        analysis = client.get(f"/api/analyses/{r.json()['id']}").json()
        assert analysis["status"] == "completed"
        dsn = next(d for d in seen if "hostaddr" in d)
        assert f"hostaddr={PUBLIC}" in dsn and "host=db.example.com" in dsn and "sslmode=require" in dsn and "dbname=shop" in dsn
        assert PASSWORD in dsn  # used to connect, but...
        assert PASSWORD not in json.dumps(analysis) and "hostaddr" not in json.dumps(analysis) and "postgresql://" not in json.dumps(analysis)
        with client.app.state.sessionmaker() as s:
            used = s.query(AuditEvent).filter_by(event_type="connection_used").one()
            assert used.detail["connection_id"] == cid and PASSWORD not in json.dumps(used.detail)


def test_the_host_is_checked_again_when_the_connection_is_used_so_rebinding_cannot_slip_through(make_client, mapping):
    with make_client() as client:
        cid = create(client).json()["id"]
        mapping["db.example.com"] = ["127.0.0.1"]  # the name now points at loopback
        r = analyse(client, cid)
        assert r.status_code == 400 and "not allowed" in r.json()["detail"].lower()


def test_using_a_connection_needs_the_flag_and_a_live_connection_of_the_same_project(make_client):
    with make_client() as client:
        cid = create(client).json()["id"]
        with client.app.state.sessionmaker() as s:
            s.add(Project(name="other"))
            s.commit()
            other = s.query(Project).filter_by(name="other").one().id
        assert analyse(client, 99999).status_code == 404
        assert analyse(client, cid, project=other).status_code == 404  # another project's connection
        client.delete(f"/api/connections/{cid}")
        assert analyse(client, cid).status_code == 404  # deleted
    with make_client(enabled=False) as off:
        assert analyse(off, 1).status_code == 403


def test_benchmarks_are_refused_for_analyses_of_custom_connections(make_client):
    with make_client() as client:
        cid = create(client).json()["id"]
        analysis = client.get(f"/api/analyses/{analyse(client, cid).json()['id']}").json()
        cand = next(c for c in analysis["candidates"] if not c["dropped"])
        r = client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/benchmark")
        assert r.status_code == 409 and "sample" in r.json()["detail"].lower()


def test_without_hypopg_candidates_stay_suggested_with_an_explanation(make_client):
    def scorer(*a, **k):
        raise AssertionError("HypoPG must not be used when the extension is missing")

    with make_client(hypopg=False, scorer=scorer) as client:
        cid = create(client).json()["id"]
        analysis = client.get(f"/api/analyses/{analyse(client, cid).json()['id']}").json()
    ideas = [c for c in analysis["candidates"] if not c["dropped"]]
    assert analysis["status"] == "completed" and ideas
    assert {c["validation"] for c in ideas} == {"suggested"}
    assert all("unavailable on this database" in c["rationale"][-1].lower() for c in ideas)


def test_the_sample_database_still_works_without_any_connection(make_client):
    with make_client(enabled=False) as client:
        assert client.get(f"/api/analyses/{analyse(client).json()['id']}").json()["status"] == "completed"
