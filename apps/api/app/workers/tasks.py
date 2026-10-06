from __future__ import annotations

from functools import lru_cache

from celery.signals import worker_ready

from app.config import get_settings
from app.database import make_engine, make_sessionmaker
from app.services.benchmarks import BenchmarkDeps, run_benchmark_job
from app.services.events import get_event_bus
from app.services.pipeline import PipelineDeps, run_analysis_pipeline
from app.workers.celery_app import celery_app
from app.workers.janitor import cleanup_stale_sandboxes


@lru_cache
def _runtime():
    settings = get_settings()
    sessionmaker = make_sessionmaker(make_engine(settings.database_url))
    deps = PipelineDeps(
        target_dsn=settings.target_dsn,
        bus=get_event_bus(settings.redis_url),
        max_plan_bytes=settings.max_plan_bytes,
        thresholds=settings.diagnosis_thresholds(),
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
    return sessionmaker, BenchmarkDeps(bench_dsn=settings.bench_dsn, bus=get_event_bus(settings.redis_url))


@celery_app.task(name="qd.run_benchmark")
def run_benchmark(run_id: int) -> None:
    sessionmaker, deps = _benchmark_runtime()
    with sessionmaker() as session:
        run_benchmark_job(session, run_id, deps)


@worker_ready.connect
def _clean_up_after_crashed_workers(**_kwargs) -> None:
    cleanup_stale_sandboxes(get_settings().bench_dsn)
