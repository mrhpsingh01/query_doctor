import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from stubs import StubTarget

from app.config import Settings
from app.main import create_app
from app.models import Base
from app.services.events import InMemoryEventBus

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite://", secret_key=TEST_KEY, redis_url="memory://", auto_create_tables=True)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def stub() -> StubTarget:
    return StubTarget()


@pytest.fixture
def client(settings: Settings, bus: InMemoryEventBus, stub: StubTarget) -> TestClient:
    """API client wired to an in-memory bus and a stubbed target; background jobs run inline."""
    app = create_app(settings, event_bus=bus, pipeline_deps=stub.deps(bus))
    with TestClient(app) as c:
        yield c
