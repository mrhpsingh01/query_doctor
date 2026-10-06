from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # In-memory SQLite (tests) must share one connection across threads.
        return create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False}, hide_parameters=True)
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)  # statement parameters never appear in errors or logs


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
