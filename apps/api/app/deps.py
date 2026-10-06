from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings
