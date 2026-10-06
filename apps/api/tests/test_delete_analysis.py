import math

import pytest
from fastapi.testclient import TestClient
from querydoctor.ranking.predictor import Contribution, Prediction
from test_benchmarks_api import FakeRunner
from test_connections_registry import analyse as _analyse
from test_pipeline_candidates import comparison, fake_scorer

from app.main import create_app
from app.models import (
    Analysis, AuditEvent, BenchmarkRun, Explanation, Finding, ModelOutcome, ModelPrediction, QuerySnapshot,
    RecommendationCandidate, UserFeedback,
)
from app.services.benchmarks import BenchmarkDeps


class Predictor:
    use_for_ranking = False
    meta = {"models": {"lgbm_regressor": {"holdout": {"mae_log_speedup": 0.3}}}}

    def predict_many(self, features):
        return [
            Prediction(8.0, 0.9, 4.0, 0.02, 1.0, [Contribution("c_include_cols", 1.0, 0.5, "x")], "stub-1", 0.1, 0.0, math.log(8)) for _ in features
        ]


@pytest.fixture
def client(settings, bus, stub):
    deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95), "DESC)": comparison(score=0.7)}), predictor=Predictor())
    app = create_app(settings, event_bus=bus, pipeline_deps=deps, benchmark_deps=BenchmarkDeps(bench_dsn="stub", bus=bus, run=FakeRunner()))
    with TestClient(app) as c:
        yield c


def build_everything(client) -> int:
    """An analysis with findings, candidates, predictions, a benchmark, an outcome, an explanation and feedback."""
    analysis = client.get(f"/api/analyses/{_analyse(client).json()['id']}").json()
    cand = next(c for c in analysis["candidates"] if not c["dropped"])
    client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/benchmark")
    client.post(f"/api/analyses/{analysis['id']}/explanations", json={"kind": "plan"})
    client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/feedback", json={"helpful": True}, headers={"X-QD-Session": "s"})
    return analysis["id"]


MODELS = [Finding, RecommendationCandidate, ModelPrediction, BenchmarkRun, ModelOutcome, Explanation, UserFeedback]


def counts(client) -> dict:
    with client.app.state.sessionmaker() as s:
        return {m.__name__: s.query(m).count() for m in [Analysis, QuerySnapshot, *MODELS]}


def test_deleting_an_analysis_removes_everything_stored_under_it(client):
    aid = build_everything(client)
    before = counts(client)
    assert all(before[m.__name__] > 0 for m in MODELS) and before["Analysis"] == 1 and before["QuerySnapshot"] == 1
    r = client.delete(f"/api/analyses/{aid}")
    assert r.status_code == 200 and r.json() == {"deleted": aid}
    assert all(v == 0 for v in counts(client).values()), counts(client)
    assert client.get(f"/api/analyses/{aid}").status_code == 404


def test_the_deletion_is_audited_without_the_query_or_its_parameters(client):
    aid = build_everything(client)
    client.delete(f"/api/analyses/{aid}")
    with client.app.state.sessionmaker() as s:
        events = s.query(AuditEvent).filter_by(event_type="analysis_deleted").all()
    assert len(events) == 1 and events[0].detail == {"analysis_id": aid}
    assert events[0].analysis_id is None


def test_deleting_one_analysis_leaves_the_others_alone(client):
    first, second = build_everything(client), build_everything(client)
    client.delete(f"/api/analyses/{first}")
    assert client.get(f"/api/analyses/{second}").status_code == 200
    assert counts(client)["Analysis"] == 1 and counts(client)["Explanation"] == 1


def test_a_shared_query_snapshot_survives_until_its_last_analysis_is_deleted(client):
    first = build_everything(client)
    with client.app.state.sessionmaker() as s:
        analysis = s.get(Analysis, first)
        twin = Analysis(project_id=analysis.project_id, snapshot_id=analysis.snapshot_id, status="completed")
        s.add(twin)
        s.commit()
        twin_id = twin.id
    client.delete(f"/api/analyses/{first}")
    assert counts(client)["QuerySnapshot"] == 1
    client.delete(f"/api/analyses/{twin_id}")
    assert counts(client)["QuerySnapshot"] == 0


def test_unknown_analyses_are_404_and_running_ones_must_be_cancelled_first(client):
    assert client.delete("/api/analyses/99999").status_code == 404
    aid = build_everything(client)
    with client.app.state.sessionmaker() as s:
        s.get(Analysis, aid).status = "running"
        s.commit()
    r = client.delete(f"/api/analyses/{aid}")
    assert r.status_code == 409 and "cancel" in r.json()["detail"].lower()
