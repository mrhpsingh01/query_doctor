import json

import pytest
from fastapi.testclient import TestClient
from querydoctor.explain import ExplainerUnavailable, explain_template
from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

from app.main import create_app
from app.services.explanations import ExplainDeps

KINDS = ["plan", "findings", "tradeoffs", "next_experiment", "pr_comment"]


class StubOllama:
    """An enabled model client: replies with a canned draft, or fails."""

    enabled = True
    model = "qd-stub"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def draft(self, pkg):
        self.calls += 1
        if self.error:
            raise ExplainerUnavailable(self.error)
        return self.reply(pkg) if callable(self.reply) else self.reply


@pytest.fixture
def make_client(settings, bus, stub):
    def build(ollama=None):
        deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95), "DESC)": comparison(score=0.7)}))
        return TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps, explain_deps=ExplainDeps(ollama=ollama)))

    return build


def analyse(client) -> int:
    pid = client.get("/api/projects").json()[0]["id"]
    body = {"sql": HEADLINE_SQL, "params": [4242, "alice@example.com"], "execute": False, "project_id": pid}
    return client.post("/api/analyses", json=body).json()["id"]


def test_asking_for_an_explanation_completes_and_returns_text_sources_and_facts(make_client):
    with make_client() as client:
        aid = analyse(client)
        r = client.post(f"/api/analyses/{aid}/explanations", json={"kind": "plan"})
        assert r.status_code == 202 and r.json()["status"] in ("queued", "completed")
        body = client.get(f"/api/explanations/{r.json()['id']}").json()
    assert body["status"] == "completed" and body["kind"] == "plan" and body["source"] == "template"
    assert body["text"] and body["fallback_reason"] is None and body["reviewed"] is None and body["dropped_sentences"] == []
    assert body["facts"] and body["facts"][0]["id"] == "F1"
    assert body["sentences"] and all(s["fact_ids"] for s in body["sentences"])
    assert all(i in {f["id"] for f in body["facts"]} for s in body["sentences"] for i in s["fact_ids"])


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_available(make_client, kind):
    with make_client() as client:
        aid = analyse(client)
        eid = client.post(f"/api/analyses/{aid}/explanations", json={"kind": kind}).json()["id"]
        body = client.get(f"/api/explanations/{eid}").json()
    assert body["status"] == "completed" and body["text"]


def test_invalid_kinds_and_unknown_analyses_are_rejected(make_client):
    with make_client() as client:
        aid = analyse(client)
        assert client.post(f"/api/analyses/{aid}/explanations", json={"kind": "poem"}).status_code == 422
        assert client.post(f"/api/analyses/{aid}/explanations", json={}).status_code == 422
        assert client.post("/api/analyses/99999/explanations", json={"kind": "plan"}).status_code == 404
        assert client.get("/api/explanations/99999").status_code == 404


def test_an_unfinished_analysis_cannot_be_explained_yet(settings, bus, stub):
    app = create_app(settings, event_bus=bus, pipeline_deps=stub.deps(bus), enqueue=lambda _id: None)
    with TestClient(app) as client:
        aid = analyse(client)
        assert client.get(f"/api/analyses/{aid}").json()["status"] == "queued"
        assert client.post(f"/api/analyses/{aid}/explanations", json={"kind": "plan"}).status_code == 409


def test_a_person_can_accept_or_reject_an_explanation(make_client):
    with make_client() as client:
        eid = client.post(f"/api/analyses/{analyse(client)}/explanations", json={"kind": "plan"}).json()["id"]
        assert client.post(f"/api/explanations/{eid}/review", json={"accepted": True}).json() == {"id": eid, "reviewed": True}
        assert client.get(f"/api/explanations/{eid}").json()["reviewed"] is True
        client.post(f"/api/explanations/{eid}/review", json={"accepted": False})
        assert client.get(f"/api/explanations/{eid}").json()["reviewed"] is False
        assert client.post(f"/api/explanations/{eid}/review", json={"accepted": "yes"}).status_code == 422
        assert client.post("/api/explanations/99999/review", json={"accepted": True}).status_code == 404


def test_a_failing_language_model_gives_the_template_with_the_reason(make_client):
    ollama = StubOllama(error="The language model server could not be reached.")
    with make_client(ollama) as client:
        eid = client.post(f"/api/analyses/{analyse(client)}/explanations", json={"kind": "tradeoffs"}).json()["id"]
        body = client.get(f"/api/explanations/{eid}").json()
    assert ollama.calls == 1
    assert body["status"] == "completed" and body["source"] == "template" and "could not be reached" in body["fallback_reason"]


def test_a_validated_language_model_draft_is_stored_with_its_source_and_dropped_sentences(make_client):
    def reply(pkg):
        good = explain_template(pkg).sentences[0]
        return f"{good.text} [{', '.join(good.fact_ids)}] It is 99999% faster. [F1] Another fine remark. [F1]"

    with make_client(StubOllama(reply=reply)) as client:
        eid = client.post(f"/api/analyses/{analyse(client)}/explanations", json={"kind": "plan"}).json()["id"]
        body = client.get(f"/api/explanations/{eid}").json()
    assert body["source"] == "ollama:qd-stub" and body["status"] == "completed" and body["fallback_reason"] is None
    assert body["dropped_sentences"] == ["It is 99999% faster. [F1]"] and "99999" not in body["text"]


def test_stored_explanations_never_contain_query_parameters(make_client):
    with make_client() as client:
        aid = analyse(client)
        blobs = []
        for kind in KINDS:
            eid = client.post(f"/api/analyses/{aid}/explanations", json={"kind": kind}).json()["id"]
            blobs.append(json.dumps(client.get(f"/api/explanations/{eid}").json()))
    assert not any("alice@example.com" in b or "4242" in b for b in blobs)


def test_an_explanation_that_crashes_ends_failed_not_stuck(make_client, monkeypatch):
    import app.services.explanations as svc

    monkeypatch.setattr(svc, "explain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with make_client() as client:
        eid = client.post(f"/api/analyses/{analyse(client)}/explanations", json={"kind": "plan"}).json()["id"]
        body = client.get(f"/api/explanations/{eid}").json()
    assert body["status"] == "failed" and body["error"] and "boom" not in body["error"]
