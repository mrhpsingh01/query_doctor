"""Bearer authentication and project ownership for customer deployments.

An empty token map keeps the local sample installation in demo mode. A shared
deployment supplies subject -> token pairs through QD_AUTH_TOKENS. Resource
lookups return the same 404 for unknown and unauthorized identifiers.
"""

from __future__ import annotations

import hmac
import re

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, BenchmarkRun, Connection, Explanation, Project, ProjectMember

_RESOURCE = re.compile(r"^/api/(projects|analyses|benchmarks|connections|explanations)/(\d+)(?:/|$)")


def validate_tokens(tokens: dict[str, str]) -> None:
    if any(not subject or len(token) < 32 for subject, token in tokens.items()):
        raise ValueError("QD_AUTH_TOKENS requires nonempty subjects and tokens of at least 32 characters")
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("QD_AUTH_TOKENS must not reuse a token")


def subject_for(request: Request, tokens: dict[str, str]) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    supplied = header[7:]
    for subject, token in tokens.items():
        if hmac.compare_digest(supplied, token):
            return subject
    return None


def member(session: Session, project_id: int, subject: str) -> bool:
    return session.scalar(
        select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.subject == subject)
    ) is not None


def require_project(session: Session, request: Request, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or (request.app.state.settings.auth_tokens and not member(session, project_id, request.state.subject)):
        raise HTTPException(404, detail="project not found")
    return project


def resource_project(session: Session, kind: str, row_id: int) -> int | None:
    if kind == "projects":
        return row_id if session.get(Project, row_id) is not None else None
    if kind == "analyses":
        row = session.get(Analysis, row_id)
        return row.project_id if row else None
    if kind == "connections":
        row = session.get(Connection, row_id)
        return row.project_id if row and row.deleted_at is None else None
    if kind == "benchmarks":
        row = session.get(BenchmarkRun, row_id)
        analysis = session.get(Analysis, row.analysis_id) if row else None
        return analysis.project_id if analysis else None
    if kind == "explanations":
        row = session.get(Explanation, row_id)
        analysis = session.get(Analysis, row.analysis_id) if row else None
        return analysis.project_id if analysis else None
    return None


def path_project(session: Session, path: str) -> int | None:
    match = _RESOURCE.match(path)
    if match is None:
        return None
    return resource_project(session, match.group(1), int(match.group(2)))


def resource_path(path: str) -> bool:
    return _RESOURCE.match(path) is not None
