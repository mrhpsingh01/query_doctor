"""Custom connections: guard the host, store the password encrypted, and build the DSN a job connects with.

Every use re-resolves the host through the SSRF guard and connects to the *checked IP address* (``hostaddr``) while keeping
the name for TLS verification, so a DNS answer that changes after registration cannot redirect a connection.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException
from psycopg.conninfo import make_conninfo
from pymongo import MongoClient
from querydoctor.netguard import GuardConfig, NetworkBlocked, resolve_and_check
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Analysis, AuditEvent, Connection
from app.security.crypto import decrypt

FLAG_OFF = "Custom connections are turned off. Set QD_ALLOW_CUSTOM_CONNECTIONS=true to enable them."
CONNECT_TIMEOUT_S = 5


def require_enabled(settings: Settings) -> None:
    if not settings.allow_custom_connections:
        raise HTTPException(403, detail=FLAG_OFF)


def guard(settings: Settings, host: str, port: int, resolver: Callable = socket.getaddrinfo, *, engine: str = "postgres"):
    """The checked target for ``host:port`` or a 400 that names the reason class and nothing else."""
    try:
        ports = frozenset({27017}) if engine == "mongodb" else frozenset({5432})
        return resolve_and_check(host, port, GuardConfig(allow_private=settings.allow_private_networks, allowed_ports=ports), resolver=resolver)
    except NetworkBlocked as exc:
        raise HTTPException(400, detail=f"That host or port is not allowed: {exc.reason}") from exc


def live_connection(session: Session, connection_id: int, project_id: int) -> Connection:
    row = session.get(Connection, connection_id)
    if row is None or row.deleted_at is not None or row.project_id != project_id:
        raise HTTPException(404, detail="connection not found")
    return row


def wipe(session: Session, row: Connection) -> None:
    """Delete the secret: the ciphertext is overwritten, not just hidden."""
    row.encrypted_password = ""
    row.deleted_at = datetime.now(UTC)
    session.add(AuditEvent(event_type="connection_deleted", detail={"connection_id": row.id}))


def build_dsn(settings: Settings, resolver: Callable, session: Session, analysis: Analysis) -> str:
    """The DSN for an analysis of a custom connection (guard re-run now; connects to the checked IP)."""
    row = session.get(Connection, analysis.connection_id)
    if row is None or row.deleted_at is not None or not row.encrypted_password or row.project_id != analysis.project_id:
        raise ValueError("the connection used by this analysis no longer exists")
    if row.engine != "postgres":
        raise ValueError("the selected connection is not PostgreSQL")
    target = guard(settings, row.host, row.port, resolver, engine="postgres")
    return make_conninfo(
        host=target.host, hostaddr=target.ip, port=target.port, dbname=row.database, user=row.username,
        password=decrypt(row.encrypted_password, key=settings.secret_key or None), sslmode=row.sslmode,
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def dsn_builder(settings: Settings, resolver: Callable = socket.getaddrinfo) -> Callable[[Session, Analysis], str]:
    return lambda session, analysis: build_dsn(settings, resolver, session, analysis)


@contextmanager
def open_mongo(settings: Settings, resolver: Callable, session: Session, analysis: Analysis):
    """Use the checked IP directly. Customer certificates must include that IP in their SAN."""
    row = session.get(Connection, analysis.connection_id)
    if row is None or row.deleted_at is not None or not row.encrypted_password or row.engine != "mongodb" or row.project_id != analysis.project_id:
        raise ValueError("the connection used by this analysis no longer exists")
    target = guard(settings, row.host, row.port, resolver, engine="mongodb")
    client = MongoClient(
        host=target.ip, port=target.port, username=row.username,
        password=decrypt(row.encrypted_password, key=settings.secret_key or None),
        authSource=row.auth_database or row.database, tls=row.tls, directConnection=True,
        serverSelectionTimeoutMS=CONNECT_TIMEOUT_S * 1000,
    )
    try:
        yield client[row.database]
    finally:
        client.close()


def mongo_builder(settings: Settings, resolver: Callable = socket.getaddrinfo):
    return lambda session, analysis: open_mongo(settings, resolver, session, analysis)


def source_dsn(settings: Settings, resolver: Callable, session: Session, row: Connection) -> str:
    return build_dsn(settings, resolver, session, SimpleNamespace(connection_id=row.id, project_id=row.project_id))


def source_mongo(settings: Settings, resolver: Callable, session: Session, row: Connection):
    return open_mongo(settings, resolver, session, SimpleNamespace(connection_id=row.id, project_id=row.project_id))
