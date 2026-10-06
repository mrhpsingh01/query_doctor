"""Failure injection against the running stack. Run explicitly:

    QD_REQUIRE_STACK=1 python -m pytest -m chaos apps/api/tests/chaos

These stop and restart real containers (target-postgres, the worker), so they are excluded from the default run.
"""

import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import psycopg
import pytest

pytestmark = [pytest.mark.chaos, pytest.mark.stack]

BASE = "http://127.0.0.1:8010"
ADMIN = "postgresql://querydoctor:querydoctor@127.0.0.1:5433/querydoctor_sample"
BENCH = "postgresql://qd_bench:qd_bench@127.0.0.1:5434/postgres"
TERMINAL = {"completed", "failed", "cancelled"}
SAMPLE = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"
SAMPLE_PARAMS = [4242, "2022-01-01"]
HEAVY = "SELECT count(*) FROM events a JOIN events b ON a.customer_id = b.customer_id WHERE a.event_type = $1"


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=30) as client:
        yield client


def start(http, sql, params, execute=True) -> int:
    pid = http.get("/api/projects").json()[0]["id"]
    r = http.post("/api/analyses", json={"sql": sql, "params": params, "execute": execute, "project_id": pid})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def wait(http, analysis_id, timeout) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.get(f"/api/analyses/{analysis_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.5)
    pytest.fail(f"analysis {analysis_id} was still not finished after {timeout}s")


def healthy(container: str, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker("inspect", "-f", "{{.State.Health.Status}}", container) == "healthy":
            return
        time.sleep(1)
    pytest.fail(f"{container} did not become healthy")


def bench_databases() -> list[str]:
    with psycopg.connect(BENCH, autocommit=True) as conn:
        return [r[0] for r in conn.execute(r"SELECT datname FROM pg_database WHERE datname LIKE 'bench\_%'")]


def test_a_table_lock_held_by_someone_else_fails_the_analysis_fast_and_the_stack_stays_up(http):
    holder = psycopg.connect(ADMIN, autocommit=False)
    try:
        holder.execute("LOCK TABLE customers IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        body = wait(http, start(http, "SELECT id, email FROM customers WHERE id = $1", [7]), timeout=20)
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert body["status"] == "failed" and elapsed < 10, (body["status"], elapsed)
    assert any(word in (body["error"] or "").lower() for word in ("lock", "timeout", "timed out")), body["error"]
    assert http.get("/api/health").status_code == 200
    assert wait(http, start(http, SAMPLE, SAMPLE_PARAMS, execute=False), timeout=60)["status"] == "completed"  # and it recovers


def test_restarting_the_target_database_mid_analysis_fails_that_analysis_and_the_next_one_succeeds(http):
    analysis_id = start(http, HEAVY, ["page_view"])
    time.sleep(2)
    docker("restart", "querydoctor-target-postgres-1")
    body = wait(http, analysis_id, timeout=90)
    assert body["status"] == "failed" and body["error"], body
    healthy("querydoctor-target-postgres-1")
    time.sleep(3)
    assert wait(http, start(http, SAMPLE, SAMPLE_PARAMS, execute=False), timeout=90)["status"] == "completed"
    assert http.get("/api/health").status_code == 200


def test_killing_the_worker_during_a_benchmark_leaves_a_failed_run_and_no_leftover_sandbox(http):
    # a slow query (a 300k-row sort) so the benchmark is certainly still measuring when the worker dies
    sort = "SELECT id, customer_id, payload, created_at FROM events WHERE event_type = $1 ORDER BY payload"
    analysis = wait(http, start(http, sort, ["page_view"], execute=False), timeout=60)
    idea = next(c for c in analysis["candidates"] if not c["dropped"])
    run_id = http.post(f"/api/analyses/{analysis['id']}/candidates/{idea['id']}/benchmark", json={"runs": 10}).json()["id"]
    deadline = time.monotonic() + 90
    while http.get(f"/api/benchmarks/{run_id}").json()["stage"] != "measuring_before" and time.monotonic() < deadline:
        time.sleep(0.2)
    assert http.get(f"/api/benchmarks/{run_id}").json()["status"] == "running"
    docker("kill", "querydoctor-worker-1")
    docker("start", "querydoctor-worker-1")
    deadline = time.monotonic() + 150
    run = {}
    while time.monotonic() < deadline:
        run = http.get(f"/api/benchmarks/{run_id}").json()
        if run["status"] in TERMINAL:
            break
        time.sleep(2)
    assert run["status"] == "failed" and "worker" in (run["error"] or "").lower(), run
    deadline = time.monotonic() + 60
    while bench_databases() and time.monotonic() < deadline:
        time.sleep(2)
    assert bench_databases() == []
    # the restarted worker is healthy again
    assert wait(http, start(http, SAMPLE, SAMPLE_PARAMS, execute=False), timeout=90)["status"] == "completed"


class _Hanging(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        time.sleep(30)


def test_a_language_model_that_hangs_falls_back_to_the_template_within_the_timeout(settings, bus, stub):
    from fastapi.testclient import TestClient
    from querydoctor.explain import OllamaExplainer
    from test_pipeline_candidates import HEADLINE_SQL, comparison, fake_scorer

    from app.main import create_app
    from app.services.explanations import ExplainDeps

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hanging)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ollama = OllamaExplainer(f"http://127.0.0.1:{server.server_address[1]}", "qd-hang", timeout_s=1.5)
        deps = stub.deps(bus, scorer=fake_scorer({"INCLUDE": comparison(score=0.95)}))
        with TestClient(create_app(settings, event_bus=bus, pipeline_deps=deps, explain_deps=ExplainDeps(ollama=ollama))) as client:
            pid = client.get("/api/projects").json()[0]["id"]
            aid = client.post("/api/analyses", json={"sql": HEADLINE_SQL, "params": [4242, "2022-01-01"], "execute": False, "project_id": pid}).json()["id"]
            started = time.monotonic()
            eid = client.post(f"/api/analyses/{aid}/explanations", json={"kind": "plan"}).json()["id"]
            body = client.get(f"/api/explanations/{eid}").json()
            elapsed = time.monotonic() - started
    finally:
        server.shutdown()
    assert body["status"] == "completed" and body["source"] == "template" and "timed out" in body["fallback_reason"].lower()
    assert elapsed < 6
