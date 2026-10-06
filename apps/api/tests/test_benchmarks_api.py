import json

import pytest
from fastapi.testclient import TestClient
from querydoctor.benchmark import BenchmarkResult, RunSample, summarize
from querydoctor.benchmark.runner import BenchmarkBusy, BenchmarkCancelled, BenchmarkFailed
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

from app.main import create_app
from app.models import AuditEvent, BenchmarkRun
from app.services.benchmarks import BenchmarkDeps


def make_result(improvement=94.5, warnings=(), checksum_match=True, index_used=True) -> BenchmarkResult:
    before = [700.0, 742.0, 750.0, 735.0, 760.0, 740.0, 745.0]
    factor = 1 - improvement / 100
    after = [x * factor for x in before]
    return BenchmarkResult(
        before=summarize(before), after=summarize(after),
        before_samples=[RunSample(execution_ms=x, rows=10) for x in before],
        after_samples=[RunSample(execution_ms=x, rows=10) for x in after],
        improvement_pct=improvement, index_size_bytes=5_000_000, index_build_ms=68.0,
        plan_fingerprint_before="aaa", plan_fingerprint_after="bbb", checksum_match=checksum_match, index_used=index_used,
        warnings=list(warnings), pg_version="PostgreSQL 16", settings={"work_mem": "4096"},
        dataset_snapshot="snap", ddl="CREATE INDEX ON orders (customer_id)",
    )


class FakeRunner:
    def __init__(self):
        self.result = make_result()
        self.error: Exception | None = None
        self.on_stage = None  # callback(stage) run as each stage starts
        self.calls: list[dict] = []
        self.stages = ["creating_sandbox", "warmup_before", "measuring_before", "creating_index", "analyzing",
                       "warmup_after", "measuring_after", "comparing", "teardown"]

    def __call__(self, bench_dsn, sql, params, ddl, config, on_progress=lambda s: None, *, cancelled=lambda: False, template="x"):
        self.calls.append({"sql": sql, "params": list(params), "ddl": ddl, "runs": config.runs})
        for stage in self.stages:
            on_progress(stage)
            if self.on_stage:
                self.on_stage(stage)
            if cancelled():
                raise BenchmarkCancelled("The benchmark was cancelled.")
            if stage == "measuring_before" and self.error:
                raise self.error
        return self.result


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def make_client(settings, bus, stub, runner):
    def build(enqueue_benchmark=None):
        deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95), "DESC)": comparison(score=0.7)}))
        bench = BenchmarkDeps(bench_dsn="stub", bus=bus, run=runner)
        return TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps, benchmark_deps=bench, enqueue_benchmark=enqueue_benchmark))

    return build


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def analyse(client) -> dict:
    pid = client.get("/api/projects").json()[0]["id"]
    body = {"sql": HEADLINE_SQL, "params": [4242, "2022-01-01"], "execute": False, "project_id": pid}
    analysis_id = client.post("/api/analyses", json=body).json()["id"]
    return client.get(f"/api/analyses/{analysis_id}").json()


def best_candidate(analysis):
    return next(c for c in analysis["candidates"] if c["rank"] == 1)


def approve(client, analysis, candidate=None, **body):
    candidate = candidate or best_candidate(analysis)
    return client.post(f"/api/analyses/{analysis['id']}/candidates/{candidate['id']}/benchmark", json=body or None)


def sse(client, url):
    events = []
    with client.stream("GET", url) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_approved_benchmark_runs_and_validates_the_candidate(client, runner):
    analysis = analyse(client)
    response = approve(client, analysis)
    assert response.status_code == 202
    run = client.get(f"/api/benchmarks/{response.json()['id']}").json()
    assert run["status"] == "completed" and run["error"] is None
    assert run["result"]["improvement_pct"] == 94.5 and run["result"]["before"]["n"] == 7
    assert runner.calls == [{"sql": analysis["sql"], "params": [4242, "2022-01-01"], "ddl": best_candidate(analysis)["ddl"], "runs": 7}]

    after = client.get(f"/api/analyses/{analysis['id']}").json()
    assert best_candidate(after)["validation"] == "benchmark_validated"
    assert after["benchmarks"][0]["id"] == run["id"] and after["benchmarks"][0]["candidate_id"] == best_candidate(analysis)["id"]


