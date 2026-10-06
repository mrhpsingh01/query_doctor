import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from querydoctor.ranking.predictor import Predictor
from sqlalchemy import select

from app.api import analyses, benchmarks, connections, model, projects, samples, workload
from app.config import Settings, get_settings
from app.database import make_engine, make_sessionmaker
from app.models import Base, Project
from app.services.events import EventBus, get_event_bus
from app.services.benchmarks import BenchmarkDeps, run_benchmark_job
from app.services.pipeline import PipelineDeps, run_analysis_pipeline
from app.services.workload import WorkloadDeps, run_demo_traffic

SAMPLE_PROJECT_NAME = "Sample shop"


def ensure_sample_project(sessionmaker) -> None:
    with sessionmaker() as session:
        if session.scalar(select(Project).where(Project.name == SAMPLE_PROJECT_NAME)) is None:
            session.add(Project(name=SAMPLE_PROJECT_NAME, description="QueryDoctor's prepared e-commerce database"))
            session.commit()


def create_app(
    settings: Settings | None = None,
    *,
    event_bus: EventBus | None = None,
    pipeline_deps: PipelineDeps | None = None,
    enqueue: Callable[[int], None] | None = None,
    benchmark_deps: BenchmarkDeps | None = None,
    enqueue_benchmark: Callable[[int], None] | None = None,
    workload_deps: WorkloadDeps | None = None,
) -> FastAPI:
    """Build the API. ``event_bus`` / ``pipeline_deps`` / ``enqueue`` exist so tests can run everything in-process;
    in deployment the defaults hand analyses to the Celery worker over Redis."""
    settings = settings or get_settings()
    bus = event_bus or get_event_bus(settings.redis_url)
    deps = pipeline_deps or PipelineDeps(
        target_dsn=settings.target_dsn,
        bus=bus,
        max_plan_bytes=settings.max_plan_bytes,
        thresholds=settings.diagnosis_thresholds(),
        predictor=Predictor.load(settings.model_dir),
    )

    bench_deps = benchmark_deps or BenchmarkDeps(bench_dsn=settings.bench_dsn, bus=bus)

    in_process = settings.redis_url.startswith("memory://")

    def demo_in_worker(mode: str) -> None:
        from app.workers.tasks import run_demo_workload

        run_demo_workload.delay(mode)

    def demo_in_thread(mode: str) -> None:
        threading.Thread(target=run_demo_traffic, args=(settings.target_dsn, settings.scale, mode), daemon=True).start()

    wl_deps = workload_deps or WorkloadDeps(
        target_dsn=settings.target_dsn, demo_runner=demo_in_thread if in_process else demo_in_worker
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.auto_create_tables:
            Base.metadata.create_all(engine)
        app.state.sessionmaker = make_sessionmaker(engine)
        ensure_sample_project(app.state.sessionmaker)
        yield
        engine.dispose()

    app = FastAPI(title="QueryDoctor API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.bus = bus
    app.state.pipeline_deps = deps
    app.state.benchmark_deps = bench_deps
    app.state.workload_deps = wl_deps

    def run_inline(analysis_id: int) -> None:
        with app.state.sessionmaker() as session:
            run_analysis_pipeline(session, analysis_id, deps)

    def run_in_worker(analysis_id: int) -> None:
        from app.workers.tasks import run_analysis

        run_analysis.delay(analysis_id)

    def run_benchmark_inline(run_id: int) -> None:
        with app.state.sessionmaker() as session:
            run_benchmark_job(session, run_id, bench_deps)

    def run_benchmark_in_worker(run_id: int) -> None:
        from app.workers.tasks import run_benchmark

        run_benchmark.delay(run_id)

    app.state.enqueue = enqueue or (run_inline if in_process else run_in_worker)
    app.state.enqueue_benchmark = enqueue_benchmark or (run_benchmark_inline if in_process else run_benchmark_in_worker)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    for router in (
        projects.router, analyses.router, analyses.project_router, benchmarks.router, samples.router, connections.router,
        workload.router, model.router,
    ):
        app.include_router(router)
    return app


def factory() -> FastAPI:
    """Entry point for uvicorn: ``uvicorn app.main:factory --factory``."""
    return create_app()
