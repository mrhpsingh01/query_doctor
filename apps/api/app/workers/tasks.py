from __future__ import annotations

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
from app.services.workload import run_demo_traffic
from app.workers.celery_app import celery_app
from app.workers.janitor import cleanup_stale_sandboxes, fail_stale_jobs


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
        fail_stale_jobs(sessionmaker)

    return sessionmaker, BenchmarkDeps(bench_dsn=settings.bench_dsn, bus=get_event_bus(settings.redis_url), janitor=janitor)


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
def run_demo_workload(mode: str) -> dict[str, int]:
    settings = get_settings()
    return run_demo_traffic(settings.target_dsn, settings.scale, mode)


@worker_ready.connect
def _clean_up_after_crashed_workers(**_kwargs) -> None:
    sessionmaker, deps = _benchmark_runtime()
    deps.janitor()