def test_the_approval_and_the_sandbox_index_are_audited(client):
    analysis = analyse(client)
    approve(client, analysis)
    session = client.app.state.sessionmaker()
    events = [e.event_type for e in session.scalars(select(AuditEvent).where(AuditEvent.analysis_id == analysis["id"]))]
    assert "benchmark_approved" in events and "sandbox_index_created" in events


def test_a_custom_run_count_is_used_and_validated(client, runner):
    analysis = analyse(client)
    assert approve(client, analysis, runs=10).status_code == 202 and runner.calls[-1]["runs"] == 10
    for bad in (4, 11):
        other = best_candidate(analysis)
        assert approve(client, analysis, other, runs=bad).status_code == 422


def test_approving_twice_while_a_run_is_active_returns_the_same_run(make_client):
    with make_client(enqueue_benchmark=lambda run_id: None) as client:  # the run stays queued
        analysis = analyse(client)
        first = approve(client, analysis)
        second = approve(client, analysis)
        assert first.status_code == 202 and second.status_code == 200
        assert first.json()["id"] == second.json()["id"]


def test_a_second_active_run_for_one_candidate_is_rejected_by_the_database(client):
    analysis = analyse(client)
    candidate_id = best_candidate(analysis)["id"]
    session = client.app.state.sessionmaker()
    session.add(BenchmarkRun(analysis_id=analysis["id"], candidate_id=candidate_id, status="queued", config={}))
    session.commit()
    session.add(BenchmarkRun(analysis_id=analysis["id"], candidate_id=candidate_id, status="running", config={}))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(BenchmarkRun(analysis_id=analysis["id"], candidate_id=candidate_id, status="completed", config={}))
    session.commit()  # finished runs never conflict


def test_a_regression_is_stored_but_does_not_validate_the_candidate(client, runner):
    runner.result = make_result(improvement=-20.0)
    analysis = analyse(client)
    run = client.get(f"/api/benchmarks/{approve(client, analysis).json()['id']}").json()
    assert run["status"] == "completed" and run["result"]["improvement_pct"] == -20.0
    assert best_candidate(client.get(f"/api/analyses/{analysis['id']}").json())["validation"] == "planner_validated"


def test_different_output_blocks_validation(client, runner):
    runner.result = make_result(checksum_match=False, warnings=["Output differs: the query returned different results"])
    analysis = analyse(client)
    run = client.get(f"/api/benchmarks/{approve(client, analysis).json()['id']}").json()
    assert run["result"]["warnings"] and run["result"]["checksum_match"] is False
    assert best_candidate(client.get(f"/api/analyses/{analysis['id']}").json())["validation"] == "planner_validated"


def test_a_non_blocking_warning_such_as_cache_warming_does_not_stop_validation(client, runner):
    runner.result = make_result(warnings=["Cache warming dominates: the first measured run was 3.4x the median."])
    analysis = analyse(client)
    approve(client, analysis)
    assert best_candidate(client.get(f"/api/analyses/{analysis['id']}").json())["validation"] == "benchmark_validated"


@pytest.mark.parametrize(
    "result",
    [
        make_result(improvement=2.0),  # inside the noise
        make_result(index_used=False, warnings=["The new index was not used by the query's final plan"]),
    ],
    ids=["noise", "index-unused"],
)
def test_results_that_do_not_clearly_show_the_index_helping_never_validate_it(client, runner, result):
    runner.result = result
    analysis = analyse(client)
    run = client.get(f"/api/benchmarks/{approve(client, analysis).json()['id']}").json()
    assert run["status"] == "completed" and run["result"] is not None  # still recorded and shown
    assert best_candidate(client.get(f"/api/analyses/{analysis['id']}").json())["validation"] == "planner_validated"


def test_progress_events_are_streamed_and_replayed(client):
    analysis = analyse(client)
    run_id = approve(client, analysis).json()["id"]
    types = [e["type"] for e in sse(client, f"/api/benchmarks/{run_id}/events")]
    assert types[0] == "benchmarking" and types[-1] == "benchmark_completed"
    assert {"creating_sandbox", "creating_index", "measuring_after", "teardown"} <= set(types)
    assert [e["type"] for e in sse(client, f"/api/benchmarks/{run_id}/events")] == types  # late subscriber


