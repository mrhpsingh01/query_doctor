from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from app.api import projects
from app.config import Settings, get_settings
from app.database import make_engine, make_sessionmaker
from app.models import Base, Project

SAMPLE_PROJECT_NAME = "Sample shop"


def ensure_sample_project(sessionmaker) -> None:
    with sessionmaker() as session:
        if session.scalar(select(Project).where(Project.name == SAMPLE_PROJECT_NAME)) is None:
            session.add(Project(name=SAMPLE_PROJECT_NAME, description="QueryDoctor's prepared e-commerce database"))
            session.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

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

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.include_router(projects.router)
    return app


def factory() -> FastAPI:
    """Entry point for uvicorn: ``uvicorn app.main:factory --factory``."""
    return create_app()
