from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache

from celery.signals import worker_ready
from querydoctor.ranking.predictor import Predictor

from app.config import get_settings
from app.database import make_engine, make_sessionmaker
from app.services.benchmarks import BenchmarkDeps, run_benchmark_job
from app.services.connections import dsn_builder
from app.services.events import get_event_bus
from app.services.explanations import ExplainDeps, run_explanation_job
from app.services.pipeline import PipelineDeps, run_analysis_pipeline
from app.services.workload import run_demo_traffic, run_mongo_demo_traffic
from app.workers.celery_app import celery_app
from app.workers.janitor import cleanup_stale_mongo_sandboxes, cleanup_stale_sandboxes, fail_orphaned_jobs, fail_stale_jobs


log = logging.getLogger(__name__)
ORPHAN_SWEEP_DELAY_S = 8


@lru_cache
def _runtime():
    settings = get_settings()
    sessionmaker = make_sessionmaker(make_engine(settings.database_url))
    deps = PipelineDeps(
        target_dsn=settings.target_dsn,
        bus=get_event_bus(settings.redis_url),
        max_plan_bytes=settings.max_plan_bytes,
        thresholds=settings.diagnosis_thresholds(),
        predictor=Predictor.load(settings.model_dir),
        connection_dsn=dsn_builder(settings),
        mongo_target_uri=settings.mongo_target_uri,
    )
    return sessionmaker, deps


@celery_app.task(name="qd.run_analysis")
def run_analysis(analysis_id: int) -> None:
    sessionmaker, deps = _runtime()
    with sessionmaker() as session:
        run_analysis_pipeline(session, analysis_id, deps)


@lru_cache
def _benchmark_runtime():
    settings = get_settings()
    sessionmaker = make_sessionmaker(make_engine(settings.database_url))
    def janitor() -> None:
        cleanup_stale_sandboxes(settings.bench_dsn)
        cleanup_stale_mongo_sandboxes(settings.mongo_bench_uri)
        fail_stale_jobs(sessionmaker)

    return sessionmaker, BenchmarkDeps(
        bench_dsn=settings.bench_dsn, bus=get_event_bus(settings.redis_url), janitor=janitor, mongo_bench_uri=settings.mongo_bench_uri
    )


@celery_app.task(name="qd.run_benchmark")
def run_benchmark(run_id: int) -> None:
    sessionmaker, deps = _benchmark_runtime()
    with sessionmaker() as session:
        run_benchmark_job(session, run_id, deps)


@celery_app.task(name="qd.run_explanation")
def run_explanation(explanation_id: int) -> None:
    from querydoctor.explain import OllamaExplainer

    settings = get_settings()
    sessionmaker, _deps = _runtime()
    ollama = OllamaExplainer(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s) if settings.ollama_model else None
    with sessionmaker() as session:
        run_explanation_job(session, explanation_id, ExplainDeps(ollama=ollama))


@celery_app.task(name="qd.run_demo_workload")
def run_demo_workload(mode: str, engine: str = "postgres") -> dict[str, int]:
    settings = get_settings()
    if engine == "mongodb":
        return run_mongo_demo_traffic(settings.mongo_target_uri, settings.scale, mode)
    return run_demo_traffic(settings.target_dsn, settings.scale, mode)


def _live_task_ids() -> tuple[set[int], set[int]] | None:
    """The benchmark and analysis ids that running workers are executing right now, or None if no worker answered."""
    try:
        active = celery_app.control.inspect(timeout=3).active()
    except Exception:
        return None
    if active is None:
        return None
    benchmarks: set[int] = set()
    analyses: set[int] = set()
    for tasks in active.values():
        for task in tasks:
            found = {int(n) for n in re.findall(r"\d+", str(task.get("args", "")))}
            if task.get("name") == "qd.run_benchmark":
                benchmarks |= found
            elif task.get("name") == "qd.run_analysis":
                analyses |= found
    return benchmarks, analyses


def _sweep_orphans() -> None:
    sessionmaker, _deps = _benchmark_runtime()
    live = _live_task_ids()
    if live is None:  # nobody answered: the age-based sweep at start-up is all there is
        log.warning("no worker answered the start-up inspection; orphaned jobs will be failed by age instead")
        return
    benchmarks, analyses = live
    fail_orphaned_jobs(sessionmaker, benchmarks, analyses)
    if not benchmarks:  # no benchmark is running anywhere, so any sandbox that is not brand new is an orphan
        cleanup_stale_sandboxes(get_settings().bench_dsn, max_age_s=15)
        cleanup_stale_mongo_sandboxes(get_settings().mongo_bench_uri, max_age_s=15)


@worker_ready.connect
def _clean_up_after_crashed_workers(**_kwargs) -> None:
    sessionmaker, deps = _benchmark_runtime()
    deps.janitor()
    # Asking the workers what they are running needs this worker's event loop, which is busy running this very handler,
    # so the inspection happens a few seconds later on a timer thread.
    timer = threading.Timer(ORPHAN_SWEEP_DELAY_S, _sweep_orphans)
    timer.daemon = True
    timer.start()