@pytest.mark.parametrize(
    "error,expected",
    [(BenchmarkFailed("The index could not be created: boom"), "boom"), (BenchmarkBusy("Too many benchmarks"), "Too many"),
     (RuntimeError("secret internal detail"), "internal error")],
)
def test_failures_end_failed_with_a_safe_message(client, runner, error, expected):
    runner.error = error
    analysis = analyse(client)
    run = client.get(f"/api/benchmarks/{approve(client, analysis).json()['id']}").json()
    assert run["status"] == "failed" and expected in run["error"] and "secret internal detail" not in run["error"]
    assert sse(client, f"/api/benchmarks/{run['id']}/events")[-1]["type"] == "failed"
    assert best_candidate(client.get(f"/api/analyses/{analysis['id']}").json())["validation"] == "planner_validated"


def test_cancelling_a_queued_or_running_benchmark_and_conflict_when_finished(make_client):
    with make_client(enqueue_benchmark=lambda run_id: None) as client:
        analysis = analyse(client)
        run_id = approve(client, analysis).json()["id"]
        cancelled = client.post(f"/api/benchmarks/{run_id}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert client.get(f"/api/benchmarks/{run_id}").json()["status"] == "cancelled"
        assert client.post(f"/api/benchmarks/{run_id}/cancel").status_code == 409
        assert client.post("/api/benchmarks/9999/cancel").status_code == 404
        session = client.app.state.sessionmaker()
        assert "benchmark_cancelled" in [e.event_type for e in session.scalars(select(AuditEvent))]


def test_the_job_notices_a_cancellation_that_arrives_mid_run(client, runner):
    analysis = analyse(client)
    session = client.app.state.sessionmaker()
    run = BenchmarkRun(analysis_id=analysis["id"], candidate_id=best_candidate(analysis)["id"], status="queued", config={"runs": 7})
    session.add(run)
    session.commit()
    runner.on_stage = lambda stage: client.post(f"/api/benchmarks/{run.id}/cancel") if stage == "measuring_before" else None

    from app.services.benchmarks import run_benchmark_job

    run_benchmark_job(session, run.id, client.app.state.benchmark_deps)
    session.refresh(run)
    assert run.status == "cancelled" and run.result is None
    assert sse(client, f"/api/benchmarks/{run.id}/events")[-1]["type"] == "cancelled"


def test_unknown_dropped_or_unfinished_targets_are_rejected(client, make_client):
    analysis = analyse(client)
    assert client.post(f"/api/analyses/{analysis['id']}/candidates/9999/benchmark").status_code == 404
    assert client.post("/api/analyses/9999/candidates/1/benchmark").status_code == 404
    dropped = next((c for c in analysis["candidates"] if c["dropped"]), None)
    if dropped:
        assert approve(client, analysis, dropped).status_code == 409
    other = analyse(client)
    assert client.post(f"/api/analyses/{analysis['id']}/candidates/{best_candidate(other)['id']}/benchmark").status_code == 404

    session = client.app.state.sessionmaker()
    from app.models import Analysis
    record = session.get(Analysis, analysis["id"])
    record.status = "running"
    session.commit()
    assert approve(client, analysis).status_code == 409


def test_report_endpoint_returns_markdown_or_html_and_is_audited(client):
    analysis = analyse(client)
    approve(client, analysis)
    md = client.get(f"/api/analyses/{analysis['id']}/report?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "Median" in md.text and "94.5%" in md.text and "attachment" in md.headers["content-disposition"]
    html = client.get(f"/api/analyses/{analysis['id']}/report?format=html")
    assert html.headers["content-type"].startswith("text/html") and html.text.startswith("<!doctype html>")
    assert client.get(f"/api/analyses/{analysis['id']}/report?format=pdf").status_code == 422
    assert client.get("/api/analyses/9999/report").status_code == 404
    session = client.app.state.sessionmaker()
    audited = [e.event_type for e in session.scalars(select(AuditEvent).where(AuditEvent.analysis_id == analysis["id"]))]
    assert audited.count("report_exported") == 2
