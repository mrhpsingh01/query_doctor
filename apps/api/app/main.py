import socket
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from querydoctor.explain import OllamaExplainer
from querydoctor.ranking.predictor import Predictor
from sqlalchemy import select

from app.api import analyses, benchmarks, connections, explanations, model, privacy, projects, samples, workload
from app.config import Settings, get_settings
from app.database import make_engine, make_sessionmaker
from app.logging_utils import install_redaction
from app.models import Base, Project
from app.security.auth import member, path_project, resource_path, subject_for, validate_tokens
from app.services.events import EventBus, get_event_bus
from app.services.connections import dsn_builder, mongo_builder, source_dsn, source_mongo
from app.services.explanations import ExplainDeps, run_explanation_job
from app.services.benchmarks import BenchmarkDeps, run_benchmark_job
from app.services.pipeline import PipelineDeps, run_analysis_pipeline
from app.services.workload import WorkloadDeps, run_demo_traffic, run_mongo_demo_traffic

SAMPLE_PROJECT_NAME = "Sample shop"


def ensure_sample_project(sessionmaker) -> None:
    with sessionmaker() as session:
        project = session.scalar(select(Project).where(Project.name == SAMPLE_PROJECT_NAME))
        if project is None:
            project = Project(name=SAMPLE_PROJECT_NAME, description="QueryDoctor's prepared e-commerce database")
            session.add(project)
            session.flush()
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
    explain_deps: ExplainDeps | None = None,
    connection_resolver: Callable | None = None,
    enqueue_explanation: Callable[[int], None] | None = None,
) -> FastAPI:
    """Build the API. ``event_bus`` / ``pipeline_deps`` / ``enqueue`` exist so tests can run everything in-process;
    in deployment the defaults hand analyses to the Celery worker over Redis."""
    settings = settings or get_settings()
    validate_tokens(settings.auth_tokens)
    if settings.allow_custom_connections and not settings.auto_create_tables:
        if not settings.auth_tokens or not settings.secret_key:
            raise ValueError("Customer connections require QD_AUTH_TOKENS and QD_SECRET_KEY")
    install_redaction()  # no query literal or parameter value reaches any log line
    bus = event_bus or get_event_bus(settings.redis_url)
    deps = pipeline_deps or PipelineDeps(
        target_dsn=settings.target_dsn,
        bus=bus,
        max_plan_bytes=settings.max_plan_bytes,
        thresholds=settings.diagnosis_thresholds(),
        predictor=Predictor.load(settings.model_dir),
        mongo_target_uri=settings.mongo_target_uri,
    )

    bench_deps = benchmark_deps or BenchmarkDeps(bench_dsn=settings.bench_dsn, bus=bus, mongo_bench_uri=settings.mongo_bench_uri)

    in_process = settings.redis_url.startswith("memory://")

    def demo_in_worker(mode: str) -> None:
        from app.workers.tasks import run_demo_workload

        run_demo_workload.delay(mode)

    def demo_in_thread(mode: str) -> None:
        threading.Thread(target=run_demo_traffic, args=(settings.target_dsn, settings.scale, mode), daemon=True).start()

    def mongo_demo_in_worker(mode: str) -> None:
        from app.workers.tasks import run_demo_workload

        run_demo_workload.delay(mode, "mongodb")

    def mongo_demo_in_thread(mode: str) -> None:
        threading.Thread(target=run_mongo_demo_traffic, args=(settings.mongo_target_uri, settings.scale, mode), daemon=True).start()

    wl_deps = workload_deps or WorkloadDeps(
        target_dsn=settings.target_dsn, demo_runner=demo_in_thread if in_process else demo_in_worker,
        mongo_uri=settings.mongo_target_uri, mongo_demo_runner=mongo_demo_in_thread if in_process else mongo_demo_in_worker,
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

    @app.middleware("http")
    async def customer_access(request, call_next):
        tokens = settings.auth_tokens
        if tokens and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            subject = subject_for(request, tokens)
            if subject is None:
                return JSONResponse(status_code=401, content={"detail": "authentication required"})
            request.state.subject = subject
            path = request.url.path
            if resource_path(path):
                with app.state.sessionmaker() as session:
                    project_id = path_project(session, path)
                    if project_id is None or not member(session, project_id, subject):
                        return JSONResponse(status_code=404, content={"detail": "not found"})
            if path == "/api/model/monitor":
                return JSONResponse(status_code=404, content={"detail": "not found"})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc: RequestValidationError):
        # FastAPI's default body echoes the submitted value of each failing field (and, for a missing field, the whole
        # body): that would send a password or a query back to the caller. Say what to fix, never what was sent.
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

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

    resolver = connection_resolver or socket.getaddrinfo
    app_state_resolver = resolver
    if deps.connection_dsn is None:
        deps.connection_dsn = dsn_builder(settings, resolver)
    if deps.connection_mongo is None:
        deps.connection_mongo = mongo_builder(settings, resolver)
    if wl_deps.connection_dsn is None:
        wl_deps.connection_dsn = lambda session, row: source_dsn(settings, resolver, session, row)
    if wl_deps.connection_mongo is None:
        wl_deps.connection_mongo = lambda session, row: source_mongo(settings, resolver, session, row)

    ex_deps = explain_deps or ExplainDeps(
        ollama=OllamaExplainer(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s) if settings.ollama_model else None
    )
    app.state.explain_deps = ex_deps
    app.state.connection_resolver = app_state_resolver

    def run_explanation_inline(explanation_id: int) -> None:
        with app.state.sessionmaker() as session:
            run_explanation_job(session, explanation_id, ex_deps)

    def run_explanation_in_worker(explanation_id: int) -> None:
        from app.workers.tasks import run_explanation

        run_explanation.delay(explanation_id)

    # the template explainer is instant, so only a language-model draft needs the worker
    use_inline = in_process or not ex_deps.uses_model
    app.state.enqueue_explanation = enqueue_explanation or (run_explanation_inline if use_inline else run_explanation_in_worker)
    app.state.enqueue = enqueue or (run_inline if in_process else run_in_worker)
    app.state.enqueue_benchmark = enqueue_benchmark or (run_benchmark_inline if in_process else run_benchmark_in_worker)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    for router in (
        projects.router, analyses.router, analyses.project_router, benchmarks.router, samples.router, connections.router,
        workload.router, model.router, explanations.router, privacy.router, connections.project_router,
    ):
        app.include_router(router)
    return app


def factory() -> FastAPI:
    """Entry point for uvicorn: ``uvicorn app.main:factory --factory``."""
    return create_app()
