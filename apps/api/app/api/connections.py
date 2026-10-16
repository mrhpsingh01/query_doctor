from __future__ import annotations

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import AuditEvent, Connection, Project
from app.security.crypto import encrypt
from app.security.network import connection_allowed
from app.services import connections as svc

router = APIRouter(prefix="/api/connections", tags=["connections"])
project_router = APIRouter(prefix="/api/projects/{project_id}/connections", tags=["connections"])


class ConnectionTest(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    database: str
    username: str
    password: str = Field(repr=False)


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    engine: Literal["postgres", "mongodb"] = "postgres"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000, repr=False)
    sslmode: Literal["prefer", "require"] = "prefer"
    tls: bool = True  # MongoDB only; TLS certificate must cover the checked IP address
    auth_database: str | None = Field(default=None, max_length=200)


class ConnectionOut(BaseModel):
    """Never carries the password or its ciphertext."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    kind: str
    engine: str
    host: str
    port: int
    database: str
    username: str
    sslmode: str
    tls: bool
    auth_database: str | None


def try_connect(*, host: str, port: int, database: str, username: str, password: str) -> tuple[bool, str]:
    """Open and close a connection. The message never echoes credentials."""
    try:
        with psycopg.connect(host=host, port=port, dbname=database, user=username, password=password, connect_timeout=3):
            return True, "ok"
    except psycopg.Error:
        return False, "could not connect with the supplied details"


@router.post("/test")
def test_connection(body: ConnectionTest, request: Request):
    if request.app.state.settings.auth_tokens:
        raise HTTPException(403, detail="Use a registered project connection for customer sources.")
    if not connection_allowed(body.host, body.port):
        raise HTTPException(400, detail="This release only connects to QueryDoctor's sample databases.")
    tester = getattr(request.app.state, "connection_tester", try_connect)
    ok, message = tester(
        host=body.host, port=body.port, database=body.database, username=body.username, password=body.password
    )
    return {"ok": ok, "message": message}


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, detail="project not found")
    return project


@project_router.post("", status_code=201, response_model=ConnectionOut)
def create_connection(project_id: int, body: ConnectionCreate, request: Request, session: Session = Depends(get_session)):
    state = request.app.state
    svc.require_enabled(state.settings)
    _project(session, project_id)
    svc.guard(state.settings, body.host, body.port, state.connection_resolver, engine=body.engine)
    row = Connection(
        project_id=project_id, name=body.name, kind="custom", engine=body.engine,
        host=body.host.strip().lower(), port=body.port, database=body.database,
        username=body.username, encrypted_password=encrypt(body.password, key=state.settings.secret_key or None),
        sslmode=body.sslmode, tls=body.tls, auth_database=body.auth_database,
    )
    session.add(row)
    session.flush()
    session.add(AuditEvent(event_type="connection_created", detail={"connection_id": row.id, "project_id": project_id, "host": row.host}))
    session.commit()
    return row


@project_router.get("", response_model=list[ConnectionOut])
def list_connections(project_id: int, request: Request, session: Session = Depends(get_session)):
    svc.require_enabled(request.app.state.settings)
    _project(session, project_id)
    stmt = select(Connection).where(Connection.project_id == project_id, Connection.deleted_at.is_(None), Connection.kind == "custom").order_by(Connection.id)
    return list(session.scalars(stmt))


@router.delete("/{connection_id}")
def delete_connection(connection_id: int, session: Session = Depends(get_session)):
    """Delete a connection's credentials. Always allowed, even when custom connections are switched off."""
    row = session.get(Connection, connection_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, detail="connection not found")
    svc.wipe(session, row)
    session.commit()
    return {"deleted": connection_id}
