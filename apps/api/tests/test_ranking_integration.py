import math

import pytest
from fastapi.testclient import TestClient
from querydoctor.ranking import FEATURE_NAMES
from querydoctor.ranking.predictor import Contribution, Prediction
from test_benchmarks_api import FakeRunner, make_result
from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

from app.main import create_app
from app.models import ModelOutcome, ModelPrediction, UserFeedback
from app.services.benchmarks import BenchmarkDeps


class StubPredictor:
    """Stands in for a trained model: a candidate with INCLUDE columns scores low, so a model-ordered list puts the
    key-only index first (the planner score prefers the INCLUDE one)."""

    def __init__(self, *, use_for_ranking=False, mode="ok", training_mae=0.3):
        self.use_for_ranking = use_for_ranking
        self.mode = mode
        self.seen: list[dict] = []
        self.meta = {
            "use_for_ranking": use_for_ranking, "deployed_model": "lgbm_regressor", "feature_names": FEATURE_NAMES,
            "reason": "stub", "models": {"lgbm_regressor": {"holdout": {"mae_log_speedup": training_mae}}},
        }

    def predict_many(self, feature_dicts):
        self.seen += feature_dicts
        if self.mode == "raise":
            raise RuntimeError("model exploded")
        out = []
        for f in feature_dicts:
            score = float("nan") if self.mode == "nan" else 10.0 - f["c_include_cols"]
            out.append(
                Prediction(
                    expected_speedup=8.0, p_improve=0.93, expected_index_mb=4.5, p_regression=0.02, rank_score=score,
                    contributions=[Contribution("d_removed_seq_scans", 1.0, 1.2, "removes a sequential scan raises the expected speedup")],
                    model_version="stub-1", base_value=0.5, other_effect=0.1, expected_log_speedup=math.log(8.0),
                )
            )
        return out


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def make_client(settings, bus, stub, runner):
    def build(predictor):
        deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95), "DESC)": comparison(score=0.7)}), predictor=predictor)
        bench = BenchmarkDeps(bench_dsn="stub", bus=bus, run=runner)
        return TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps, benchmark_deps=bench))

    return build


def analyse(client) -> dict:
    pid = client.get("/api/projects").json()[0]["id"]
    body = {"sql": HEADLINE_SQL, "params": [4242, "2022-01-01"], "execute": False, "project_id": pid}
    analysis_id = client.post("/api/analyses", json=body).json()["id"]
    return client.get(f"/api/analyses/{analysis_id}").json()


def kept(analysis):
    return sorted((c for c in analysis["candidates"] if not c["dropped"]), key=lambda c: c["rank"])


def test_predictions_are_stored_and_shown_next_to_but_separate_from_the_evidence_level(make_client):
    predictor = StubPredictor()
    with make_client(predictor) as client:
        analysis = analyse(client)
    assert analysis["status"] == "completed"
    for c in kept(analysis):
        assert c["validation"] == "planner_validated"  # the model never changes the evidence level
        p = c["prediction"]
        assert p["expected_speedup"] == 8.0 and p["p_improve"] == 0.93 and p["model_version"] == "stub-1"
        assert p["expected_index_mb"] == 4.5 and p["p_regression"] == 0.02 and p["used_for_ranking"] is False
        assert p["contributions"][0]["phrase"].startswith("removes a sequential scan")
    assert len(predictor.seen) == 2 and all(list(f) == FEATURE_NAMES for f in predictor.seen)


def test_without_use_for_ranking_the_planner_score_keeps_ordering(make_client):
    with make_client(StubPredictor(use_for_ranking=False)) as client:
        ordered = kept(analyse(client))
    assert "INCLUDE" in ordered[0]["ddl"] and ordered[0]["planner_score"] == 0.95  # the planner prefers the covering index


def test_with_use_for_ranking_the_model_reorders_planner_validated_candidates_only(make_client):
    with make_client(StubPredictor(use_for_ranking=True)) as client:
        ordered = kept(analyse(client))
    assert "INCLUDE" not in ordered[0]["ddl"] and [c["rank"] for c in ordered] == [1, 2]
    assert ordered[0]["prediction"]["used_for_ranking"] is True
    assert {c["validation"] for c in ordered} == {"planner_validated"}  # ordering changed, evidence did not


@pytest.mark.parametrize("predictor", [None, StubPredictor(mode="raise"), StubPredictor(mode="nan", use_for_ranking=True)])
def test_a_missing_failing_or_nonsensical_model_changes_nothing(make_client, predictor):
    with make_client(predictor) as client:
        analysis = analyse(client)
    assert analysis["status"] == "completed"
    ordered = kept(analysis)
    assert all(c["prediction"] is None for c in ordered)
    assert "INCLUDE" in ordered[0]["ddl"]  # heuristic order


def test_a_completed_benchmark_records_the_actual_versus_predicted_outcome(make_client, runner):
    runner.result = make_result(improvement=87.5)  # 8x faster: 1 / (1 - 0.875)
    with make_client(StubPredictor()) as client:
        analysis = analyse(client)
        cand = kept(analysis)[0]
        assert client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/benchmark").status_code == 202
        with client.app.state.sessionmaker() as s:
            outcome = s.query(ModelOutcome).one()
            assert outcome.candidate_id == cand["id"] and outcome.model_version == "stub-1"
            assert outcome.predicted_speedup == 8.0 and outcome.actual_speedup == pytest.approx(8.0, rel=0.01)
            assert outcome.abs_error_log == pytest.approx(0.0, abs=0.01) and outcome.actual_useful is True and outcome.predicted_positive is True
        monitor = client.get("/api/model/monitor").json()
    assert monitor["n"] == 1 and monitor["mae_log_speedup"] == pytest.approx(0.0, abs=0.01)
    assert monitor["precision_of_positive"] == 1.0 and monitor["drift"] is False


def test_a_failed_benchmark_or_a_candidate_without_a_prediction_records_no_outcome(make_client, runner):
    runner.error = __import__("querydoctor.benchmark.runner", fromlist=["BenchmarkFailed"]).BenchmarkFailed("boom")
    with make_client(StubPredictor()) as client:
        analysis = analyse(client)
        client.post(f"/api/analyses/{analysis['id']}/candidates/{kept(analysis)[0]['id']}/benchmark")
        with client.app.state.sessionmaker() as s:
            assert s.query(ModelOutcome).count() == 0
    runner.error = None
    with make_client(None) as client:
        analysis = analyse(client)
        client.post(f"/api/analyses/{analysis['id']}/candidates/{kept(analysis)[0]['id']}/benchmark")
        with client.app.state.sessionmaker() as s:
            assert s.query(ModelOutcome).count() == 0


def seed_outcomes(client, n, *, error):
    with client.app.state.sessionmaker() as s:
        cand = s.query(ModelPrediction).first().candidate_id
        for i in range(n):
            s.add(
                ModelOutcome(
                    candidate_id=cand, benchmark_run_id=1000 + i, model_version="stub-1", predicted_speedup=8.0,
                    actual_speedup=8.0 * math.exp(error), abs_error_log=error, predicted_p_improve=0.9, predicted_positive=True,
                    actual_useful=i % 2 == 0,
                )
            )
        s.commit()


@pytest.mark.parametrize(
    ("n", "error", "drift"),
    [(19, 1.0, False), (20, 1.0, True), (20, 0.4, False), (25, 0.46, True)],  # training MAE 0.3: the limit is 0.45
)
def test_drift_needs_enough_outcomes_and_an_error_well_above_the_training_error(make_client, n, error, drift):
    with make_client(StubPredictor(training_mae=0.3)) as client:
        analyse(client)
        seed_outcomes(client, n, error=error)
        monitor = client.get("/api/model/monitor").json()
    assert monitor["n"] == n and monitor["mae_log_speedup"] == pytest.approx(error)
    assert monitor["drift"] is drift and monitor["training_mae"] == 0.3
    assert monitor["precision_of_positive"] == pytest.approx(math.ceil(n / 2) / n)
    assert monitor["calibration"] and monitor["calibration"][0]["count"] == n


def test_monitor_and_model_endpoints_without_a_model(make_client):
    with make_client(None) as client:
        assert client.get("/api/model").json() == {"available": False}
        monitor = client.get("/api/model/monitor").json()
    assert monitor["n"] == 0 and monitor["drift"] is False and monitor["mae_log_speedup"] is None


def test_the_model_endpoint_serves_the_training_metadata(make_client):
    with make_client(StubPredictor(use_for_ranking=True)) as client:
        body = client.get("/api/model").json()
    assert body["available"] is True and body["use_for_ranking"] is True and body["reason"] == "stub"
    assert body["models"]["lgbm_regressor"]["holdout"]["mae_log_speedup"] == 0.3


def feedback(client, analysis, cand, session="s1", **body):
    body = {"helpful": True, **body}
    return client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/feedback", json=body, headers={"X-QD-Session": session})


def test_feedback_is_stored_once_per_candidate_and_session(make_client):
    with make_client(StubPredictor()) as client:
        analysis = analyse(client)
        a, b = kept(analysis)
        assert feedback(client, analysis, a, helpful=True, comment="worked").status_code == 200
        assert feedback(client, analysis, a, helpful=False, comment="changed my mind").status_code == 200  # same session: updated
        assert feedback(client, analysis, a, session="s2", helpful=True).status_code == 200  # another session: a second row
        assert feedback(client, analysis, b).status_code == 200
        with client.app.state.sessionmaker() as s:
            rows = s.query(UserFeedback).filter_by(candidate_id=a["id"]).order_by(UserFeedback.id).all()
            assert [(r.session_id, r.helpful, r.comment) for r in rows] == [("s1", False, "changed my mind"), ("s2", True, None)]
            assert s.query(UserFeedback).count() == 3


@pytest.mark.parametrize("body", [{}, {"helpful": "maybe"}, {"helpful": True, "comment": "x" * 501}])
def test_invalid_feedback_is_rejected(make_client, body):
    with make_client(StubPredictor()) as client:
        analysis = analyse(client)
        cand = kept(analysis)[0]
        r = client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/feedback", json=body, headers={"X-QD-Session": "s"})
        assert r.status_code == 422
        assert client.post(f"/api/analyses/{analysis['id']}/candidates/{cand['id']}/feedback", json={"helpful": True}, headers={"X-QD-Session": "x" * 100}).status_code == 422


def test_feedback_on_an_unknown_or_foreign_candidate_is_404(make_client):
    with make_client(StubPredictor()) as client:
        first, second = analyse(client), analyse(client)
        assert feedback(client, first, {"id": 99999}).status_code == 404
        assert feedback(client, first, kept(second)[0]).status_code == 404  # the candidate belongs to another analysis
        assert client.post("/api/analyses/99999/candidates/1/feedback", json={"helpful": True}).status_code == 404
